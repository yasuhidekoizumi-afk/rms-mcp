"""Tests for server.py aggregation logic: _i(), revenue allocation, MCP tools."""
from datetime import datetime
from unittest.mock import patch

import pytest

from rms_mcp.server import _i, _daily_sales, _product_ranking, _cancel_rate, _order_detail


# --- _i() NULL-coercion ---

class TestICoercion:
    def test_none_is_zero(self):
        assert _i(None) == 0

    def test_zero_is_zero(self):
        assert _i(0) == 0

    def test_empty_string_is_zero(self):
        assert _i("") == 0

    def test_int_passes_through(self):
        assert _i(1500) == 1500

    def test_string_int_converts(self):
        assert _i("1500") == 1500


# --- daily_sales aggregation ---

def _order(date: str, total: int, **kw) -> dict:
    base = {
        "orderDatetime": f"{date}T10:00:00+0900",
        "totalPrice": total,
        "goodsTax": kw.get("tax", 0),
        "couponShopPrice": kw.get("coupon_shop", 0),
        "couponOtherPrice": kw.get("coupon_other", 0),
        "deliveryPrice": kw.get("delivery", 0),
    }
    return base


class FakeOrderAPI:
    def __init__(self, orders: list[dict]):
        self._orders = orders

    def search_orders(self, *a, **kw):
        return {"orderNumberList": [f"N{i}" for i in range(len(self._orders))]}

    def get_order(self, nums):
        return {"OrderModelList": self._orders}


@pytest.mark.asyncio
async def test_daily_sales_aggregates_by_date():
    orders = [
        _order("2026-05-01", 1000, tax=100, delivery=500),
        _order("2026-05-01", 2000, tax=200, delivery=500),
        _order("2026-05-02", 3000, tax=300, delivery=500),
    ]
    api = FakeOrderAPI(orders)
    out = await _daily_sales({"start_date": "2026-05-01", "end_date": "2026-05-02"}, api)
    text = out[0].text
    assert "2026-05-01" in text
    assert "2026-05-02" in text
    # Day-1 totals
    assert "¥3,000" in text  # 2 orders summed on day 1
    # Grand total
    assert "Total" in text
    assert "¥6,000" in text


@pytest.mark.asyncio
async def test_daily_sales_handles_null_numeric_fields():
    """Order with all numeric fields null should not crash."""
    orders = [{
        "orderDatetime": "2026-05-01T10:00:00+0900",
        "totalPrice": None,
        "goodsTax": None,
        "couponShopPrice": None,
        "couponOtherPrice": None,
        "deliveryPrice": None,
    }]
    api = FakeOrderAPI(orders)
    out = await _daily_sales({"start_date": "2026-05-01", "end_date": "2026-05-01"}, api)
    assert "¥0" in out[0].text


@pytest.mark.asyncio
async def test_daily_sales_empty_returns_no_orders_message():
    api = FakeOrderAPI([])
    out = await _daily_sales({"start_date": "2026-05-01", "end_date": "2026-05-01"}, api)
    assert "No orders found" in out[0].text


# --- product_ranking revenue allocation ---

@pytest.mark.asyncio
async def test_product_ranking_prorates_total_price():
    """
    Order: totalPrice=900 (after a ¥100 coupon)
    Items: A (qty=1, price=600), B (qty=2, price=200) → gross=1000
    Allocation: A=900*600/1000=540, B=900*400/1000=360
    """
    orders = [{
        "orderDatetime": "2026-05-01T10:00:00+0900",
        "totalPrice": 900,
        "PackageModelList": [{
            "ItemModelList": [
                {"itemName": "A", "itemNumber": "a1", "units": 1, "price": 600},
                {"itemName": "B", "itemNumber": "b1", "units": 2, "price": 200},
            ]
        }],
    }]
    api = FakeOrderAPI(orders)
    out = await _product_ranking({"start_date": "2026-05-01", "end_date": "2026-05-01"}, api)
    text = out[0].text
    # A: revenue=540, gross=600
    # B: revenue=360, gross=400
    assert "¥540" in text
    assert "¥600" in text  # gross of A
    assert "¥360" in text
    assert "¥400" in text  # gross of B


@pytest.mark.asyncio
async def test_product_ranking_handles_missing_total_price():
    """When totalPrice is null, fall back to gross."""
    orders = [{
        "orderDatetime": "2026-05-01T10:00:00+0900",
        "totalPrice": None,
        "PackageModelList": [{
            "ItemModelList": [{"itemName": "A", "itemNumber": "a", "units": 2, "price": 500}],
        }],
    }]
    api = FakeOrderAPI(orders)
    out = await _product_ranking({"start_date": "2026-05-01", "end_date": "2026-05-01"}, api)
    assert "¥1,000" in out[0].text


@pytest.mark.asyncio
async def test_product_ranking_sums_across_orders():
    """Same product across two orders should aggregate."""
    common_item = {"itemName": "X", "itemNumber": "x", "units": 1, "price": 1000}
    orders = [
        {
            "orderDatetime": "2026-05-01T10:00:00+0900",
            "totalPrice": 1000,
            "PackageModelList": [{"ItemModelList": [common_item]}],
        },
        {
            "orderDatetime": "2026-05-02T10:00:00+0900",
            "totalPrice": 1000,
            "PackageModelList": [{"ItemModelList": [common_item]}],
        },
    ]
    api = FakeOrderAPI(orders)
    out = await _product_ranking({"start_date": "2026-05-01", "end_date": "2026-05-02"}, api)
    text = out[0].text
    # Qty 2, revenue ¥2,000
    assert "| 2 |" in text  # qty column
    assert "¥2,000" in text


# --- order_detail ---

@pytest.mark.asyncio
async def test_order_detail_returns_json():
    api = FakeOrderAPI([{"orderNumber": "N0", "totalPrice": 1000}])
    out = await _order_detail({"order_numbers": ["N0"]}, api)
    assert "orderNumber" in out[0].text
    assert "N0" in out[0].text


# --- cancel_rate ---

class CancelFakeAPI:
    """Returns different counts for active vs cancelled searches."""
    def __init__(self, all_count: int, cancel_count: int):
        self._all = all_count
        self._cancel = cancel_count

    def search_orders(self, start, end, *, date_type=1, progress_list=None):
        if progress_list == [800, 900]:
            return {"orderNumberList": [f"C{i}" for i in range(self._cancel)]}
        return {"orderNumberList": [f"A{i}" for i in range(self._all)]}

    def get_order(self, nums):
        return {"OrderModelList": []}


@pytest.mark.asyncio
async def test_cancel_rate_computes_percentage():
    api = CancelFakeAPI(all_count=100, cancel_count=5)
    out = await _cancel_rate({"start_date": "2026-05-01", "end_date": "2026-05-31"}, api)
    text = out[0].text
    assert "Total: 100" in text
    assert "Cancelled: 5" in text
    assert "5.0%" in text


@pytest.mark.asyncio
async def test_cancel_rate_zero_division_safe():
    api = CancelFakeAPI(all_count=0, cancel_count=0)
    out = await _cancel_rate({"start_date": "2026-05-01", "end_date": "2026-05-31"}, api)
    assert "0.0%" in out[0].text
