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
from rms_mcp.order_api import OrderAPI, PurchaseItemAPI, ACTIVE_PROGRESS

JST = ZoneInfo("Asia/Tokyo")
server = Server("rms-mcp")


def _get_clients():
    ss = os.environ.get("RMS_SERVICE_SECRET", "")
    lk = os.environ.get("RMS_LICENSE_KEY", "")
    if not ss or not lk:
        raise RuntimeError("Set RMS_SERVICE_SECRET and RMS_LICENSE_KEY env vars")
    c = RMSClient(ss, lk)
    return c, OrderAPI(c), PurchaseItemAPI(c)


def _now() -> datetime:
    return datetime.now(JST)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="rms_daily_sales", description="Daily sales summary (orders, revenue, tax, coupons, delivery)",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD (default: 7 days ago)"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
                 "date_type": {"type": "integer", "description": "1=注文日 3=注文確定日", "default": 1},
             }}),
        Tool(name="rms_product_ranking", description="Product sales ranking by revenue",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD (default: 30 days ago)"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
                 "top_n": {"type": "integer", "description": "Top N (default: 20)", "default": 20},
             }}),
        Tool(name="rms_order_detail", description="Full order detail by order number(s)",
             inputSchema={"type": "object", "properties": {
                 "order_numbers": {"type": "array", "items": {"type": "string"},
                                   "description": "Order numbers"},
             }, "required": ["order_numbers"]}),
        Tool(name="rms_cancel_rate", description="Cancellation rate and counts",
             inputSchema={"type": "object", "properties": {
                 "start_date": {"type": "string", "description": "YYYY-MM-DD (default: 30 days ago)"},
                 "end_date": {"type": "string", "description": "YYYY-MM-DD (default: today)"},
             }}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    c, oa, ia = _get_clients()
    try:
        if name == "rms_daily_sales":
            return await _daily_sales(arguments, oa)
        elif name == "rms_product_ranking":
            return await _product_ranking(arguments, ia)
        elif name == "rms_order_detail":
            return await _order_detail(arguments, oa)
        elif name == "rms_cancel_rate":
            return await _cancel_rate(arguments, oa)
        return [TextContent(type="text", text=f"Unknown: {name}")]
    finally:
        c.close()


async def _daily_sales(args: dict, api: OrderAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=7)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    dt = args.get("date_type", 1)
    end_eod = end.replace(hour=23, minute=59, second=59)

    r = api.search_orders(_iso(start), _iso(end_eod), date_type=dt, progress_list=ACTIVE_PROGRESS)
    nums = r.get("orderNumberList", [])
    if not nums:
        return [TextContent(type="text", text="No orders found.")]

    daily: dict[str, dict] = defaultdict(lambda: {"o": 0, "rev": 0, "tax": 0, "cs": 0, "co": 0, "dlv": 0})
    for i in range(0, len(nums), 50):
        detail = api.get_order(nums[i:i+50])
        for o in detail.get("OrderModelList", []):
            d = o.get("orderDatetime", "")[:10]
            daily[d]["o"] += 1
            daily[d]["rev"] += o.get("totalPrice", 0)
            daily[d]["tax"] += o.get("goodsTax", 0)
            daily[d]["cs"] += o.get("couponShopPrice", 0)
            daily[d]["co"] += o.get("couponOtherPrice", 0)
            daily[d]["dlv"] += o.get("deliveryPrice", 0)

    lines = [f"# RMS Daily Sales: {start.date()} ~ {end.date()}\n| Date | Orders | Revenue | Tax | Shop Coupon | Delivery |\n|---|---|---|---|---|---|"]
    gt, go = 0, 0
    for day in sorted(daily):
        d = daily[day]
        gt += d["rev"]; go += d["o"]
        lines.append(f"| {day} | {d['o']}件 | ¥{d['rev']:,} | ¥{d['tax']:,} | ¥{d['cs']:,} | ¥{d['dlv']:,} |")
    avg = gt // go if go else 0
    lines.append(f"\n**Total**: {go} orders, ¥{gt:,}, avg ¥{avg:,}")
    return [TextContent(type="text", text="\n".join(lines))]


async def _product_ranking(args: dict, api: PurchaseItemAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=30)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    top_n = args.get("top_n", 20)
    end_eod = end.replace(hour=23, minute=59, second=59)

    r = api.search_order_items(_iso(start), _iso(end_eod), progress_list=ACTIVE_PROGRESS, limit=1000)
    items = r.get("orderItemList", r.get("OrderItemList", []))
    if not items:
        return [TextContent(type="text", text="No items found.")]

    ps: dict[str, dict] = defaultdict(lambda: {"n": "", "q": 0, "r": 0})
    for it in items:
        nm = it.get("itemName", "?")
        qty = it.get("units", 0)
        pr = it.get("unitPrice", it.get("price", 0))
        key = f"{it.get('itemNumber','')}:{nm}"
        ps[key]["n"] = nm
        ps[key]["q"] += qty
        ps[key]["r"] += qty * pr

    ranked = sorted(ps.items(), key=lambda x: x[1]["r"], reverse=True)[:top_n]
    lines = [f"# RMS Product Ranking: {start.date()} ~ {end.date()}\n| # | Product | Qty | Revenue | Avg |\n|---|---|---|---|---|"]
    for i, (_, s) in enumerate(ranked, 1):
        avg = s["r"] // s["q"] if s["q"] else 0
        lines.append(f"| {i} | {s['n']} | {s['q']} | ¥{s['r']:,} | ¥{avg:,} |")
    return [TextContent(type="text", text="\n".join(lines))]


async def _order_detail(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.get_order(args["order_numbers"])
    return [TextContent(type="text", text=json.dumps(r, ensure_ascii=False, indent=2))]


async def _cancel_rate(args: dict, api: OrderAPI) -> list[TextContent]:
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=30)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end_eod = end.replace(hour=23, minute=59, second=59)

    all_r = api.search_orders(_iso(start), _iso(end_eod), date_type=1)
    total = len(all_r.get("orderNumberList", []))
    cancel_r = api.search_orders(_iso(start), _iso(end_eod), date_type=1, progress_list=[800, 900])
    cancelled = len(cancel_r.get("orderNumberList", []))
    rate = (cancelled / total * 100) if total else 0
    return [TextContent(type="text", text=f"# RMS Cancel Rate: {start.date()} ~ {end.date()}\n- Total: {total}\n- Cancelled: {cancelled}\n- Rate: {rate:.1f}%")]


def main():
    import asyncio
    asyncio.run(_run())


async def _run():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    main()
