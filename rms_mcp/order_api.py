"""OrderAPI + PurchaseItemAPI wrappers."""
from typing import Any

from rms_mcp.client import RMSClient

ORDER_PROGRESS = {
    100: "注文確認待ち", 200: "楽天処理中", 300: "発送待ち",
    400: "変更確定待ち", 500: "発送済", 600: "支払手続き中",
    700: "支払手続き済", 800: "キャンセル確定待ち", 900: "キャンセル確定",
}
ACTIVE_PROGRESS = [100, 200, 300, 400, 500, 600, 700]


class OrderAPI:
    def __init__(self, client: RMSClient):
        self._c = client

    def search_orders(self, start_date: str, end_date: str, *,
                      date_type: int = 1, progress_list: list[int] | None = None) -> dict:
        payload: dict[str, Any] = {
            "dateType": date_type,
            "startDatetime": start_date,
            "endDatetime": end_date,
        }
        if progress_list is not None:
            payload["orderProgressList"] = progress_list
        return self._c.post("/order/searchOrder/", json=payload).json()

    def get_order(self, order_numbers: list[str]) -> dict:
        return self._c.post(
            "/order/getOrder/",
            json={"orderNumberList": order_numbers, "version": "7"},
        ).json()


class PurchaseItemAPI:
    def __init__(self, client: RMSClient):
        self._c = client

    def search_order_items(self, start_date: str, end_date: str, *,
                           date_type: int = 1, progress_list: list[int] | None = None,
                           limit: int = 100) -> dict:
        payload: dict[str, Any] = {
            "dateType": date_type,
            "startDatetime": start_date,
            "endDatetime": end_date,
            "PaginationRequestModel": {"requestRecordsAmount": limit},
        }
        if progress_list is not None:
            payload["orderProgressList"] = progress_list
        return self._c.post("/purchaseItem/searchOrderItem/", json=payload).json()
