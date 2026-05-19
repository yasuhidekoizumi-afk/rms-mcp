"""Startup smoke test: verify Rakuten RMS credentials work.

Runs once when the HTTP server boots. Logs a clear ✅/❌ marker so admins
can spot credential issues (e.g. I/l typos in licenseKey) immediately
after deploy, instead of discovering them when a user first tries a tool.

Set RMS_MCP_SKIP_SMOKE=1 to disable (useful for tests or air-gapped envs).
"""
import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from rms_mcp.client import RMSClient
from rms_mcp.order_api import OrderAPI

logger = logging.getLogger("rms_mcp.smoke")

JST = ZoneInfo("Asia/Tokyo")


def run_startup_smoke_test() -> bool:
    """Hit Rakuten searchOrder with a 1-minute window to verify auth.

    Returns True on success, False on any failure. Never raises — the server
    must still come up even when Rakuten is temporarily down.
    """
    if os.environ.get("RMS_MCP_SKIP_SMOKE") == "1":
        logger.info("Startup smoke test skipped (RMS_MCP_SKIP_SMOKE=1)")
        return True

    ss = os.environ.get("RMS_SERVICE_SECRET", "")
    lk = os.environ.get("RMS_LICENSE_KEY", "")
    if not ss or not lk:
        logger.error(
            "❌ Startup smoke test: credentials missing. "
            "Set RMS_SERVICE_SECRET and RMS_LICENSE_KEY."
        )
        return False

    # Use a 1-minute window from yesterday 00:00 JST. Minimal API cost.
    now = datetime.now(JST)
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(minutes=1)
    s = start.strftime("%Y-%m-%dT%H:%M:%S+0900")
    e = end.strftime("%Y-%m-%dT%H:%M:%S+0900")

    client = RMSClient(ss, lk, retry_attempts=1)
    try:
        api = OrderAPI(client)
        result = api.search_orders(s, e)
        count = len(result.get("orderNumberList", []))
        logger.info(
            "✅ Startup smoke test passed: Rakuten RMS auth OK "
            "(window %s..%s, %d orders)",
            s, e, count,
        )
        return True
    except Exception as exc:
        # Log details but don't crash — we still want the MCP server up so
        # OAuth callbacks etc. work, and so the admin can read the error.
        logger.error("❌ Startup smoke test FAILED: %s", exc)
        logger.error(
            "Common causes:\n"
            "  - RMS_LICENSE_KEY has an I/l (capital I vs lowercase L) typo\n"
            "    → Open Rakuten RMS console → API設定 and re-copy carefully\n"
            "  - serviceSecret rotated on Rakuten side\n"
            "  - Rakuten API契約 suspended or expired\n"
            "  - Temporary 5xx from Rakuten (will resolve on its own)"
        )
        return False
    finally:
        client.close()
