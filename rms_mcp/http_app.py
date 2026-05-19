"""Streamable HTTP transport with Bearer-token auth for Claude.ai connector.

Auth model: a single shared secret in RMS_MCP_AUTH_TOKEN.
Clients (Claude.ai connector) send `Authorization: Bearer <token>`.

This is intentionally simple - the server is deployed for a small internal
team (2-3 users). When the user list grows beyond that, replace with OAuth.
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


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without a valid Bearer token.

    The /health endpoint is exempt so platform health checks can hit it.
    """

    def __init__(self, app, token: str | None):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        if not self._token:
            return JSONResponse(
                {"error": "Server misconfigured: RMS_MCP_AUTH_TOKEN unset"},
                status_code=503,
            )

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing Bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="rms-mcp"'},
            )
        presented = auth[len("Bearer "):].strip()
        if not _constant_time_eq(presented, self._token):
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        return await call_next(request)


def _constant_time_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing leaks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


async def _health(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_app(mcp_server: Server) -> Starlette:
    """Build a Starlette ASGI app that serves the MCP server over HTTP."""
    token = os.environ.get("RMS_MCP_AUTH_TOKEN")

    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        stateless=True,  # Each request is independent; no per-session state.
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
            Mount("/mcp", app=handle_mcp),
        ],
        middleware=[Middleware(BearerAuthMiddleware, token=token)],
        lifespan=lifespan,
    )
