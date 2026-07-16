"""Logiless → RMS 出荷同期パイプライン.

Logilessで出荷完了した注文の追跡番号を、RMSの配送情報に反映する。

処理フロー:
1. Logiless API で Shipped ステータスの出荷を取得
2. 各出荷の sales_order.code (= RMS注文番号) と追跡番号を抽出
3. RMS updateOrderShipping で配送情報を登録
4. （オプション）RMS updateOrderSubStatus でサブステータスを更新

実行方法:
    python -m rms_mcp.pipelines.shipping_sync

    # 環境変数（両方必要）
    RMS_SERVICE_SECRET=SP_xxx
    RMS_LICENSE_KEY=SL_xxx
    LOGILESS_API_KEY=xxx   # Logiless REST APIキー

GHA cron例:
    毎日 JST 8:00, 12:00, 16:00, 20:00 に実行（出荷締め後に反映）
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from rms_mcp.client import RMSClient
from rms_mcp.order_api import OrderAPI
from rms_mcp.pipelines.logiless_client import LogilessClient

JST = ZoneInfo("Asia/Tokyo")
LOGILESS_BASE = "https://api.logiless.com/v1"

# Logiless配送方法 → RMS配送業者ID のマッピング
# ※ RMS配送業者IDは店舗設定によって異なる。要実環境確認。
# RMS管理画面: RMS > 店舗設定 > 基本設定 > 配送業者設定 で確認可能
DELIVERY_METHOD_MAP: dict[str, dict] = {
    "yu_packet_3cm": {"shippingCompanyId": 103, "name": "ゆうパケット"},
    "yu_packet_5cm": {"shippingCompanyId": 103, "name": "ゆうパケット"},
    "yu_pack": {"shippingCompanyId": 102, "name": "ゆうパック"},
    "yamato": {"shippingCompanyId": 100, "name": "ヤマト運輸"},
    "sagawa": {"shippingCompanyId": 101, "name": "佐川急便"},
    "yamato_kuroneko": {"shippingCompanyId": 100, "name": "ヤマト運輸（クロネコDM）"},
}


def _logiless_get_shipped(client: LogilessClient, date_from: str, date_to: str) -> list[dict]:
    """Logiless API で Shipped ステータスの出荷を取得."""
    params = {
        "date_from": date_from,
        "date_to": date_to,
        "delivery_status": "Shipped",
        "per_page": 100,
    }
    all_shipments: list[dict] = []
    page = 1
    while True:
        params["page"] = page
        r = client.get("/shipments", params=params)
        r.raise_for_status()
        data = r.json()
        shipments = data.get("data", [])
        all_shipments.extend(shipments)
        total_pages = data.get("meta", {}).get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)  # rate limit

    # 追跡番号があるものだけフィルタ
    return [s for s in all_shipments if s.get("delivery_tracking_numbers")]


def _build_shipping_payload(shipment: dict) -> dict | None:
    """Logiless出荷データからRMS updateOrderShipping用payloadを構築."""
    sales_order = shipment.get("sales_order", {})
    order_number = sales_order.get("code")
    if not order_number:
        return None

    tracking_numbers = shipment.get("delivery_tracking_numbers", [])
    if not tracking_numbers:
        return None

    delivery_method = shipment.get("delivery_method", "")
    company_map = DELIVERY_METHOD_MAP.get(delivery_method, {})
    shipping_company_id = company_map.get("shippingCompanyId", 102)  # default: ゆうパック

    # shippingId は通常1から始まる（1注文1梱包の場合）
    shipping_list = []
    for idx, tracking_no in enumerate(tracking_numbers, 1):
        shipping_list.append({
            "shippingId": idx,
            "shippingCompanyId": shipping_company_id,
            "shippingNumber": tracking_no,
        })

    return {
        "orderNumber": order_number,
        "shippingList": shipping_list,
    }


def sync_shipping(rms_secret: str, rms_license: str,
                  logiless_client: LogilessClient | None = None,
                  date_from: str | None = None, date_to: str | None = None,
                  dry_run: bool = False) -> dict:
    """出荷同期のメイン処理.

    Returns: {processed, success, failed, skipped, errors}
    """
    now = datetime.now(JST)
    if not date_from:
        date_from = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    if not date_to:
        date_to = now.strftime("%Y-%m-%d")

    print(f"[shipping_sync] Logiless {date_from}〜{date_to} のShipped出荷を取得中...")

    # 1. Logiless から出荷済みデータを取得
    try:
        if not logiless_client:
            logiless_client = LogilessClient()
        shipments = _logiless_get_shipped(logiless_client, date_from, date_to)
    except Exception as e:
        return {"error": f"Logiless API error: {e}", "processed": 0}

    print(f"[shipping_sync] {len(shipments)}件の出荷済み（追跡番号あり）を取得")

    if not shipments:
        return {"processed": 0, "success": 0, "failed": 0, "skipped": 0}

    # 2. RMS配送情報更新payloadを構築
    c = RMSClient(rms_secret, rms_license)
    api = OrderAPI(c)

    payloads: list[dict] = []
    skipped: list[str] = []
    for s in shipments:
        payload = _build_shipping_payload(s)
        if payload:
            payloads.append(payload)
        else:
            order_num = s.get("sales_order", {}).get("code", "unknown")
            skipped.append(order_num)

    print(f"[shipping_sync] {len(payloads)}件をRMSに反映予定（スキップ:{len(skipped)}件）")

    if dry_run:
        c.close()
        return {
            "processed": len(payloads),
            "dry_run": True,
            "payloads_preview": payloads[:3],
        }

    # 3. RMSに配送情報を登録
    success = 0
    failed = 0
    errors: list[dict] = []

    # バルク送信（最大150件/リクエスト）
    CHUNK = 150
    for i in range(0, len(payloads), CHUNK):
        chunk = payloads[i:i + CHUNK]
        try:
            r = api.update_order_shipping(chunk)
            msg_list = r.get("MessageModelList", [])
            if msg_list:
                for msg in msg_list:
                    if msg.get("messageType") == "ERROR":
                        failed += 1
                        errors.append({
                            "orderNumber": msg.get("orderNumber", ""),
                            "error": msg.get("message", ""),
                        })
                    else:
                        success += 1
            else:
                success += len(chunk)
            print(f"  chunk {i//CHUNK + 1}: {len(chunk)}件処理完了")
        except Exception as e:
            failed += len(chunk)
            errors.append({"chunk": i // CHUNK, "error": str(e)[:200]})
            print(f"  chunk {i//CHUNK + 1}: ERROR {e}")

        time.sleep(1)  # rate limit

    c.close()

    result = {
        "processed": len(payloads),
        "success": success,
        "failed": failed,
        "skipped": len(skipped),
        "errors": errors[:10],  # 最初の10件のみ
    }
    print(f"[shipping_sync] 完了: 成功{success} / 失敗{failed} / スキップ{len(skipped)}")
    return result


if __name__ == "__main__":
    rms_ss = os.environ.get("RMS_SERVICE_SECRET", "")
    rms_lk = os.environ.get("RMS_LICENSE_KEY", "")

    if not all([rms_ss, rms_lk]):
        print("ERROR: RMS_SERVICE_SECRET and RMS_LICENSE_KEY required")
        sys.exit(1)

    dry_run = "--dry-run" in sys.argv

    result = sync_shipping(rms_ss, rms_lk, dry_run=dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
