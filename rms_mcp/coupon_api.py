"""CouponAPI wrapper — クーポンの検索・発行・更新・削除.

CouponAPIはJSONではなくXML形式で通信する（RMSの旧API形式）。
エンドポイント: /es/1.0/coupon/*

主な操作:
  GET    /coupon/search           — クーポン検索
  GET    /coupon/get?couponCode=  — 個別クーポン取得
  POST   /coupon/issue            — クーポン発行
  POST   /coupon/update           — クーポン更新
  POST   /coupon/delete           — クーポン削除

サンクスクーポン:
  GET    /thankscoupons           — 検索
  GET    /thankscoupon/{id}       — 取得
  POST   /thankscoupon            — 発行
  PUT    /thankscoupon/{id}       — 更新
  PUT    /thankscoupon/{id}/issuestatus/stop — 停止
"""
import xml.etree.ElementTree as ET
from typing import Any

from rms_mcp.client import RMSClient

COUPON_BASE = "https://api.rms.rakuten.co.jp/es/1.0"


class CouponAPI:
    """楽天 RMS CouponAPI ラッパー（XML形式）."""

    def __init__(self, client: RMSClient):
        self._c = client

    def search(self, *, hits: int = 30, page: int = 1,
               coupon_name: str | None = None,
               coupon_code: str | None = None) -> dict:
        """クーポン検索.

        Returns: {coupons: [...], allCount: N}
        """
        params: dict[str, Any] = {"hits": hits, "page": page}
        if coupon_name:
            params["couponName"] = coupon_name
        if coupon_code:
            params["couponCode"] = coupon_code

        r = self._c.get(f"{COUPON_BASE}/coupon/search", params=params)
        return self._parse_search_response(r.text)

    def get(self, coupon_code: str) -> dict:
        """個別クーポン取得."""
        r = self._c.get(f"{COUPON_BASE}/coupon/get", params={"couponCode": coupon_code})
        return self._parse_coupon_xml(r.text)

    def issue(self, coupon_data: dict) -> dict:
        """クーポン発行.

        coupon_dataの主なフィールド:
        {
            "couponName": "セールクーポン",
            "couponStartDate": "2026-08-01T00:00:00+09:00",
            "couponEndDate": "2026-08-07T23:59:59+09:00",
            "discountType": 1,  # 1=円引, 2=%引
            "discountFactor": 300,  # 300円引 または 20(%引)
            "itemType": 4,  # 4=全商品
            "memberAvailMaxCount": 1,
            "issueCount": 1000,
        }
        """
        xml_body = self._dict_to_coupon_xml(coupon_data)
        r = self._c.post(
            f"{COUPON_BASE}/coupon/issue",
            content=xml_body,
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        return self._parse_coupon_xml(r.text)

    def delete(self, coupon_code: str) -> dict:
        """クーポン削除."""
        xml_body = f'<?xml version="1.0" encoding="UTF-8"?>\n<request><coupon><couponCode>{coupon_code}</couponCode></coupon></request>'
        r = self._c.post(
            f"{COUPON_BASE}/coupon/delete",
            content=xml_body,
            headers={"Content-Type": "application/xml; charset=utf-8"},
        )
        return {"status": "deleted", "couponCode": coupon_code}

    # ── XML パーサー ─────────────────────────────────

    def _parse_search_response(self, xml_text: str) -> dict:
        """クーポン検索のXMLレスポンスをパース."""
        root = ET.fromstring(xml_text)
        all_count = root.find('.//allCount')
        coupons = []
        for c in root.findall('.//coupon'):
            coupons.append(self._element_to_dict(c))
        return {
            "allCount": int(all_count.text) if all_count is not None and all_count.text else 0,
            "coupons": coupons,
        }

    def _parse_coupon_xml(self, xml_text: str) -> dict:
        """個別クーポンXMLをパース."""
        root = ET.fromstring(xml_text)
        coupon = root.find('.//coupon')
        if coupon is not None:
            return self._element_to_dict(coupon)
        status = root.find('.//status')
        if status is not None:
            return {"status": "error", "message": status.find('message').text if status.find('message') is not None else "unknown"}
        return {}

    def _element_to_dict(self, elem) -> dict:
        """XML要素を再帰的にdictに変換."""
        result = {}
        for child in elem:
            if len(child) > 0:
                result[child.tag] = self._element_to_dict(child)
            else:
                result[child.tag] = (child.text or '').strip() if child.text else ''
        return result

    def _dict_to_coupon_xml(self, data: dict) -> str:
        """dictをクーポン発行用XMLに変換."""
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<request><coupon>']
        for key, value in data.items():
            lines.append(f'<{key}>{value}</{key}>')
        lines.append('</coupon></request>')
        return '\n'.join(lines)
