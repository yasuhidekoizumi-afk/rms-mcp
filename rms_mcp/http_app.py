"""Streamable HTTP transport with OAuth 2.0 (PKCE) for Claude.ai connector.

Auth model: OAuth 2.0 Authorization Code + PKCE per MCP spec.
- Claude.ai dynamically registers as a client (/oauth/register).
- User visits /oauth/authorize, enters a shared passcode, gets an auth code.
- Claude.ai exchanges the code for an access token at /oauth/token.
- All /mcp/* requests require Authorization: Bearer <access_token>.

Designed for a 2-3 person internal team. See oauth.py for details.
"""
import contextlib
import os
from collections.abc import AsyncIterator

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from rms_mcp import oauth


# Paths that bypass the bearer check (OAuth flow itself + health).
PUBLIC_PATHS = {
    "/health",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/oauth/register",
    "/oauth/authorize",
    "/oauth/token",
}


class OAuthBearerMiddleware(BaseHTTPMiddleware):
    """Enforce Bearer auth on protected paths; let OAuth/health pass through.

    Also accepts a static API key via RMS_MCP_API_KEY env var for
    simple internal use (no OAuth flow needed).
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/oauth/"):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing Bearer token"},
                status_code=401,
                headers={
                    "WWW-Authenticate":
                        'Bearer resource_metadata="/.well-known/oauth-protected-resource"',
                },
            )
        token = auth[len("Bearer "):].strip()

        # Static API key (for internal non-OAuth clients like Claude Code)
        api_key = os.environ.get("RMS_MCP_API_KEY", "")
        if api_key and token == api_key:
            return await call_next(request)

        if not oauth.validate_bearer(token):
            return JSONResponse(
                {"error": "Invalid or expired token"},
                status_code=401,
                headers={
                    "WWW-Authenticate":
                        'Bearer error="invalid_token", '
                        'resource_metadata="/.well-known/oauth-protected-resource"',
                },
            )
        return await call_next(request)


class McpPathNormalizer:
    """ASGI middleware: rewrite incoming path /mcp → /mcp/ before routing.

    Claude.ai (and some other MCP clients) POST to /mcp without a trailing
    slash. Starlette's default behavior is to 307-redirect to /mcp/, but
    POST redirects strip the Authorization header on most clients, causing
    the OAuth flow to appear successful while the actual MCP call fails.

    Rewriting at the ASGI layer is safer than registering both /mcp and
    /mcp/ mounts, because Starlette's internal routing within the mounted
    sub-app also emits the 307.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/mcp":
            # Mutate the scope path before downstream routing sees it.
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        await self.app(scope, receive, send)


async def _health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app(mcp_server: Server) -> Starlette:
    """Build a Starlette ASGI app that serves the MCP server over HTTP."""
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,
    )

    async def handle_mcp(scope, receive, send):
        await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return Starlette(
        routes=[
            Route("/health", _health, methods=["GET"]),

            # OAuth metadata
            Route("/.well-known/oauth-authorization-server",
                  oauth.oauth_authorization_server_metadata, methods=["GET"]),
            Route("/.well-known/oauth-protected-resource",
                  oauth.oauth_protected_resource_metadata, methods=["GET"]),

            # OAuth flow
            Route("/oauth/register", oauth.register_client, methods=["POST"]),
            Route("/oauth/authorize", oauth.authorize_get, methods=["GET"]),
            Route("/oauth/authorize", oauth.authorize_post, methods=["POST"]),
            Route("/oauth/token", oauth.token_endpoint, methods=["POST"]),

            # MCP
            Mount("/mcp/", app=handle_mcp),
        ],
        middleware=[
            # Path normalizer runs first (outermost), so auth middleware sees
            # the canonical /mcp/ path.
            Middleware(McpPathNormalizer),
            Middleware(OAuthBearerMiddleware),
        ],
        lifespan=lifespan,
    )
