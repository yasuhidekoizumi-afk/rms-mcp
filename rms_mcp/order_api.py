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

# Async shipping update: max orders per request
SHIPPING_ASYNC_CHUNK = 150


class OrderAPI:
    def __init__(self, client: RMSClient):
        self._c = client

    # ── 読み取り系 ──────────────────────────────────────────

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

    def get_sub_status_list(self) -> dict:
        """サブステータス一覧を取得."""
        return self._c.post("/order/getSubStatusList/", json={}).json()

    def get_payment(self, order_number: str) -> dict:
        """支払情報を取得（楽天ペイ決済状況等）."""
        return self._c.post(
            "/order/getPayment/", json={"orderNumber": order_number}
        ).json()

    # ── 書き込み系 ──────────────────────────────────────────

    def confirm_order(self, order_numbers: list[str]) -> dict:
        """受注確認（ステータス: 注文確認待ち → 楽天処理中）.

        バルク対応: 複数注文番号を一度に送信可能。
        """
        return self._c.post(
            "/order/confirmOrder/",
            json={"orderNumberList": order_numbers},
        ).json()

    def update_order_shipping(self, order_number: str, basket_id: int,
                              shipping_list: list[dict]) -> dict:
        """配送情報更新（配送業者・配送番号の登録）.

        RMS APIは orderNumber + BasketidModelList(ShippingModelList) の形式を要求する。
        basketId は getOrder で取得できる（PackageModelList[].basketId）。

        shipping_list の各要素:
        {"shippingId": 1, "shippingCompanyId": 100, "shippingNumber": "1234-5678-9012"}
        """
        payload = {
            "orderNumber": order_number,
            "BasketidModelList": [
                {
                    "basketId": basket_id,
                    "ShippingModelList": shipping_list,
                }
            ],
        }
        return self._c.post(
            "/order/updateOrderShipping/",
            json=payload,
        ).json()

    def update_order_shipping_async(self, payloads: list[dict]) -> dict:
        """非同期配送情報更新（大量データ向け、150件/リクエスト）.

        戻り値に assertionToken が含まれる。結果は get_result_update_order_shipping_async で取得。
        """
        results = []
        for i in range(0, len(payloads), SHIPPING_ASYNC_CHUNK):
            chunk = payloads[i:i + SHIPPING_ASYNC_CHUNK]
            r = self._c.post(
                "/order/updateOrderShippingAsync/",
                json={"Parameter_Model": {"updateOrderShippingModels": chunk}},
            ).json()
            results.append(r)
        return {"results": results}

    def get_result_update_order_shipping_async(self, assertion_token: str) -> dict:
        """非同期配送情報更新の結果取得."""
        return self._c.post(
            "/order/getResultUpdateOrderShippingAsync/",
            json={"assertionToken": assertion_token},
        ).json()

    def update_order_sub_status(self, order_status_list: list[dict]) -> dict:
        """サブステータス更新.

        各要素の形式:
        {"orderKey": {"orderNumber": "...", "shippingId": 1}, "subStatusId": 5}
        """
        return self._c.post(
            "/order/updateOrderSubStatus/",
            json={"orderSubStatusModels": order_status_list},
        ).json()

    def update_order_memo(self, order_number: str, memo: str) -> dict:
        """注文メモ更新（店舗側メモ）."""
        return self._c.post(
            "/order/updateOrderMemo/",
            json={"orderNumber": order_number, "memo": memo},
        ).json()

    def update_order_remarks(self, order_number: str, remarks: str) -> dict:
        """注文備考更新（購入者に表示される備考）."""
        return self._c.post(
            "/order/updateOrderRemarks/",
            json={"orderNumber": order_number, "remarks": remarks},
        ).json()

    def update_order_sender(self, order_number: str, sender_model: dict) -> dict:
        """送り主情報更新.

        sender_model の主なフィールド:
        {
            "senderSei": "苗字", "senderMei": "名前",
            "senderSeiKana": "ミョウジ", "senderMeiKana": "ナマエ",
            "senderZipCode1": "123", "senderZipCode2": "4567",
            "senderPrefecture": "東京都",
            "senderAddress1": "市区町村", "senderAddress2": "番地以下",
            "senderPhoneNumber1": "03", "senderPhoneNumber2": "1234", "senderPhoneNumber3": "5678",
        }
        """
        payload = {"orderNumber": order_number, "SenderModel": sender_model}
        return self._c.post("/order/updateOrderSender/", json=payload).json()

    def update_order_orderer(self, order_number: str, orderer_model: dict) -> dict:
        """注文者情報更新.

        orderer_model の主なフィールド:
        {
            "OrdererName1": "苗字", "OrdererName2": "名前",
            "OrdererKana1": "ミョウジ", "OrdererKana2": "ナマエ",
            "OrdererZipCode1": "123", "OrdererZipCode2": "4567",
            "OrdererPrefecture": "東京都",
            "OrdererAddress1": "市区町村", "OrdererAddress2": "番地以下",
            "OrdererSex": "M",  # M / F
            "OrdererPhoneNumber1": "03", "OrdererPhoneNumber2": "1234", "OrdererPhoneNumber3": "5678",
            "OrdererEmail": "user@example.com",
        }
        """
        payload = {"orderNumber": order_number, "OrdererModel": orderer_model}
        return self._c.post("/order/updateOrderOrderer/", json=payload).json()

    def update_order_delivery(self, order_number: str, delivery_model: dict) -> dict:
        """お届け先情報更新.

        delivery_model の主なフィールド:
        {
            "deliveryName1": "苗字", "deliveryName2": "名前",
            "deliveryKana1": "ミョウジ", "deliveryKana2": "ナマエ",
            "deliveryZipCode1": "123", "deliveryZipCode2": "4567",
            "deliveryPrefecture": "東京都",
            "deliveryAddress1": "市区町村", "deliveryAddress2": "番地以下",
            "deliveryPhoneNumber1": "03", "deliveryPhoneNumber2": "1234", "deliveryPhoneNumber3": "5678",
        }
        """
        payload = {"orderNumber": order_number, "DeliveryModel": delivery_model}
        return self._c.post("/order/updateOrderDelivery/", json=payload).json()

    def cancel_order(self, order_number: str, cancel_reason: int = 0) -> dict:
        """注文キャンセル（発送前）.

        cancel_reason: 0=店舗都合(その他), 1=商品欠番, 2=商品違犯, 3=支払方法エラー,
                       4=在庫切れ, 5=価格設定ミス, 6=お客様都合, 7=その他
        """
        payload: dict[str, Any] = {"orderNumber": order_number}
        if cancel_reason:
            payload["cancelReason"] = cancel_reason
        return self._c.post("/order/cancelOrder/", json=payload).json()

    def cancel_order_after_shipping(self, order_number: str) -> dict:
        """発送後キャンセル."""
        return self._c.post(
            "/order/cancelOrderAfterShipping/", json={"orderNumber": order_number}
        ).json()

    def simulate_coupon_amount(self, coupon_model: dict) -> dict:
        """クーポン金額シミュレーション."""
        return self._c.post(
            "/order/simulateCouponAmount/", json=coupon_model
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
