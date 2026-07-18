"""Tests for RMSClient: auth header, retry behavior, error handling."""
import base64

import httpx
import pytest
import respx

from rms_mcp.client import REST_BASE, RMSClient


def _make_client(sleep_calls: list[float] | None = None) -> RMSClient:
    def fake_sleep(s: float) -> None:
        if sleep_calls is not None:
            sleep_calls.append(s)
    return RMSClient("SP404839_x", "SL404839_y", sleep=fake_sleep)


@respx.mock
def test_auth_header_is_esa_base64():
    expected = "ESA " + base64.b64encode(b"SP404839_x:SL404839_y").decode()
    route = respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(200, json={"orderNumberList": []})
    )
    c = _make_client()
    c.post("/order/searchOrder/", json={})
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == expected
    c.close()


@respx.mock
def test_error_message_does_not_leak_auth_header():
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    c = _make_client()
    with pytest.raises(RuntimeError) as exc:
        c.post("/order/searchOrder/", json={})
    assert "Authorization" not in str(exc.value)
    assert "ESA " not in str(exc.value)
    c.close()


@respx.mock
def test_retries_on_500_then_succeeds():
    sleeps: list[float] = []
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        side_effect=[
            httpx.Response(500, text="server err"),
            httpx.Response(503, text="busy"),
            httpx.Response(200, json={"orderNumberList": ["A"]}),
        ]
    )
    c = _make_client(sleeps)
    r = c.post("/order/searchOrder/", json={})
    assert r.json()["orderNumberList"] == ["A"]
    # Two backoffs before the 3rd success: 1.0s, 2.0s
    assert sleeps == [1.0, 2.0]
    c.close()


@respx.mock
def test_retries_exhausted_on_persistent_5xx():
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    c = _make_client([])
    with pytest.raises(RuntimeError) as exc:
        c.post("/order/searchOrder/", json={})
    assert "502" in str(exc.value)
    assert "after 5 attempts" in str(exc.value)
    c.close()


@respx.mock
def test_no_retry_on_4xx():
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    sleeps: list[float] = []
    c = _make_client(sleeps)
    with pytest.raises(RuntimeError) as exc:
        c.post("/order/searchOrder/", json={})
    assert "400" in str(exc.value)
    assert sleeps == []  # no retries
    c.close()


@respx.mock
def test_retries_on_connect_error():
    sleeps: list[float] = []
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        side_effect=[
            httpx.ConnectError("conn refused"),
            httpx.Response(200, json={"orderNumberList": []}),
        ]
    )
    c = _make_client(sleeps)
    r = c.post("/order/searchOrder/", json={})
    assert r.status_code == 200
    assert sleeps == [1.0]
    c.close()
