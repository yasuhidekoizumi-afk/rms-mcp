"""InquiryManagementAPI wrapper — 問い合わせの取得・返信・管理.

エンドポイント（JSON形式, /es/1.0/inquirymng-api/）:

  GET    /inquiries/count          — 問い合わせ件数
  GET    /inquiries                — 一覧取得
  GET    /inquiry/{inquiryNumber}  — 個別詳細
  POST   /inquiry/reply            — 返信
  PATCH  /inquiries/read           — 既読
  PATCH  /inquiries/complete       — 完了
  PATCH  /inquiries/incomplete     — 未完了
  POST   /attachment               — 添付ファイル登録
  GET    /attachment?path=&label=  — 添付ファイル取得
"""
from datetime import datetime, timezone
from typing import Any

from rms_mcp.client import RMSClient

INQUIRY_BASE = "https://api.rms.rakuten.co.jp/es/1.0/inquirymng-api"


class InquiryAPI:
    """楽天 RMS InquiryManagementAPI ラッパー."""

    def __init__(self, client: RMSClient):
        self._c = client

    def get_count(self, from_date: str, to_date: str,
                  no_merchant_reply: bool | None = None) -> int:
        """問い合わせ件数取得.

        from_date/to_date: "2026-07-01T00:00:00" 形式
        """
        params: dict[str, Any] = {"fromDate": from_date, "toDate": to_date}
        if no_merchant_reply is not None:
            params["noMerchantReply"] = str(no_merchant_reply).lower()
        r = self._c.get(f"{INQUIRY_BASE}/inquiries/count", params=params)
        return r.json().get("result", {}).get("count", 0)

    def get_inquiries(self, from_date: str, to_date: str,
                      limit: int = 20, page: int = 1,
                      no_merchant_reply: bool | None = None) -> dict:
        """問い合わせ一覧取得."""
        params: dict[str, Any] = {
            "fromDate": from_date,
            "toDate": to_date,
            "limit": limit,
            "page": page,
        }
        if no_merchant_reply is not None:
            params["noMerchantReply"] = str(no_merchant_reply).lower()
        r = self._c.get(f"{INQUIRY_BASE}/inquiries", params=params)
        return r.json()

    def get_inquiry(self, inquiry_number: str) -> dict:
        """個別問い合わせ詳細取得."""
        r = self._c.get(f"{INQUIRY_BASE}/inquiry/{inquiry_number}")
        return r.json()

    def reply(self, inquiry_number: str, shop_id: int,
              message: str, attachments: list[dict] | None = None) -> dict:
        """問い合わせに返信.

        inquiry_number: "404839-20260702-xxxxxxx"
        shop_id: 404839（固定）
        message: 返信本文
        """
        body: dict[str, Any] = {
            "inquiryNumber": inquiry_number,
            "shopId": shop_id,
            "message": message,
        }
        if attachments:
            body["attachments"] = attachments
        r = self._c.post(f"{INQUIRY_BASE}/inquiry/reply", json=body)
        return r.json()

    def mark_read(self, inquiry_numbers: list[str]) -> dict:
        """複数の問い合わせを既読に."""
        r = self._c.patch(
            f"{INQUIRY_BASE}/inquiries/read",
            json={"inquiryNumbers": inquiry_numbers},
        )
        return r.json()

    def mark_complete(self, inquiry_numbers: list[str]) -> dict:
        """複数の問い合わせを完了に."""
        r = self._c.patch(
            f"{INQUIRY_BASE}/inquiries/complete",
            json={"inquiryNumbers": inquiry_numbers},
        )
        return r.json()
