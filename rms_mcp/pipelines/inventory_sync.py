"""Logiless → RMS 在庫同期パイプライン.

Logilessの在庫変動を検知し、RMSの在庫に反映する。

処理フロー:
1. Logiless API で在庫一覧を取得
2. RMS商品のバリアントSKUと突き合わせ
3. RMS InventoryAPI で在庫数を更新

注意:
- InventoryAPI がRMS側で契約・有効化されている必要がある
- オーバーセル防止のため、バッファ在庫（1-2個）を設定可能
- 1時間ごとのリコンサイル（整合性確認）を推奨

実行方法:
    python -m rms_mcp.pipelines.inventory_sync

    # 環境変数
    RMS_SERVICE_SECRET=SP_xxx
    RMS_LICENSE_KEY=SL_xxx
    LOGILESS_API_KEY=xxx
"""
import json
import os
import sys
import time
from typing import Any

import httpx

from rms_mcp.client import RMSClient
from rms_mcp.item_api import ItemAPI
from rms_mcp.inventory_api import InventoryAPI
from rms_mcp.pipelines.logiless_client import LogilessClient

JST_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%S+0900"
LOGILESS_BASE = "https://api.logiless.com/v1"

# 在庫バッファ（オーバーセル防止用。楽天表示を実際より少なめにする）
DEFAULT_BUFFER = 2


def _logiless_get_inventory(api_key: str, page: int = 1, per_page: int = 100) -> dict:
    """Logiless API で在庫一覧を取得."""
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"page": page, "per_page": per_page}
    r = httpx.get(
        f"{LOGILESS_BASE}/inventory",
        headers=headers,
        params=params,
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


def _logiless_get_all_inventory(client: LogilessClient) -> dict[str, int]:
    """全ページ取得して {article_code: stock} のdictを返す."""
    all_stock: dict[str, int] = {}
    page = 1
    while True:
        r = client.get("/inventory", params={"page": page, "per_page": 100})
        r.raise_for_status()
        data = r.json()
        items = data.get("data", [])
        for item in items:
            # 商品（attr6=商品/単品）のみ対象。資材・同梱物はスキップ
            article = item.get("article", {})
            attr6 = article.get("attr6", "")
            if attr6 not in ("商品", "単品"):
                continue
            code = article.get("code", "")
            stock = item.get("available", item.get("free", 0))
            if code:
                all_stock[code] = int(stock) if stock else 0

        total_pages = data.get("meta", {}).get("total_pages", 1)
        if page >= total_pages or not items:
            break
        page += 1
        time.sleep(0.5)

    return all_stock


def _build_inventory_map(rms_secret: str, rms_license: str) -> dict[str, dict]:
    """RMS商品から {merchantDefinedSkuId: {itemUrl, variantId}} のマップを構築.

    ItemAPI search で全商品を取得し、各バリアントのSKUを抽出する。
    """
    c = RMSClient(rms_secret, rms_license)
    api = ItemAPI(c)
    all_items = api.search_all()
    c.close()

    sku_map: dict[str, dict] = {}
    for row in all_items:
        item = row.get("item", {})
        item_url = item.get("manageNumber", "")
        variants = item.get("variants", {})
        for vkey, vdata in variants.items():
            sku = vdata.get("merchantDefinedSkuId", "")
            if sku:
                sku_map[sku] = {
                    "itemUrl": item_url,
                    "variantId": vkey,
                    "standardPrice": vdata.get("standardPrice", ""),
                }

    return sku_map


def sync_inventory(rms_secret: str, rms_license: str,
                  logiless_client: LogilessClient | None = None,
                  buffer: int = DEFAULT_BUFFER,
                  dry_run: bool = False) -> dict:
    """在庫同期のメイン処理.

    Returns: {matched, updated, skipped, errors}
    """
    print("[inventory_sync] Logilessの在庫データを取得中...")

    # 1. Logiless在庫を取得
    try:
        if not logiless_client:
            logiless_client = LogilessClient()
        logiless_stock = _logiless_get_all_inventory(logiless_client)
    except Exception as e:
        return {"error": f"Logiless API error: {e}", "matched": 0}

    print(f"[inventory_sync] Logiless: {len(logiless_stock)} SKU取得")

    # 2. RMS商品SKUマップを構築
    print("[inventory_sync] RMS商品SKUマップを構築中...")
    rms_sku_map = _build_inventory_map(rms_secret, rms_license)
    print(f"[inventory_sync] RMS: {len(rms_sku_map)} SKU取得")

    # 3. 突き合わせ
    updates: list[dict] = []
    unmatched_logiless: list[str] = []
    unmatched_rms: list[str] = []

    # 手動マッピングテーブルを読み込み
    import pathlib
    mapping_file = pathlib.Path(__file__).parent / "sku_mapping.json"
    manual_map: dict[str, dict] = {}
    if mapping_file.exists():
        import json as _json
        manual_map = _json.loads(mapping_file.read_text()).get("mappings", {})

    for sku, stock in logiless_stock.items():
        rms_info = None
        # 1. 自動マッチ（merchantDefinedSkuId == Logiless article.code）
        if sku in rms_sku_map:
            rms_info = rms_sku_map[sku]
        # 2. 手動マッピングテーブル
        elif sku in manual_map:
            rms_info = manual_map[sku]

        if rms_info:
            rms_qty = max(0, stock - buffer)
            updates.append({
                "itemUrl": rms_info["manageNumber"],
                "variantId": rms_info["variantId"],
                "quantity": rms_qty,
                "logiless_stock": stock,
                "sku": sku,
            })
        else:
            unmatched_logiless.append(sku)

    for sku in rms_sku_map:
        if sku not in logiless_stock:
            unmatched_rms.append(sku)

    print(f"[inventory_sync] マッチ: {len(updates)}件 / Logiless未マッチ: {len(unmatched_logiless)} / RMS未マッチ: {len(unmatched_rms)}")

    if not updates:
        return {
            "matched": 0,
            "logiless_total": len(logiless_stock),
            "rms_total": len(rms_sku_map),
            "unmatched_logiless": unmatched_logiless[:10],
            "unmatched_rms": unmatched_rms[:10],
        }

    if dry_run:
        return {
            "matched": len(updates),
            "dry_run": True,
            "updates_preview": [
                {"sku": u["sku"], "logiless": u["logiless_stock"], "rms": u["quantity"]}
                for u in updates[:10]
            ],
        }

    # 4. RMS在庫を更新
    c = RMSClient(rms_secret, rms_license)
    inv_api = InventoryAPI(c)

    print(f"[inventory_sync] RMS InventoryAPIで{len(updates)}件を更新中...")

    # 新しいAPI形式: {manageNumber, variantId, mode, quantity}
    inventory_list = []
    for u in updates:
        inventory_list.append({
            "manageNumber": u["itemUrl"],  # itemUrl = manageNumber
            "variantId": u["variantId"],
            "mode": "ABSOLUTE",
            "quantity": u["quantity"],
        })

    try:
        result = inv_api.bulk_upsert(inventory_list)
        print(f"[inventory_sync] 完了: {len(updates)} SKU更新")
        c.close()
        return {
            "matched": len(updates),
            "updated": len(updates),
            "result": result,
            "unmatched_logiless": unmatched_logiless[:10],
            "unmatched_rms": unmatched_rms[:10],
        }
    except RuntimeError as e:
        c.close()
        return {
            "matched": len(updates),
            "updated": 0,
            "error": str(e)[:300],
        }


if __name__ == "__main__":
    rms_ss = os.environ.get("RMS_SERVICE_SECRET", "")
    rms_lk = os.environ.get("RMS_LICENSE_KEY", "")

    if not all([rms_ss, rms_lk]):
        print("ERROR: RMS_SERVICE_SECRET and RMS_LICENSE_KEY required")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    result = sync_inventory(rms_ss, rms_lk, dry_run=dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
