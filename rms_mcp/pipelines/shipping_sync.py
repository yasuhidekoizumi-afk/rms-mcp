"""Logiless → RMS 出荷同期パイプライン.

Logilessで出荷完了した注文の追跡番号を、RMSの配送情報に反映する。

処理フロー:
1. Logiless API で Shipped ステータスの出荷を取得
2. 各出荷の sales_order.code (= RMS注文番号) と追跡番号を抽出
3. RMS getOrder で basketId を取得
4. RMS updateOrderShipping で配送情報を登録

実行方法:
    python -m rms_mcp.pipelines.shipping_sync

    # 環境変数
    RMS_SERVICE_SECRET=SP_xxx
    RMS_LICENSE_KEY=SL_xxx

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

from rms_mcp.client import RMSClient
from rms_mcp.order_api import OrderAPI
from rms_mcp.pipelines.logiless_client import LogilessClient

JST = ZoneInfo("Asia/Tokyo")

# Logiless配送方法 → RMS配送業者ID のマッピング
# ※ RMS配送業者IDは店舗設定によって異なる。要実環境確認。
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
        r = client.get("/outbound_deliveries", params=params)
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


def _extract_shipping_info(shipment: dict) -> dict | None:
    """Logiless出荷データから配送情報を抽出.

    Returns: {order_number, tracking_numbers, shipping_company_id} or None
    """
    sales_order = shipment.get("sales_order", {})
    order_number = sales_order.get("code")
    if not order_number:
        return None

    # 楽天市場の注文番号のみ対象（404839-で始まる）
    if not order_number.startswith("404839-"):
        return None

    tracking_numbers = shipment.get("delivery_tracking_numbers", [])
    if not tracking_numbers:
        return None

    delivery_method = shipment.get("delivery_method", "")
    company_map = DELIVERY_METHOD_MAP.get(delivery_method, {})
    shipping_company_id = company_map.get("shippingCompanyId", 102)

    return {
        "order_number": order_number,
        "tracking_numbers": tracking_numbers,
        "shipping_company_id": shipping_company_id,
    }


def _get_basket_ids(api: OrderAPI, order_number: str) -> list[int]:
    """RMS注文からbasketIdリストを取得（1注文に複数梱包がある場合）."""
    r = api.get_order([order_number])
    orders = r.get("OrderModelList", [])
    if not orders:
        return []

    basket_ids: list[int] = []
    for pkg in orders[0].get("PackageModelList", []) or []:
        bid = pkg.get("basketId")
        if bid:
            basket_ids.append(int(bid))
    return basket_ids


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

    # 2. 配送情報を抽出
    shipping_infos: list[dict] = []
    skipped: list[str] = []
    for s in shipments:
        info = _extract_shipping_info(s)
        if info:
            shipping_infos.append(info)
        else:
            order_num = s.get("sales_order", {}).get("code", "unknown")
            skipped.append(order_num)

    print(f"[shipping_sync] {len(shipping_infos)}件をRMSに反映予定（スキップ:{len(skipped)}件）")

    if dry_run:
        return {
            "processed": len(shipping_infos),
            "dry_run": True,
            "preview": [{"order": si["order_number"], "tracking": si["tracking_numbers"]} for si in shipping_infos[:3]],
        }

    # 3. RMSに配送情報を登録（1件ずつ、basketId取得が必要なため）
    c = RMSClient(rms_secret, rms_license)
    api = OrderAPI(c)

    success = 0
    failed = 0
    errors: list[dict] = []

    for si in shipping_infos:
        try:
            # basketIdを取得
            basket_ids = _get_basket_ids(api, si["order_number"])
            if not basket_ids:
                failed += 1
                errors.append({"orderNumber": si["order_number"], "error": "basketId not found"})
                continue

            # 各basketId（梱包）に追跡番号を割り当て
            for idx, basket_id in enumerate(basket_ids):
                tracking_no = si["tracking_numbers"][idx] if idx < len(si["tracking_numbers"]) else si["tracking_numbers"][0]
                shipping_list = [{
                    "shippingId": 1,
                    "shippingCompanyId": si["shipping_company_id"],
                    "shippingNumber": tracking_no,
                }]

                r = api.update_order_shipping(si["order_number"], basket_id, shipping_list)
                msg_list = r.get("MessageModelList", [])
                if msg_list:
                    msg = msg_list[0]
                    if msg.get("messageType") == "ERROR":
                        failed += 1
                        errors.append({"orderNumber": si["order_number"], "error": msg.get("message", "")})
                    else:
                        success += 1
                        print(f"  ✅ {si['order_number']}: {msg.get('message', '')[:60]}")
                else:
                    success += 1

            time.sleep(0.5)  # rate limit
        except Exception as e:
            failed += 1
            errors.append({"orderNumber": si["order_number"], "error": str(e)[:200]})
            print(f"  ❌ {si['order_number']}: {str(e)[:80]}")

    c.close()

    result = {
        "processed": len(shipping_infos),
        "success": success,
        "failed": failed,
        "skipped": len(skipped),
        "errors": errors[:10],
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
