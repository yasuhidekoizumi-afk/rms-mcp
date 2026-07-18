"""セール価格 自動設定/復元 パイプライン.

お買い物マラソン・スーパーSALE等のイベント前後で、
商品価格を一括変更し、終了後に自動で元の価格に戻す。

処理フロー:
1. セール開始: ItemAPI PATCH で全商品の価格を変更
2. セール終了: 保存した元価格に自動復元
3. 価格履歴をJSONファイルに記録（景表法対応のエビデンス）

実行方法:
    # セール開始（価格変更）
    python -m rms_mcp.pipelines.sale_pricing start \
        --sale-rate 0.85 \
        --target all

    # セール終了（価格復元）
    python -m rms_mcp.pipelines.sale_pricing restore

環境変数:
    RMS_SERVICE_SECRET=SP_xxx
    RMS_LICENSE_KEY=SL_xxx

GHA cron例:
    セール開始時刻に start、終了時刻に restore を実行
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from rms_mcp.client import RMSClient
from rms_mcp.item_api import ItemAPI

JST = ZoneInfo("Asia/Tokyo")

# 価格履歴の保存先
PRICE_HISTORY_PATH = Path(__file__).parent / "price_history.json"

# セール価格の安全設定
MAX_DISCOUNT_RATE = 0.50  # 最大50%OFF
MIN_PRICE = 100  # 最低価格（¥100）


def _save_price_snapshot(items: list[dict]):
    """セール前の価格をスナップショットとして保存."""
    snapshot = {
        "saved_at": datetime.now(JST).isoformat(),
        "items": items,
    }
    PRICE_HISTORY_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    print(f"[sale_pricing] 価格スナップショット保存: {len(items)}商品 → {PRICE_HISTORY_PATH}")


def _load_price_snapshot() -> dict | None:
    """保存した価格スナップショットを読み込み."""
    if not PRICE_HISTORY_PATH.exists():
        return None
    return json.loads(PRICE_HISTORY_PATH.read_text())


def _get_all_prices(api: ItemAPI) -> list[dict]:
    """全商品の現在価格を取得.

    Returns: [{manageNumber, variantId, standardPrice, title}]
    """
    all_items = api.search_all()
    prices = []
    for row in all_items:
        item = row.get("item", {})
        mn = item.get("manageNumber", "")
        title = item.get("title", "")[:40]
        variants = item.get("variants", {})
        for vkey, vdata in variants.items():
            price_str = vdata.get("standardPrice", "")
            if price_str:
                try:
                    price = int(price_str)
                except ValueError:
                    continue
                prices.append({
                    "manageNumber": mn,
                    "variantId": vkey,
                    "standardPrice": price,
                    "title": title,
                })
    return prices


def start_sale(rms_secret: str, rms_license: str,
               sale_rate: float = 0.85,
               target_manage_numbers: list[str] | None = None,
               dry_run: bool = False) -> dict:
    """セール価格を一括設定.

    sale_rate: セール価格の掛け率（0.85 = 15%OFF）
    target_manage_numbers: 対象商品の管理番号リスト（省略時=全商品）

    Returns: {processed, updated, skipped, errors}
    """
    if sale_rate < (1.0 - MAX_DISCOUNT_RATE):
        return {"error": f"割引率が上限({MAX_DISCOUNT_RATE:.0%})を超えています"}

    c = RMSClient(rms_secret, rms_license)
    api = ItemAPI(c)

    print(f"[sale_pricing] 全商品の価格を取得中...")
    all_prices = _get_all_prices(api)
    print(f"[sale_pricing] {len(all_prices)} SKU取得")

    # 対象絞り込み
    if target_manage_numbers:
        all_prices = [p for p in all_prices if p["manageNumber"] in target_manage_numbers]
        print(f"[sale_pricing] 対象絞り込み: {len(all_prices)} SKU")

    # セール前価格を保存（復元用）
    _save_price_snapshot(all_prices)

    # セール価格を計算
    updates = []
    skipped = []
    for p in all_prices:
        original = p["standardPrice"]
        sale_price = max(MIN_PRICE, int(original * sale_rate / 10) * 10)  # 10円単位に丸める

        if sale_price >= original:
            skipped.append(f"{p['manageNumber']}:{p['variantId']} (割引後≧元価格)")
            continue

        updates.append({
            "manageNumber": p["manageNumber"],
            "variantId": p["variantId"],
            "originalPrice": original,
            "salePrice": sale_price,
            "title": p["title"],
        })

    print(f"[sale_pricing] セール設定: {len(updates)}件 / スキップ: {len(skipped)}件")

    if dry_run:
        c.close()
        return {
            "processed": len(updates),
            "dry_run": True,
            "preview": [{"sku": f"{u['manageNumber']}:{u['variantId']}",
                         "original": u["originalPrice"], "sale": u["salePrice"],
                         "discount": f"{(1-u['salePrice']/u['originalPrice'])*100:.0f}%"} for u in updates[:10]],
        }

    # 価格変更を実行
    success = 0
    failed = 0
    errors = []

    for u in updates:
        try:
            result = api.patch(u["manageNumber"], {
                "variants": {u["variantId"]: {"standardPrice": str(u["salePrice"])}}
            })
            success += 1
            print(f"  ✅ {u['manageNumber']}:{u['variantId']} ¥{u['originalPrice']}→¥{u['salePrice']}")
        except Exception as e:
            failed += 1
            errors.append({"sku": f"{u['manageNumber']}:{u['variantId']}", "error": str(e)[:200]})
            print(f"  ❌ {u['manageNumber']}:{u['variantId']} {str(e)[:60]}")

        time.sleep(1.0)  # QPS制限対策（ItemAPIは1req/sec程度）

    c.close()

    result = {
        "processed": len(updates),
        "success": success,
        "failed": failed,
        "skipped": len(skipped),
        "sale_rate": sale_rate,
        "snapshot_path": str(PRICE_HISTORY_PATH),
        "errors": errors[:10],
    }
    print(f"[sale_pricing] 完了: 成功{success} / 失敗{failed} / スキップ{len(skipped)}")
    return result


def restore_prices(rms_secret: str, rms_license: str,
                   dry_run: bool = False) -> dict:
    """セール終了後に元の価格に復元.

    Returns: {processed, restored, errors}
    """
    snapshot = _load_price_snapshot()
    if not snapshot:
        return {"error": "価格スナップショットが見つかりません。start_saleを先に実行してください。"}

    saved_at = snapshot.get("saved_at", "?")
    items = snapshot.get("items", [])
    print(f"[sale_pricing] スナップショット読み込み: {saved_at} ({len(items)} SKU)")

    if dry_run:
        return {
            "processed": len(items),
            "dry_run": True,
            "snapshot_saved_at": saved_at,
        }

    c = RMSClient(rms_secret, rms_license)
    api = ItemAPI(c)

    success = 0
    failed = 0
    errors = []

    for p in items:
        mn = p["manageNumber"]
        vid = p["variantId"]
        original_price = p["standardPrice"]

        try:
            result = api.patch(mn, {"variants": {vid: {"standardPrice": str(original_price)}}})
            success += 1
            print(f"  ✅ {mn}:{vid} → ¥{original_price}")
        except Exception as e:
            failed += 1
            errors.append({"sku": f"{mn}:{vid}", "error": str(e)[:200]})
            print(f"  ❌ {mn}:{vid} {str(e)[:60]}")

        time.sleep(0.5)

    c.close()

    # スナップショットを削除（復元完了）
    if failed == 0:
        PRICE_HISTORY_PATH.unlink()
        print(f"[sale_pricing] スナップショット削除（復元完了）")

    result = {
        "processed": len(items),
        "success": success,
        "failed": failed,
        "errors": errors[:10],
    }
    print(f"[sale_pricing] 復元完了: 成功{success} / 失敗{failed}")
    return result


if __name__ == "__main__":
    rms_ss = os.environ.get("RMS_SERVICE_SECRET", "")
    rms_lk = os.environ.get("RMS_LICENSE_KEY", "")

    if not all([rms_ss, rms_lk]):
        print("ERROR: RMS_SERVICE_SECRET and RMS_LICENSE_KEY required")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("Usage: python -m rms_mcp.pipelines.sale_pricing [start|restore] [--dry-run]")
        print("  start --sale-rate 0.85  : 15%OFFでセール開始")
        print("  restore                  : 元の価格に復元")
        sys.exit(0)

    if args[0] == "start":
        sale_rate = 0.85  # デフォルト15%OFF
        for i, a in enumerate(args):
            if a == "--sale-rate" and i + 1 < len(args):
                sale_rate = float(args[i + 1])

        result = start_sale(rms_ss, rms_lk, sale_rate=sale_rate, dry_run=dry_run)
    elif args[0] == "restore":
        result = restore_prices(rms_ss, rms_lk, dry_run=dry_run)
    else:
        print(f"Unknown command: {args[0]}")
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))
