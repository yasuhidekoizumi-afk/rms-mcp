"""RMS MCP Server - Rakuten sales dashboard."""
import os
import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from rms_mcp.client import RMSClient
from rms_mcp.order_api import OrderAPI, ACTIVE_PROGRESS

JST = ZoneInfo("Asia/Tokyo")
server = Server("rms-mcp")


def _get_clients():
    ss = os.environ.get("RMS_SERVICE_SECRET", "")
    lk = os.environ.get("RMS_LICENSE_KEY", "")
    if not ss or not lk:
        raise RuntimeError("Set RMS_SERVICE_SECRET and RMS_LICENSE_KEY env vars")
    c = RMSClient(ss, lk)
    return c, OrderAPI(c)


def _now() -> datetime:
    return datetime.now(JST)


def _to_rms(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S+0900")


def _i(v) -> int:
    """Coerce None / missing values to 0 for numeric summing."""
    return int(v) if v else 0


def _fetch_all_orders(api: OrderAPI, start: datetime, end: datetime, progress: list[int] | None) -> list[dict]:
    r = api.search_orders(_to_rms(start), _to_rms(end), date_type=1, progress_list=progress)
    nums = r.get("orderNumberList", [])
    if not nums:
        return []
    return api.get_order(nums).get("OrderModelList", [])


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="rms_daily_sales", description="Daily sales summary (orders, revenue, tax, coupons, delivery)",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD"},
             }}),
        Tool(name="rms_product_ranking", description="Product sales ranking by revenue (from PackageModelList)",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                 "top_n": {"type": "integer", "description": "Top N", "default": 20},
             }}),
        Tool(name="rms_order_detail", description="Full order detail by order number(s)",
             inputSchema={"type": "object", "properties": {
                 "order_numbers": {"type": "array", "items": {"type": "string"}},
             }, "required": ["order_numbers"]}),
        Tool(name="rms_cancel_rate", description="Cancellation rate and counts",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD"},
             }}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    c, api = _get_clients()
    try:
        if name == "rms_daily_sales":
            return await _daily_sales(arguments, api)
        elif name == "rms_product_ranking":
            return await _product_ranking(arguments, api)
        elif name == "rms_order_detail":
            return await _order_detail(arguments, api)
        elif name == "rms_cancel_rate":
            return await _cancel_rate(arguments, api)
        return [TextContent(type="text", text=f"Unknown: {name}")]
    finally:
        c.close()


async def _daily_sales(args: dict, api: OrderAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=7)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end = end.replace(hour=23, minute=59, second=59)

    orders = _fetch_all_orders(api, start, end, ACTIVE_PROGRESS)
    if not orders:
        return [TextContent(type="text", text="No orders found.")]

    daily: dict[str, dict] = defaultdict(lambda: {"o": 0, "rev": 0, "tax": 0, "cs": 0, "co": 0, "dlv": 0})
    for o in orders:
        d = o.get("orderDatetime", "")[:10]
        daily[d]["o"] += 1
        daily[d]["rev"] += _i(o.get("totalPrice"))
        daily[d]["tax"] += _i(o.get("goodsTax"))
        daily[d]["cs"] += _i(o.get("couponShopPrice"))
        daily[d]["co"] += _i(o.get("couponOtherPrice"))
        daily[d]["dlv"] += _i(o.get("deliveryPrice"))

    lines = [f"# RMS Daily Sales: {start.date()} ~ {end.date()}\n| Date | Orders | Revenue | Tax | Shop Coupon | Delivery |\n|---|---|---|---|---|---|"]
    gt, go = 0, 0
    for day in sorted(daily):
        d = daily[day]
        gt += d["rev"]; go += d["o"]
        lines.append(f"| {day} | {d['o']}件 | ¥{d['rev']:,} | ¥{d['tax']:,} | ¥{d['cs']:,} | ¥{d['dlv']:,} |")
    avg = gt // go if go else 0
    lines.append(f"\n**Total**: {go} orders, ¥{gt:,}, avg ¥{avg:,}")
    return [TextContent(type="text", text="\n".join(lines))]


async def _product_ranking(args: dict, api: OrderAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=30)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end = end.replace(hour=23, minute=59, second=59)
    top_n = args.get("top_n", 20)

    orders = _fetch_all_orders(api, start, end, ACTIVE_PROGRESS)
    if not orders:
        return [TextContent(type="text", text="No orders found.")]

    # Allocate each order's totalPrice across its items in proportion to
    # qty * unit price. This reflects coupon/point-adjusted realized revenue,
    # which lines up with accounting figures better than raw unit price * qty.
    ps: dict[str, dict] = defaultdict(lambda: {"n": "", "q": 0, "r": 0, "gross": 0})
    for order in orders:
        total = _i(order.get("totalPrice"))
        item_rows: list[tuple[str, str, int, int]] = []
        gross = 0
        for pkg in order.get("PackageModelList", []) or []:
            for item in pkg.get("ItemModelList", []) or []:
                nm = item.get("itemName", "?")
                qty = _i(item.get("units"))
                pr = _i(item.get("price"))
                key = f"{item.get('itemNumber','')}:{nm}"
                line = qty * pr
                item_rows.append((key, nm, qty, line))
                gross += line

        for key, nm, qty, line in item_rows:
            ps[key]["n"] = nm
            ps[key]["q"] += qty
            ps[key]["gross"] += line
            # Pro-rate totalPrice by line share. Fall back to gross when
            # totalPrice or gross is missing (e.g. cancelled orders).
            if total and gross:
                ps[key]["r"] += round(total * line / gross)
            else:
                ps[key]["r"] += line

    ranked = sorted(ps.items(), key=lambda x: x[1]["r"], reverse=True)[:top_n]
    lines = [
        f"# RMS Product Ranking: {start.date()} ~ {end.date()}",
        "Revenue = totalPrice pro-rated across items (coupon/point adjusted).",
        "",
        "| # | Product | Qty | Revenue | Gross (list) | Avg |",
        "|---|---|---|---|---|---|",
    ]
    for i, (_, s) in enumerate(ranked, 1):
        avg = s["r"] // s["q"] if s["q"] else 0
        lines.append(f"| {i} | {s['n']} | {s['q']} | ¥{s['r']:,} | ¥{s['gross']:,} | ¥{avg:,} |")
    return [TextContent(type="text", text="\n".join(lines))]


async def _order_detail(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.get_order(args["order_numbers"])
    return [TextContent(type="text", text=json.dumps(r, ensure_ascii=False, indent=2))]


async def _cancel_rate(args: dict, api: OrderAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=30)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end = end.replace(hour=23, minute=59, second=59)

    all_r = api.search_orders(_to_rms(start), _to_rms(end))
    total = len(all_r.get("orderNumberList", []))
    cancel_r = api.search_orders(_to_rms(start), _to_rms(end), progress_list=[800, 900])
    cancelled = len(cancel_r.get("orderNumberList", []))
    rate = (cancelled / total * 100) if total else 0
    return [TextContent(type="text", text=f"# RMS Cancel Rate: {start.date()} ~ {end.date()}\n- Total: {total}\n- Cancelled: {cancelled}\n- Rate: {rate:.1f}%")]


def main():
    """CLI entry point. Picks transport based on RMS_MCP_TRANSPORT env var.

    - "stdio" (default): local Claude Code use
    - "http": remote Claude.ai use; serves on $PORT (default 8000)
    """
    import asyncio
    transport = os.environ.get("RMS_MCP_TRANSPORT", "stdio").lower()
    if transport == "http":
        _run_http()
    else:
        asyncio.run(_run_stdio())


async def _run_stdio():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def _run_http():
    """Run the MCP server over Streamable HTTP, behind a Bearer-token guard."""
    import logging
    import uvicorn
    from rms_mcp.http_app import build_app
    from rms_mcp.smoke import run_startup_smoke_test

    # Make sure smoke test logs appear before uvicorn takes over the stream.
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s: %(message)s")
    run_startup_smoke_test()

    port = int(os.environ.get("PORT", "8000"))
    app = build_app(server)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
