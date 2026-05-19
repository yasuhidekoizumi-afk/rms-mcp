"""Tests for HTTP transport: OAuth flow, bearer enforcement, health."""
import base64
import hashlib
import os
import secrets
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from rms_mcp import oauth
from rms_mcp.http_app import build_app
from rms_mcp.server import server as mcp_server


PASSCODE = "test-passcode-12345"


@pytest.fixture(autouse=True)
def _reset_oauth_state():
    oauth.reset_store_for_tests()
    yield
    oauth.reset_store_for_tests()


@pytest.fixture
def app():
    with patch.dict(os.environ, {"RMS_MCP_OAUTH_PASSCODE": PASSCODE}):
        yield build_app(mcp_server)


def _pkce_pair() -> tuple[str, str]:
    """Generate (verifier, challenge) for PKCE S256."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---- Health ----

def test_health_unauthenticated(app):
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.text == "ok"


# ---- Metadata ----

def test_authorization_server_metadata(app):
    with TestClient(app) as client:
        r = client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    j = r.json()
    assert j["authorization_endpoint"].endswith("/oauth/authorize")
    assert j["token_endpoint"].endswith("/oauth/token")
    assert j["registration_endpoint"].endswith("/oauth/register")
    assert "S256" in j["code_challenge_methods_supported"]


def test_protected_resource_metadata(app):
    with TestClient(app) as client:
        r = client.get("/.well-known/oauth-protected-resource")
    assert r.status_code == 200
    assert r.json()["bearer_methods_supported"] == ["header"]


# ---- Registration ----

def test_register_client_creates_id(app):
    with TestClient(app) as client:
        r = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
            "client_name": "Test",
        })
    assert r.status_code == 201
    body = r.json()
    assert body["client_id"].startswith("client_")
    assert body["redirect_uris"] == ["https://example.com/cb"]


def test_register_client_rejects_missing_redirect(app):
    with TestClient(app) as client:
        r = client.post("/oauth/register", json={"client_name": "Test"})
    assert r.status_code == 400


# ---- Authorize GET ----

def test_authorize_get_shows_form(app):
    with TestClient(app) as client:
        reg = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
        }).json()
        verifier, challenge = _pkce_pair()
        r = client.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        })
    assert r.status_code == 200
    assert "passcode" in r.text.lower()


def test_authorize_rejects_non_pkce(app):
    with TestClient(app) as client:
        reg = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
        }).json()
        r = client.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
        })
    assert r.status_code == 400


# ---- Full flow ----

def _full_flow(client) -> tuple[str, str]:
    """Helper: run the full OAuth flow and return (access_token, verifier)."""
    reg = client.post("/oauth/register", json={
        "redirect_uris": ["https://example.com/cb"],
    }).json()
    verifier, challenge = _pkce_pair()

    # Authorize GET → get a state_token by parsing HTML
    r = client.get("/oauth/authorize", params={
        "client_id": reg["client_id"],
        "redirect_uri": "https://example.com/cb",
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
    })
    assert r.status_code == 200
    # Extract state_token from the hidden input
    import re
    m = re.search(r'name="state_token" value="([^"]+)"', r.text)
    assert m, "state_token not found in form"
    state_token = m.group(1)

    # Submit passcode
    r = client.post("/oauth/authorize", data={
        "state_token": state_token,
        "passcode": PASSCODE,
    }, follow_redirects=False)
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://example.com/cb?")
    # Extract code
    from urllib.parse import urlparse, parse_qs
    code = parse_qs(urlparse(location).query)["code"][0]

    # Exchange for token
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "https://example.com/cb",
        "client_id": reg["client_id"],
        "code_verifier": verifier,
    })
    assert r.status_code == 200
    return r.json()["access_token"], verifier


def test_full_oauth_flow(app):
    with TestClient(app) as client:
        token, _ = _full_flow(client)
        # Token should validate
        assert oauth.validate_bearer(token)


def test_authorize_wrong_passcode(app):
    with TestClient(app) as client:
        reg = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
        }).json()
        _, challenge = _pkce_pair()
        r = client.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        import re
        state_token = re.search(r'name="state_token" value="([^"]+)"', r.text).group(1)

        r = client.post("/oauth/authorize", data={
            "state_token": state_token,
            "passcode": "wrong",
        })
    assert r.status_code == 401


def test_token_pkce_mismatch(app):
    with TestClient(app) as client:
        reg = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
        }).json()
        _, challenge = _pkce_pair()
        r = client.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        import re
        state_token = re.search(r'name="state_token" value="([^"]+)"', r.text).group(1)
        r = client.post("/oauth/authorize", data={
            "state_token": state_token,
            "passcode": PASSCODE,
        }, follow_redirects=False)
        from urllib.parse import urlparse, parse_qs
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

        # Wrong verifier
        r = client.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://example.com/cb",
            "client_id": reg["client_id"],
            "code_verifier": "wrong-verifier-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        })
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_grant"


def test_auth_code_single_use(app):
    """An auth code can only be exchanged once."""
    with TestClient(app) as client:
        reg = client.post("/oauth/register", json={
            "redirect_uris": ["https://example.com/cb"],
        }).json()
        verifier, challenge = _pkce_pair()
        r = client.get("/oauth/authorize", params={
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        import re
        state_token = re.search(r'name="state_token" value="([^"]+)"', r.text).group(1)
        r = client.post("/oauth/authorize", data={
            "state_token": state_token,
            "passcode": PASSCODE,
        }, follow_redirects=False)
        from urllib.parse import urlparse, parse_qs
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

        # First exchange OK
        r1 = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://example.com/cb",
            "client_id": reg["client_id"], "code_verifier": verifier,
        })
        assert r1.status_code == 200
        # Second exchange fails
        r2 = client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": "https://example.com/cb",
            "client_id": reg["client_id"], "code_verifier": verifier,
        })
    assert r2.status_code == 400


# ---- Bearer enforcement on /mcp ----

def test_mcp_rejects_missing_auth(app):
    with TestClient(app) as client:
        r = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "Bearer" in r.headers.get("www-authenticate", "")


def test_mcp_rejects_invalid_token(app):
    with TestClient(app) as client:
        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer fake-token"},
        )
    assert r.status_code == 401


def test_mcp_accepts_valid_token(app):
    with TestClient(app) as client:
        token, _ = _full_flow(client)
        r = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26",
                             "capabilities": {},
                             "clientInfo": {"name": "test", "version": "1"}}},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert r.status_code == 200
