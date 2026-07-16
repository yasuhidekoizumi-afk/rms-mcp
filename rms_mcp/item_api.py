"""ItemAPI 2.0 wrapper — 商品の検索・取得・登録・更新・削除."""
from typing import Any

from rms_mcp.client import RMSClient

SEARCH_PAGE_SIZE = 10  # RMS ItemAPI 2.0 はpageSize指定不可（常に10件/ページ）


class ItemAPI:
    """楽天 RMS ItemAPI 2.0 ラッパー.

    ItemAPIはRESTful設計で、HTTPメソッドが操作に対応する:
      GET    /items/search/  — 商品検索（offset ベースのページネーション、10件/ページ固定）
      GET    /items/{manageNumber}/ — 個別商品取得
      PUT    /items/         — 商品登録・更新 (upsert)
      PATCH  /items/{manageNumber}/ — 部分更新
      DELETE /items/{manageNumber}/ — 削除
    """

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
        return self._c.get(f"/items/{manage_number}/").json()

    def upsert(self, item_data: dict) -> dict:
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
        return self._c.put("/items/", json=item_data).json()

    def patch(self, manage_number: str, patch_data: dict) -> dict:
        """商品部分更新.

        patch_dataは更新対象フィールドのみを含む:
        {"standardPrice": 980}  → 価格のみ更新
        {"salesDescription": "新キャッチコピー"} → 販売説明のみ更新
        """
        return self._c.patch(
            f"/items/{manage_number}/", json=patch_data
        ).json()

    def delete(self, manage_number: str) -> dict:
        """商品削除."""
        return self._c.delete(f"/items/{manage_number}/").json()
