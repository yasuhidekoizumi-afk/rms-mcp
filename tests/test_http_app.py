"""Tests for HTTP transport: auth middleware, health endpoint."""
import os
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from rms_mcp.http_app import _constant_time_eq, build_app
from rms_mcp.server import server as mcp_server


@pytest.fixture
def app_with_token():
    with patch.dict(os.environ, {"RMS_MCP_AUTH_TOKEN": "test-secret-token"}):
        yield build_app(mcp_server)


@pytest.fixture
def app_without_token():
    env = dict(os.environ)
    env.pop("RMS_MCP_AUTH_TOKEN", None)
    with patch.dict(os.environ, env, clear=True):
        yield build_app(mcp_server)


def test_health_endpoint_unauthenticated(app_with_token):
    """Health check must work without auth - Railway needs it."""
    with TestClient(app_with_token) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"


def test_mcp_endpoint_rejects_missing_auth(app_with_token):
    with TestClient(app_with_token) as client:
        r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "Bearer" in r.json()["error"]


def test_mcp_endpoint_rejects_wrong_token(app_with_token):
    with TestClient(app_with_token) as client:
        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert r.status_code == 401


def test_mcp_endpoint_rejects_non_bearer_scheme(app_with_token):
    with TestClient(app_with_token) as client:
        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
    assert r.status_code == 401


def test_missing_token_env_returns_503(app_without_token):
    """If admin forgot to set the env var, fail closed with a clear error."""
    with TestClient(app_without_token) as client:
        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer any-token"},
        )
    assert r.status_code == 503
    assert "misconfigured" in r.json()["error"].lower()


# --- constant_time_eq ---

def test_constant_time_eq_matches():
    assert _constant_time_eq("abc", "abc") is True


def test_constant_time_eq_differs():
    assert _constant_time_eq("abc", "abd") is False


def test_constant_time_eq_length_mismatch():
    assert _constant_time_eq("abc", "abcd") is False
