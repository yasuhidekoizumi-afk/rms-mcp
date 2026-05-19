"""OrderAPI + PurchaseItemAPI wrappers."""
from typing import Any

from rms_mcp.client import RMSClient

ORDER_PROGRESS = {
    100: "注文確認待ち", 200: "楽天処理中", 300: "発送待ち",
    400: "変更確定待ち", 500: "発送済", 600: "支払手続き中",
    700: "支払手続き済", 800: "キャンセル確定待ち", 900: "キャンセル確定",
}
ACTIVE_PROGRESS = [100, 200, 300, 400, 500, 600, 700]

SEARCH_PAGE_SIZE = 1000
GET_ORDER_CHUNK = 100
MAX_PAGES = 100


class OrderAPI:
    def __init__(self, client: RMSClient):
        self._c = client

    def search_orders(self, start_date: str, end_date: str, *,
                      date_type: int = 1,
                      progress_list: list[int] | None = None) -> dict:
        """Fetch all orderNumbers across pages.

        RMS searchOrder caps each page at 1000 records. Loop pages until
        totalPages is reached (falling back on short-page heuristic).
        """
        all_numbers: list[str] = []
        page = 1
        while True:
            payload: dict[str, Any] = {
                "dateType": date_type,
                "startDatetime": start_date,
                "endDatetime": end_date,
                "PaginationRequestModel": {
                    "requestRecordsAmount": SEARCH_PAGE_SIZE,
                    "requestPage": page,
                },
            }
            if progress_list is not None:
                payload["orderProgressList"] = progress_list
            res = self._c.post("/order/searchOrder/", json=payload).json()
            nums = res.get("orderNumberList", []) or []
            all_numbers.extend(nums)

            pagination = res.get("PaginationResponseModel") or {}
            total_pages = pagination.get("totalPages") or 0
            if total_pages and page >= total_pages:
                break
            if len(nums) < SEARCH_PAGE_SIZE:
                break
            page += 1
            if page > MAX_PAGES:
                break

        return {"orderNumberList": all_numbers}

    def get_order(self, order_numbers: list[str]) -> dict:
        """Fetch full order details, chunked at 100 (RMS API limit)."""
        all_orders: list[dict] = []
        for i in range(0, len(order_numbers), GET_ORDER_CHUNK):
            chunk = order_numbers[i:i + GET_ORDER_CHUNK]
            r = self._c.post(
                "/order/getOrder/",
                json={"orderNumberList": chunk, "version": "7"},
            ).json()
            all_orders.extend(r.get("OrderModelList", []))
        return {"OrderModelList": all_orders}


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
