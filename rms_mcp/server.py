"""RMS MCP Server - Rakuten RMS full-operation dashboard & automation."""
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
from rms_mcp.item_api import ItemAPI
from rms_mcp.inventory_api import InventoryAPI

JST = ZoneInfo("Asia/Tokyo")
server = Server("rms-mcp")


def _get_clients():
    ss = os.environ.get("RMS_SERVICE_SECRET", "")
    lk = os.environ.get("RMS_LICENSE_KEY", "")
    if not ss or not lk:
        raise RuntimeError("Set RMS_SERVICE_SECRET and RMS_LICENSE_KEY env vars")
    c = RMSClient(ss, lk)
    return c, OrderAPI(c), ItemAPI(c), InventoryAPI(c)


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


# ─── Tool definitions ─────────────────────────────────────

TOOLS = [
    # ── 受注: 読み取り ──
    Tool(name="rms_daily_sales", description="日別売上サマリー（件数・税・クーポン・送料）",
         inputSchema={"type": "object", "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD"},
             "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         }}),
    Tool(name="rms_product_ranking", description="商品別ランキング（数量・売上・平均単価）",
         inputSchema={"type": "object", "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD"},
             "end_date": {"type": "string", "description": "YYYY-MM-DD"},
             "top_n": {"type": "integer", "description": "Top N", "default": 20},
         }}),
    Tool(name="rms_order_detail", description="注文番号指定で全詳細JSON",
         inputSchema={"type": "object", "properties": {
             "order_numbers": {"type": "array", "items": {"type": "string"}},
         }, "required": ["order_numbers"]}),
    Tool(name="rms_cancel_rate", description="キャンセル率・件数",
         inputSchema={"type": "object", "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD"},
             "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         }}),

    # ── 受注: 書き込み ──
    Tool(name="rms_confirm_order", description="受注確認（注文確認待ち→楽天処理中へ進める）。バルク対応。",
         inputSchema={"type": "object", "properties": {
             "order_numbers": {"type": "array", "items": {"type": "string"}},
         }, "required": ["order_numbers"]}),
    Tool(name="rms_update_shipping", description="配送情報更新（配送業者・追跡番号の登録）",
         inputSchema={"type": "object", "properties": {
             "payloads": {"type": "array", "items": {
                 "type": "object",
                 "properties": {
                     "orderNumber": {"type": "string"},
                     "shippingList": {"type": "array", "items": {
                         "type": "object",
                         "properties": {
                             "shippingId": {"type": "integer"},
                             "shippingCompanyId": {"type": "integer"},
                             "shippingNumber": {"type": "string"},
                         },
                     }},
                 },
             }},
         }, "required": ["payloads"]}),
    Tool(name="rms_update_sub_status", description="サブステータス更新（出荷準備中・確認済み等）",
         inputSchema={"type": "object", "properties": {
             "order_status_list": {"type": "array", "items": {
                 "type": "object",
                 "properties": {
                     "orderKey": {"type": "object"},
                     "subStatusId": {"type": "integer"},
                 },
             }},
         }, "required": ["order_status_list"]}),
    Tool(name="rms_update_memo", description="注文メモ更新（店舗側内部メモ）",
         inputSchema={"type": "object", "properties": {
             "order_number": {"type": "string"},
             "memo": {"type": "string"},
         }, "required": ["order_number", "memo"]}),
    Tool(name="rms_cancel_order", description="注文キャンセル（発送前）",
         inputSchema={"type": "object", "properties": {
             "order_number": {"type": "string"},
             "cancel_reason": {"type": "integer", "default": 0,
                               "description": "0=その他,1=欠番,2=違犯,3=支払エラー,4=在庫切れ,5=価格ミス,6=客都合"},
         }, "required": ["order_number"]}),
    Tool(name="rms_get_sub_status_list", description="サブステータス一覧取得",
         inputSchema={"type": "object", "properties": {}}),

    # ── 受注: 未確認一覧 ──
    Tool(name="rms_unconfirmed_orders", description="未確認（注文確認待ち）の注文一覧",
         inputSchema={"type": "object", "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD（省略時=今日）"},
             "end_date": {"type": "string", "description": "YYYY-MM-DD（省略時=今日）"},
         }}),
    Tool(name="rms_pending_shipping", description="発送待ち注文一覧（配送番号未登録のもの）",
         inputSchema={"type": "object", "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD"},
             "end_date": {"type": "string", "description": "YYYY-MM-DD"},
         }}),

    # ── 商品 ──
    Tool(name="rms_search_products", description="商品検索（管理番号・商品名・ジャンルで絞り込み可）",
         inputSchema={"type": "object", "properties": {
             "search_type": {"type": "integer", "default": 1},
             "offset": {"type": "integer", "default": 0, "description": "0始まりのオフセット"},
             "item_url": {"type": "string", "description": "管理番号で絞り込み"},
             "genre_id": {"type": "integer"},
         }}),
    Tool(name="rms_all_products", description="全商品の管理番号・商品名・価格の一覧を取得",
         inputSchema={"type": "object", "properties": {
             "include_variants": {"type": "boolean", "default": True},
         }}),

    # ── 在庫 ──
    Tool(name="rms_get_inventory", description="指定商品の在庫情報を取得",
         inputSchema={"type": "object", "properties": {
             "item_urls": {"type": "array", "items": {"type": "string"}},
         }, "required": ["item_urls"]}),
    Tool(name="rms_update_inventory", description="在庫数を更新（バリアント単位、ABSOLUTE絶対値設定）",
         inputSchema={"type": "object", "properties": {
             "updates": {"type": "array", "items": {
                 "type": "object",
                 "properties": {
                     "manageNumber": {"type": "string", "description": "商品管理番号"},
                     "variantId": {"type": "string", "description": "バリアントID"},
                     "quantity": {"type": "integer", "description": "設定する在庫数"},
                 },
                 "required": ["manageNumber", "variantId", "quantity"],
             }},
         }, "required": ["updates"]}),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    c, order_api, item_api, inv_api = _get_clients()
    try:
        if name == "rms_daily_sales":
            return await _daily_sales(arguments, order_api)
        elif name == "rms_product_ranking":
            return await _product_ranking(arguments, order_api)
        elif name == "rms_order_detail":
            return await _order_detail(arguments, order_api)
        elif name == "rms_cancel_rate":
            return await _cancel_rate(arguments, order_api)
        elif name == "rms_confirm_order":
            return await _confirm_order(arguments, order_api)
        elif name == "rms_update_shipping":
            return await _update_shipping(arguments, order_api)
        elif name == "rms_update_sub_status":
            return await _update_sub_status(arguments, order_api)
        elif name == "rms_update_memo":
            return await _update_memo(arguments, order_api)
        elif name == "rms_cancel_order":
            return await _cancel_order(arguments, order_api)
        elif name == "rms_get_sub_status_list":
            return await _get_sub_status_list(arguments, order_api)
        elif name == "rms_unconfirmed_orders":
            return await _unconfirmed_orders(arguments, order_api)
        elif name == "rms_pending_shipping":
            return await _pending_shipping(arguments, order_api)
        elif name == "rms_search_products":
            return await _search_products(arguments, item_api)
        elif name == "rms_all_products":
            return await _all_products(arguments, item_api)
        elif name == "rms_get_inventory":
            return await _get_inventory(arguments, inv_api)
        elif name == "rms_update_inventory":
            return await _update_inventory(arguments, inv_api)
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    finally:
        c.close()


# ─── 受注: 読み取り系 ─────────────────────────────────────

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


# ─── 受注: 書き込み系 ─────────────────────────────────────

async def _confirm_order(args: dict, api: OrderAPI) -> list[TextContent]:
    nums = args["order_numbers"]
    r = api.confirm_order(nums)
    lines = [f"# 受注確認: {len(nums)}件"]
    lines.append(f"```json\n{json.dumps(r, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


async def _update_shipping(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.update_order_shipping(args["payloads"])
    lines = [f"# 配送情報更新: {len(args['payloads'])}件"]
    lines.append(f"```json\n{json.dumps(r, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


async def _update_sub_status(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.update_order_sub_status(args["order_status_list"])
    lines = [f"# サブステータス更新: {len(args['order_status_list'])}件"]
    lines.append(f"```json\n{json.dumps(r, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


async def _update_memo(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.update_order_memo(args["order_number"], args["memo"])
    lines = [f"# メモ更新: {args['order_number']}"]
    lines.append(f"```json\n{json.dumps(r, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


async def _cancel_order(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.cancel_order(args["order_number"], args.get("cancel_reason", 0))
    lines = [f"# キャンセル: {args['order_number']}"]
    lines.append(f"```json\n{json.dumps(r, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


async def _get_sub_status_list(args: dict, api: OrderAPI) -> list[TextContent]:
    r = api.get_sub_status_list()
    return [TextContent(type="text", text=json.dumps(r, ensure_ascii=False, indent=2))]


# ─── 受注: 運用クエリ ─────────────────────────────────────

async def _unconfirmed_orders(args: dict, api: OrderAPI) -> list[TextContent]:
    """注文確認待ち(progress=100)の注文一覧を返す."""
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", now.strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end = end.replace(hour=23, minute=59, second=59)

    r = api.search_orders(_to_rms(start), _to_rms(end), date_type=1, progress_list=[100])
    nums = r.get("orderNumberList", [])
    if not nums:
        return [TextContent(type="text", text="未確認の注文はありません。")]

    orders = api.get_order(nums).get("OrderModelList", [])
    lines = [f"# 未確認注文: {len(orders)}件\n| OrderNumber | Date | Name | Total | Items |\n|---|---|---|---|---|"]
    for o in orders:
        name = o.get("OrdererModel", {}).get("FamilyName", "") + o.get("OrdererModel", {}).get("GivenName", "")
        items = []
        for pkg in o.get("PackageModelList", []) or []:
            for item in pkg.get("ItemModelList", []) or []:
                items.append(f"{item.get('itemName', '?')[:20]}×{item.get('units', 1)}")
        lines.append(f"| {o.get('orderNumber', '')} | {o.get('orderDatetime', '')[:16]} | {name} | ¥{_i(o.get('totalPrice')):,} | {' / '.join(items)[:60]} |")
    return [TextContent(type="text", text="\n".join(lines))]


async def _pending_shipping(args: dict, api: OrderAPI) -> list[TextContent]:
    """発送待ち(progress=300)の注文一覧を返す."""
    now = _now()
    start = datetime.fromisoformat(args.get("start_date", (now - timedelta(days=7)).strftime("%Y-%m-%d")))
    end = datetime.fromisoformat(args.get("end_date", now.strftime("%Y-%m-%d")))
    end = end.replace(hour=23, minute=59, second=59)

    r = api.search_orders(_to_rms(start), _to_rms(end), date_type=1, progress_list=[300])
    nums = r.get("orderNumberList", [])
    if not nums:
        return [TextContent(type="text", text="発送待ちの注文はありません。")]

    orders = api.get_order(nums).get("OrderModelList", [])
    lines = [f"# 発送待ち注文: {len(orders)}件\n| OrderNumber | Date | Name | Total |\n|---|---|---|---|"]
    for o in orders:
        name = o.get("OrdererModel", {}).get("FamilyName", "") + o.get("OrdererModel", {}).get("GivenName", "")
        lines.append(f"| {o.get('orderNumber', '')} | {o.get('orderDatetime', '')[:16]} | {name} | ¥{_i(o.get('totalPrice')):,} |")
    return [TextContent(type="text", text="\n".join(lines))]


# ─── 商品 ──────────────────────────────────────────────────

async def _search_products(args: dict, api: ItemAPI) -> list[TextContent]:
    r = api.search(
        search_type=args.get("search_type", 1),
        offset=args.get("offset", 0),
        item_url=args.get("item_url"),
        genre_id=args.get("genre_id"),
    )
    results = r.get("results", [])
    lines = [f"# 商品検索: {r.get('numFound', 0)}件中 {len(results)}件 (offset {args.get('offset', 0)})\n| manageNumber | itemNumber | title | price |\n|---|---|---|---|"]
    for row in results:
        item = row.get("item", {})
        title = item.get("title", "")[:40]
        price = ""
        variants = item.get("variants", {})
        if variants:
            first_v = list(variants.values())[0] if variants else {}
            price = first_v.get("standardPrice", "")
        lines.append(f"| {item.get('manageNumber', '')} | {item.get('itemNumber', '')[:20]} | {title} | ¥{price} |")
    return [TextContent(type="text", text="\n".join(lines))]


async def _all_products(args: dict, api: ItemAPI) -> list[TextContent]:
    include_variants = args.get("include_variants", True)
    all_items = api.search_all()
    lines = [f"# 全商品: {len(all_items)}件\n| manageNumber | itemNumber | title | variantCount | prices |\n|---|---|---|---|---|"]
    for row in all_items:
        item = row.get("item", {})
        variants = item.get("variants", {})
        prices = []
        if include_variants and variants:
            for vkey, vdata in variants.items():
                p = vdata.get("standardPrice", "")
                if p:
                    prices.append(f"{vkey}:{p}")
        lines.append(f"| {item.get('manageNumber', '')} | {item.get('itemNumber', '')[:20]} | {item.get('title', '')[:40]} | {len(variants)} | {' / '.join(prices)[:60]} |")
    return [TextContent(type="text", text="\n".join(lines))]


# ─── 在庫 ──────────────────────────────────────────────────

async def _get_inventory(args: dict, api: InventoryAPI) -> list[TextContent]:
    """指定商品の全バリアントの在庫を取得."""
    items_to_fetch = args["item_urls"]  # list of manageNumber
    # 全バリアントの在庫をbulk-getで取得
    # manageNumberごとにバリアントIDが必要なので、まず商品情報から取得
    # 簡略化: bulk-getに manageNumber だけ渡す（variantId空）
    inv_list = []
    for mn in items_to_fetch:
        inv_list.append({"manageNumber": mn, "variantId": ""})

    # これは動かない可能性があるので、個別に全バリアントを試す
    lines = ["# 在庫情報\n| manageNumber | variantId | quantity | updated |\n|---|---|---|---|"]
    for mn in items_to_fetch:
        # 商品検索でバリアントIDを取得してから在庫取得
        try:
            c2 = RMSClient(os.environ.get("RMS_SERVICE_SECRET", ""), os.environ.get("RMS_LICENSE_KEY", ""))
            item_api = ItemAPI(c2)
            search_result = item_api.search(item_url=mn)
            results = search_result.get("results", [])
            c2.close()
            if results:
                item = results[0].get("item", {})
                variants = item.get("variants", {})
                for vkey in variants:
                    try:
                        inv = api.get_variant(mn, vkey)
                        lines.append(f"| {mn} | {vkey} | {inv.get('quantity', '?')} | {inv.get('updated', '')[:16]} |")
                    except Exception:
                        lines.append(f"| {mn} | {vkey} | エラー | - |")
            else:
                lines.append(f"| {mn} | - | 商品が見つかりません | - |")
        except Exception as e:
            lines.append(f"| {mn} | - | エラー: {str(e)[:60]} | - |")
    return [TextContent(type="text", text="\n".join(lines))]


async def _update_inventory(args: dict, api: InventoryAPI) -> list[TextContent]:
    """在庫更新の実行.

    args["updates"] の各要素: {manageNumber, variantId, quantity}
    """
    updates = args["updates"]
    inventory_list = []
    for u in updates:
        inventory_list.append({
            "manageNumber": u["manageNumber"],
            "variantId": u["variantId"],
            "mode": "ABSOLUTE",
            "quantity": u["quantity"],
        })
    result = api.bulk_upsert(inventory_list)
    lines = [f"# 在庫更新: {len(updates)}件"]
    lines.append(f"```json\n{json.dumps(result, ensure_ascii=False, indent=2)}\n```")
    return [TextContent(type="text", text="\n".join(lines))]


# ─── Entrypoint ───────────────────────────────────────────

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
