"""Live API smoke test - only runs when RMS credentials are present.

Run with:
    RMS_SERVICE_SECRET=SP... RMS_LICENSE_KEY=SL... uv run pytest tests/test_live_smoke.py
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rms_mcp.client import RMSClient
from rms_mcp.order_api import OrderAPI

JST = ZoneInfo("Asia/Tokyo")

pytestmark = pytest.mark.skipif(
    not (os.environ.get("RMS_SERVICE_SECRET") and os.environ.get("RMS_LICENSE_KEY")),
    reason="RMS credentials not set",
)


def test_search_orders_connects():
    """Smoke test: yesterday's orders should fetch without errors."""
    c = RMSClient(os.environ["RMS_SERVICE_SECRET"], os.environ["RMS_LICENSE_KEY"])
    try:
        now = datetime.now(JST)
        start = (now - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00+0900")
        end = (now - timedelta(days=1)).strftime("%Y-%m-%dT23:59:59+0900")
        r = OrderAPI(c).search_orders(start, end)
        assert "orderNumberList" in r
        # No assertion on count - some days may have 0 orders
    finally:
        c.close()
