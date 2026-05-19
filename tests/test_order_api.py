"""Tests for OrderAPI: pagination, chunking, payload shape."""
import httpx
import respx

from rms_mcp.client import REST_BASE, RMSClient
from rms_mcp.order_api import GET_ORDER_CHUNK, OrderAPI, SEARCH_PAGE_SIZE


def _client() -> RMSClient:
    return RMSClient("SP_x", "SL_y", sleep=lambda s: None)


@respx.mock
def test_search_orders_single_page():
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(200, json={
            "orderNumberList": ["A", "B"],
            "PaginationResponseModel": {"totalPages": 1},
        })
    )
    c = _client()
    api = OrderAPI(c)
    r = api.search_orders("2026-05-01T00:00:00+0900", "2026-05-02T00:00:00+0900")
    assert r["orderNumberList"] == ["A", "B"]
    c.close()


@respx.mock
def test_search_orders_paginates_until_totalPages():
    page1 = [f"O{i}" for i in range(SEARCH_PAGE_SIZE)]
    page2 = ["X", "Y"]
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        side_effect=[
            httpx.Response(200, json={
                "orderNumberList": page1,
                "PaginationResponseModel": {"totalPages": 2},
            }),
            httpx.Response(200, json={
                "orderNumberList": page2,
                "PaginationResponseModel": {"totalPages": 2},
            }),
        ]
    )
    c = _client()
    r = OrderAPI(c).search_orders("s", "e")
    assert len(r["orderNumberList"]) == SEARCH_PAGE_SIZE + 2
    assert r["orderNumberList"][-2:] == ["X", "Y"]
    c.close()


@respx.mock
def test_search_orders_stops_on_short_page_when_no_totalPages():
    """Fallback: if RMS doesn't return totalPages, stop when page is short."""
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(200, json={
            "orderNumberList": ["only_one"],
        })
    )
    c = _client()
    r = OrderAPI(c).search_orders("s", "e")
    assert r["orderNumberList"] == ["only_one"]
    c.close()


@respx.mock
def test_search_orders_passes_progress_filter():
    route = respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(200, json={"orderNumberList": []})
    )
    c = _client()
    OrderAPI(c).search_orders("s", "e", progress_list=[800, 900])
    body = route.calls.last.request.read().decode()
    assert "orderProgressList" in body
    assert "800" in body and "900" in body
    c.close()


@respx.mock
def test_get_order_chunks_at_100():
    """Asks for 250 orders → expects 3 POSTs (100+100+50)."""
    respx.post(f"{REST_BASE}/order/getOrder/").mock(
        side_effect=[
            httpx.Response(200, json={"OrderModelList": [{"i": j} for j in range(100)]}),
            httpx.Response(200, json={"OrderModelList": [{"i": j} for j in range(100, 200)]}),
            httpx.Response(200, json={"OrderModelList": [{"i": j} for j in range(200, 250)]}),
        ]
    )
    c = _client()
    nums = [f"N{i}" for i in range(250)]
    r = OrderAPI(c).get_order(nums)
    assert len(r["OrderModelList"]) == 250
    c.close()


@respx.mock
def test_get_order_chunk_size_constant():
    """Verify the chunk size constant matches RMS API limit."""
    assert GET_ORDER_CHUNK == 100
