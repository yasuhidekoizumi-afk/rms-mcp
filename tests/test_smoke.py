"""Tests for the startup smoke test."""
import logging
import os
from unittest.mock import patch

import httpx
import pytest
import respx

from rms_mcp.client import REST_BASE
from rms_mcp.smoke import run_startup_smoke_test


@pytest.fixture
def _clean_env():
    """Remove RMS env vars so tests start from a known state."""
    keep = {k: v for k, v in os.environ.items() if not k.startswith("RMS_")}
    with patch.dict(os.environ, keep, clear=True):
        yield


def test_skip_when_flag_set(_clean_env, caplog):
    os.environ["RMS_MCP_SKIP_SMOKE"] = "1"
    with caplog.at_level(logging.INFO, logger="rms_mcp.smoke"):
        assert run_startup_smoke_test() is True
    assert any("skipped" in r.message.lower() for r in caplog.records)


def test_fails_when_credentials_missing(_clean_env, caplog):
    with caplog.at_level(logging.ERROR, logger="rms_mcp.smoke"):
        assert run_startup_smoke_test() is False
    assert any("credentials missing" in r.message.lower() for r in caplog.records)


@respx.mock
def test_succeeds_with_good_credentials(_clean_env, caplog):
    os.environ["RMS_SERVICE_SECRET"] = "SP_x"
    os.environ["RMS_LICENSE_KEY"] = "SL_y"
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(200, json={
            "orderNumberList": ["A", "B"],
            "PaginationResponseModel": {"totalPages": 1},
        })
    )
    with caplog.at_level(logging.INFO, logger="rms_mcp.smoke"):
        assert run_startup_smoke_test() is True
    # Should report success with count
    assert any("smoke test passed" in r.message.lower() and "2 orders" in r.message
               for r in caplog.records)


@respx.mock
def test_fails_on_401_with_helpful_hint(_clean_env, caplog):
    """The classic 'I/l typo' scenario must produce a clear admin hint."""
    os.environ["RMS_SERVICE_SECRET"] = "SP_x"
    os.environ["RMS_LICENSE_KEY"] = "SL_typo_ll"  # ll instead of Il
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        return_value=httpx.Response(401, json={
            "Results": {"errorCode": "ES01-01", "message": "Un-Authorised"}
        })
    )
    with caplog.at_level(logging.ERROR, logger="rms_mcp.smoke"):
        assert run_startup_smoke_test() is False
    msgs = "\n".join(r.message for r in caplog.records)
    assert "smoke test FAILED" in msgs
    # Crucially, hint about I/l typo must be in the output
    assert "I/l" in msgs or "capital I" in msgs.lower()


@respx.mock
def test_does_not_raise_on_network_error(_clean_env):
    """Server must boot even if Rakuten is completely unreachable."""
    os.environ["RMS_SERVICE_SECRET"] = "SP_x"
    os.environ["RMS_LICENSE_KEY"] = "SL_y"
    respx.post(f"{REST_BASE}/order/searchOrder/").mock(
        side_effect=httpx.ConnectError("network down")
    )
    # Should return False but not raise.
    assert run_startup_smoke_test() is False
