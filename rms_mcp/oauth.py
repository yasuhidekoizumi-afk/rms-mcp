"""Minimal OAuth 2.0 + PKCE implementation for Claude.ai MCP connector.

This is intentionally simple — designed for a 2-3 person internal team:
- Single shared passcode (RMS_MCP_OAUTH_PASSCODE) gates the authorize page.
- Dynamic Client Registration accepts any client (no whitelist).
- Tokens stored in memory (lost on restart; users re-authorize).
- Access tokens are long-lived (30 days) since this is internal.

For wider deployment, replace the in-memory store with Redis and add a
proper login flow.
"""
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

# Token lifetimes
ACCESS_TOKEN_TTL = 30 * 24 * 3600   # 30 days
AUTH_CODE_TTL = 600                 # 10 minutes


@dataclass
class AuthCode:
    code: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    expires_at: float
    used: bool = False


@dataclass
class AccessToken:
    token: str
    client_id: str
    scope: str
    expires_at: float


@dataclass
class OAuthStore:
    """In-memory storage. Resets on server restart."""
    clients: dict[str, dict] = field(default_factory=dict)
    auth_codes: dict[str, AuthCode] = field(default_factory=dict)
    access_tokens: dict[str, AccessToken] = field(default_factory=dict)


# Single process-wide store
_store = OAuthStore()


def get_store() -> OAuthStore:
    return _store


def _passcode() -> str | None:
    return os.environ.get("RMS_MCP_OAUTH_PASSCODE")


def _resource_base_url(request: Request) -> str:
    """Build the canonical base URL (https://host) for this server.

    Honors X-Forwarded-Proto / X-Forwarded-Host so it works behind Railway's
    edge proxy.
    """
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    return f"{proto}://{host}"


# ---- Metadata endpoints ----

async def oauth_authorization_server_metadata(request: Request) -> JSONResponse:
    """RFC 8414: OAuth 2.0 Authorization Server Metadata."""
    base = _resource_base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],  # PKCE public clients
        "scopes_supported": ["mcp"],
    })


async def oauth_protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728: OAuth 2.0 Protected Resource Metadata.

    Tells MCP clients where the authorization server lives.
    """
    base = _resource_base_url(request)
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    })


# ---- Dynamic Client Registration ----

async def register_client(request: Request) -> JSONResponse:
    """RFC 7591: Dynamic Client Registration.

    We accept any registration request — for internal use, the passcode at
    /oauth/authorize is the real gate, not client identity.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            {"error": "invalid_redirect_uri",
             "error_description": "redirect_uris is required"},
            status_code=400,
        )

    client_id = f"client_{secrets.token_urlsafe(16)}"
    client_record = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": body.get("client_name", "mcp-client"),
    }
    _store.clients[client_id] = client_record
    return JSONResponse(client_record, status_code=201)


# ---- Authorize endpoint ----

_AUTHORIZE_FORM = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<title>rms-mcp authorize</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;
     max-width:420px;margin:80px auto;padding:0 20px;color:#222;}}
h1{{font-size:20px;}}
.box{{border:1px solid #ddd;border-radius:8px;padding:20px;}}
input[type=password]{{width:100%;padding:10px;font-size:16px;
     border:1px solid #ccc;border-radius:6px;box-sizing:border-box;}}
button{{margin-top:12px;width:100%;padding:10px;font-size:16px;
     background:#225533;color:#fff;border:0;border-radius:6px;cursor:pointer;}}
.err{{color:#b33;margin-top:10px;}}
.meta{{color:#666;font-size:13px;margin-top:14px;}}
</style></head>
<body>
<h1>rms-mcp に接続を許可</h1>
<div class="box">
  <p>Claude（{client_name}）から接続要求があります。</p>
  <form method="post" action="/oauth/authorize">
    <input type="hidden" name="state_token" value="{state_token}">
    <input type="password" name="passcode" placeholder="共有パスコード" autofocus required>
    <button type="submit">許可する</button>
  </form>
  {error_html}
  <p class="meta">redirect_uri: {redirect_uri}</p>
</div>
</body></html>
"""


# Short-lived in-memory map of state_token -> pending auth request
_pending_auth: dict[str, dict[str, Any]] = {}


async def authorize_get(request: Request) -> HTMLResponse:
    """Show passcode form."""
    params = request.query_params
    client_id = params.get("client_id", "")
    redirect_uri = params.get("redirect_uri", "")
    response_type = params.get("response_type", "")
    state = params.get("state", "")
    code_challenge = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "")
    scope = params.get("scope", "mcp")

    if response_type != "code":
        return HTMLResponse("unsupported_response_type", status_code=400)
    if not code_challenge or code_challenge_method != "S256":
        return HTMLResponse("PKCE S256 required", status_code=400)
    client = _store.clients.get(client_id)
    if not client:
        return HTMLResponse("unknown client_id", status_code=400)
    if redirect_uri not in client["redirect_uris"]:
        return HTMLResponse("redirect_uri not registered", status_code=400)

    state_token = secrets.token_urlsafe(24)
    _pending_auth[state_token] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "expires_at": time.time() + 600,
    }
    return HTMLResponse(_AUTHORIZE_FORM.format(
        client_name=client.get("client_name", "Claude"),
        state_token=state_token,
        redirect_uri=redirect_uri,
        error_html="",
    ))


async def authorize_post(request: Request) -> HTMLResponse | RedirectResponse:
    """Validate passcode and issue authorization code."""
    form = await request.form()
    state_token = form.get("state_token", "")
    passcode = form.get("passcode", "")

    pending = _pending_auth.get(state_token)
    if not pending or pending["expires_at"] < time.time():
        return HTMLResponse("Session expired. Please retry from Claude.ai.", status_code=400)

    server_pass = _passcode()
    if not server_pass:
        return HTMLResponse(
            "Server misconfigured: RMS_MCP_OAUTH_PASSCODE not set", status_code=503
        )
    if not _constant_time_eq(passcode, server_pass):
        # Re-render with error
        client = _store.clients.get(pending["client_id"], {})
        return HTMLResponse(_AUTHORIZE_FORM.format(
            client_name=client.get("client_name", "Claude"),
            state_token=state_token,
            redirect_uri=pending["redirect_uri"],
            error_html='<p class="err">パスコードが違います</p>',
        ), status_code=401)

    # Issue auth code
    code = secrets.token_urlsafe(32)
    _store.auth_codes[code] = AuthCode(
        code=code,
        client_id=pending["client_id"],
        redirect_uri=pending["redirect_uri"],
        code_challenge=pending["code_challenge"],
        code_challenge_method=pending["code_challenge_method"],
        scope=pending["scope"],
        expires_at=time.time() + AUTH_CODE_TTL,
    )
    del _pending_auth[state_token]

    q = {"code": code}
    if pending["state"]:
        q["state"] = pending["state"]
    redirect_url = f"{pending['redirect_uri']}?{urlencode(q)}"
    return RedirectResponse(redirect_url, status_code=302)


# ---- Token endpoint ----

async def token_endpoint(request: Request) -> JSONResponse:
    """Exchange authorization code for access token (PKCE)."""
    form = await request.form()
    grant_type = form.get("grant_type", "")
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    code = form.get("code", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id = form.get("client_id", "")
    code_verifier = form.get("code_verifier", "")

    ac = _store.auth_codes.get(code)
    if not ac or ac.used or ac.expires_at < time.time():
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if ac.client_id != client_id or ac.redirect_uri != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    # Verify PKCE
    verifier_hash = hashlib.sha256(code_verifier.encode("ascii")).digest()
    import base64
    expected = base64.urlsafe_b64encode(verifier_hash).rstrip(b"=").decode("ascii")
    if not _constant_time_eq(expected, ac.code_challenge):
        return JSONResponse({"error": "invalid_grant",
                             "error_description": "PKCE verification failed"},
                            status_code=400)

    ac.used = True
    token = secrets.token_urlsafe(32)
    _store.access_tokens[token] = AccessToken(
        token=token,
        client_id=client_id,
        scope=ac.scope,
        expires_at=time.time() + ACCESS_TOKEN_TTL,
    )
    return JSONResponse({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "scope": ac.scope,
    })


# ---- Bearer validation helper ----

def validate_bearer(token: str) -> bool:
    rec = _store.access_tokens.get(token)
    if not rec:
        return False
    if rec.expires_at < time.time():
        del _store.access_tokens[token]
        return False
    return True


# ---- Utility ----

def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def reset_store_for_tests() -> None:
    """Test helper: wipe in-memory state."""
    _store.clients.clear()
    _store.auth_codes.clear()
    _store.access_tokens.clear()
    _pending_auth.clear()
