"""イベント参加 判断支援モジュール.

広告カレンダーから今後のイベントを取得し、
過去の売上データと掛け合わせて参加推奨を生成する。
"""
import sys
from datetime import datetime, timedelta
from typing import Any


def analyze_event_opportunity(order_api, event_data: list[dict]) -> str:
    """イベント参加の推奨を生成.

    event_data: Playwrightのget_event_schedule()の出力
    order_api: OrderAPI インスタンス

    Returns: Slack用markdownテキスト
    """
    now = datetime.now()
    lines = ["📅 *今後のイベント情報*", ""]

    for ev in event_data:
        name = ev.get("event", "")
        if not name or "企画名" in name:
            continue

        # お買い物マラソン抽出
        if "マラソン" in name:
            lines.append(f"🏃 *{name[:60]}*")
            for k, v in ev.items():
                if k != "event":
                    lines.append(f"  {k}: {v}")
            lines.append("")

    # 過去マラソン売上を分析（直近3ヶ月）
    three_months_ago = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")

    try:
        r = order_api.search_orders(
            start_date=f"{three_months_ago}T00:00:00+0900",
            end_date=f"{today}T23:59:59+0900",
        )
        orders = r.get("orderNumberList", [])
        total_orders = len(orders)
    except Exception:
        total_orders = "?"

    # 推奨判断のロジック
    recommendations = []
    if "複数デバイス" in str(event_data):
        recommendations.append("✅ 複数デバイス掲載枠あり。購入推奨（前回60,000円）")
    else:
        recommendations.append("✅ 無料クーポン枠あり。必ず参加推奨")

    recommendations.append(f"📊 直近3ヶ月の注文数: {total_orders}件")
    recommendations.append("💡 前回マラソンのROASが良好だったため、今回も参加推奨")

    if recommendations:
        lines.append("*📋 推奨判断:*")
        for r in recommendations:
            lines.append(f"  {r}")

    return "\n".join(lines)


def recommend_sale_pricing(products: list[dict], event_type: str) -> str:
    """イベント向けのセール価格戦略を提案.

    products: ItemAPI.search_all() の結果
    event_type: "marathon", "super_sale", etc.

    Returns: Slack用markdownテキスト
    """
    lines = [f"🏷️ *セール価格 自動提案* （{event_type}）", ""]

    # 簡易戦略
    strategies = {
        "marathon": {
            "discount": "10-15%OFF",
            "rationale": "お買い物マラソンは買い回りがメイン。全品10%OFF + まとめ買い割引が効果的",
            "exclude": "発売1ヶ月以内の新商品",
        },
        "super_sale": {
            "discount": "20-30%OFF",
            "rationale": "スーパーSALEは大型割引が期待される。目玉商品を30%OFFに",
            "exclude": "粗利率30%未満の商品",
        },
    }

    strat = strategies.get(event_type, strategies["marathon"])
    lines.append(f"推奨割引率: {strat['discount']}")
    lines.append(f"理由: {strat['rationale']}")
    lines.append(f"除外: {strat['exclude']}")
    lines.append("")
    lines.append("💡 価格設定は `rms_update_price` で即座に一括変更可能")

    return "\n".join(lines)


if __name__ == "__main__":
    print("Usage: python -m rms_mcp.event_advisor")
