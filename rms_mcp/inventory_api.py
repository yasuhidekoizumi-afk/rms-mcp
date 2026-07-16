"""InventoryAPI 2.1 wrapper — 在庫の取得・更新.

正しいエンドポイントURL（JakeJP/Rakuten.RMS.Api ソースコードから確認）:

  GET    /es/{ver}/inventories/manage-numbers/{manageNumber}/variants/{variantId}
  PUT    /es/{ver}/inventories/manage-numbers/{manageNumber}/variants/{variantId}
  DELETE /es/{ver}/inventories/manage-numbers/{manageNumber}/variants/{variantId}
  POST   /es/2.0/inventories/bulk-get
  GET    /es/2.0/inventories/bulk-get/range?minQuantity=&maxQuantity=
  POST   /es/{ver}/inventories/bulk-upsert

mode: ABSOLUTE（絶対値設定）または RELATIVE（相対変更）
"""
import time
from typing import Any

from rms_mcp.client import RMSClient

REST_BASE = "https://api.rms.rakuten.co.jp/es"
VERSION = "2.1"
BULK_VERSION = "2.0"  # bulk-get は2.0固定


class InventoryAPI:
    """楽天 RMS InventoryAPI 2.1 ラッパー."""

    def __init__(self, client: RMSClient):
        self._c = client

    def get_variant(self, manage_number: str, variant_id: str) -> dict:
        """個別在庫取得.

        Returns: {manageNumber, variantId, quantity, created, updated}
        """
        path = f"/inventories/manage-numbers/{manage_number}/variants/{variant_id}"
        url = f"{REST_BASE}/{VERSION}{path}"
        # client の base_url を使わず直接URL指定
        r = self._c.get(url)
        return r.json()

    def upsert_variant(self, manage_number: str, variant_id: str,
                       mode: str = "ABSOLUTE", quantity: int = 0) -> dict:
        """個別在庫更新.

        mode: ABSOLUTE（絶対値）または RELATIVE（相対変更）
        """
        path = f"/inventories/manage-numbers/{manage_number}/variants/{variant_id}"
        url = f"{REST_BASE}/{VERSION}{path}"
        body = {"mode": mode, "quantity": quantity}
        r = self._c.put(url, json=body)
        # 204 No Content の場合は空なので空dictを返す
        if r.status_code == 204:
            return {"status": "ok", "manageNumber": manage_number, "variantId": variant_id, "quantity": quantity}
        return r.json()

    def delete_variant(self, manage_number: str, variant_id: str) -> dict:
        """在庫情報削除."""
        path = f"/inventories/manage-numbers/{manage_number}/variants/{variant_id}"
        url = f"{REST_BASE}/{VERSION}{path}"
        r = self._c.delete(url)
        if r.status_code == 204:
            return {"status": "deleted"}
        return r.json()

    def bulk_get(self, inventory_list: list[dict]) -> dict:
        """一括在庫取得（最大1000件）.

        inventory_list の各要素: {manageNumber, variantId}
        Returns: {inventories: [{manageNumber, variantId, quantity, created, updated}]}
        """
        url = f"{REST_BASE}/{BULK_VERSION}/inventories/bulk-get"
        r = self._c.post(url, json={"inventories": inventory_list})
        return r.json()

    def bulk_get_range(self, min_quantity: int | None = None,
                       max_quantity: int | None = None) -> dict:
        """在庫数範囲指定で一括取得.

        min_quantity / max_quantity のいずれかまたは両方を指定。
        """
        url = f"{REST_BASE}/{BULK_VERSION}/inventories/bulk-get/range"
        params = {}
        if min_quantity is not None:
            params["minQuantity"] = min_quantity
        if max_quantity is not None:
            params["maxQuantity"] = max_quantity
        r = self._c.get(url, params=params)
        return r.json()

    def bulk_upsert(self, inventory_list: list[dict]) -> dict:
        """一括在庫更新（最大400件）.

        各要素: {manageNumber, variantId, mode: "ABSOLUTE"|"RELATIVE", quantity}
        """
        url = f"{REST_BASE}/{VERSION}/inventories/bulk-upsert"
        r = self._c.post(url, json={"inventories": inventory_list})
        if r.status_code == 204:
            return {"status": "ok", "updated": len(inventory_list)}
        return r.json()

    # ── 便利メソッド ────────────────────────────────────

    def get_all_inventory_for_item(self, manage_number: str,
                                    variant_ids: list[str]) -> list[dict]:
        """指定商品の全バリアントの在庫を取得."""
        inv_list = [{"manageNumber": manage_number, "variantId": vid} for vid in variant_ids]
        result = self.bulk_get(inv_list)
        return result.get("inventories", [])

    def set_inventory(self, manage_number: str, variant_id: str, quantity: int) -> dict:
        """在庫を絶対値で設定（便利メソッド）."""
        return self.upsert_variant(manage_number, variant_id, mode="ABSOLUTE", quantity=quantity)


# ── Logiless連携ユーティリティ ──────────────────────────

SHIPPING_COMPANY_MAP = {
    "ヤマト運輸": 100,
    "佐川急便": 101,
    "日本郵政": 102,
    "ゆうパック": 102,
    "ゆうパケット": 103,
    "クリックポスト": 104,
    "レターパック": 105,
}


def logiless_to_rms_shipping(carrier_name: str) -> int | None:
    """Logiless配送業者名からRMS shippingCompanyIdを取得."""
    return SHIPPING_COMPANY_MAP.get(carrier_name)
