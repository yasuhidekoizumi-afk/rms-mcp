"""ItemAPI 2.0 wrapper — 商品の検索・取得・登録・更新・削除.

正しいエンドポイントURL（JakeJP/Rakuten.RMS.Api ソースコードから確認）:

  GET    /es/2.0/items/search?searchType=&offset=         — 商品検索（10件/ページ固定）
  GET    /es/2.0/items/manage-numbers/{manageNumber}       — 個別商品取得
  PUT    /es/2.0/items/manage-numbers/{manageNumber}       — 商品登録・更新 (upsert)
  PATCH  /es/2.0/items/manage-numbers/{manageNumber}       — 部分更新
  DELETE /es/2.0/items/manage-numbers/{manageNumber}       — 削除
  POST   /es/2.0/items/bulk-get                            — 一括取得
"""
from typing import Any

from rms_mcp.client import RMSClient

SEARCH_PAGE_SIZE = 10  # RMS ItemAPI 2.0 はpageSize指定不可（常に10件/ページ）
REST_BASE = "https://api.rms.rakuten.co.jp/es/2.0"


class ItemAPI:
    """楽天 RMS ItemAPI 2.0 ラッパー."""

    def __init__(self, client: RMSClient):
        self._c = client

    def search(self, *, search_type: int = 1,
               offset: int = 0,
               item_url: str | None = None,
               genre_id: int | None = None,
               item_number: str | None = None,
               shop_status: int | None = None) -> dict:
        """商品検索.

        search_type: 1=商品管理番号(全件), 2=除外(表示/在庫切れ), 3=除外(在庫切れ)
        offset: 0始まりのオフセット（10件/ページ固定）
        Returns: {offset, numFound, results: [{item: {...}}, ...]}
        """
        params: dict[str, Any] = {
            "searchType": search_type,
            "offset": offset,
        }
        if item_url:
            params["itemUrl"] = item_url
        if genre_id:
            params["genreId"] = genre_id
        if item_number:
            params["itemNumber"] = item_number
        if shop_status is not None:
            params["shopStatus"] = shop_status

        return self._c.get("/items/search/", params=params).json()

    def search_all(self, **kw) -> list[dict]:
        """全ページを取得して商品リストを返す（10件/ページで自動ページング）."""
        all_items: list[dict] = []
        offset = 0
        while True:
            r = self.search(offset=offset, **kw)
            results = r.get("results", [])
            all_items.extend(results)
            total = r.get("numFound", 0)
            if len(all_items) >= total or not results:
                break
            offset += SEARCH_PAGE_SIZE
        return all_items

    def get(self, manage_number: str) -> dict:
        """個別商品取得（管理番号で指定）."""
        url = f"{REST_BASE}/items/manage-numbers/{manage_number}"
        r = self._c.get(url)
        return r.json()

    def bulk_get(self, manage_numbers: list[str]) -> dict:
        """一括取得."""
        url = f"{REST_BASE}/items/bulk-get"
        r = self._c.post(url, json={"manageNumbers": manage_numbers})
        return r.json()

    def upsert(self, manage_number: str, item_data: dict) -> dict:
        """商品登録・更新 (upsert).

        item_dataの主なフィールド:
        {
            "manageNumber": "item-id",
            "itemType": "NORMAL",
            "title": "商品名",
            "itemNumber": "商品番号",
            "standardPrice": 1080,  # 税込価格
            "genreId": 100307,
            "description": "商品説明",
            "tagline": "サブタイトル",
            "variants": { ... },
            "images": [ ... ],
            ...
        }
        """
        url = f"{REST_BASE}/items/manage-numbers/{manage_number}"
        r = self._c.put(url, json=item_data)
        if r.status_code in (200, 204):
            return {"status": "ok", "manageNumber": manage_number}
        return r.json()

    def patch(self, manage_number: str, patch_data: dict) -> dict:
        """商品部分更新.

        patch_dataは更新対象フィールドのみを含む:
        {"standardPrice": 980}  → 価格のみ更新
        {"salesDescription": "新キャッチコピー"} → 販売説明のみ更新
        """
        url = f"{REST_BASE}/items/manage-numbers/{manage_number}"
        r = self._c.patch(url, json=patch_data)
        if r.status_code in (200, 204):
            return {"status": "ok", "manageNumber": manage_number}
        return r.json()

    def delete(self, manage_number: str) -> dict:
        """商品削除."""
        url = f"{REST_BASE}/items/manage-numbers/{manage_number}"
        r = self._c.delete(url)
        if r.status_code in (200, 204):
            return {"status": "deleted"}
        return r.json()
