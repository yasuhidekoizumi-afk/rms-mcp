"""RPP広告 自動分析 → Slackレポート生成.

毎朝のRPP CSVからROAS・費用対効果を分析し、
予算配分の推奨を生成する。
"""
import csv
import glob
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPORTS_DIR = Path.home() / "Downloads" / "rms-reports"


def parse_rpp_csv(csv_path: str) -> dict[str, Any]:
    """RPP CSVをパースして主要KPIを抽出.

    Returns:
        {
            "period": "2026-07-11〜2026-07-17",
            "clicks": 3786,
            "cost": 90961,
            "sales_720h": 229834,
            "orders_720h": 65,
            "roas_720h": 252.67,
            "cvr_720h": 1.71,
            "cpa_720h": 1400,
        }
    """
    result: dict[str, Any] = {}
    with open(csv_path, "r", encoding="shift_jis", errors="replace") as f:
        reader = csv.reader(f)
        headers = None
        for row in reader:
            if not row:
                continue
            if row[0] == "日付":
                headers = row
                continue
            if headers and row[0].startswith("2026"):
                result["period"] = row[0]
                # ヘッダー名→列indexのマッピング
                col = {h: i for i, h in enumerate(headers)}
                for key, idx_key in [
                    ("clicks", "クリック数(合計)"),
                    ("cost", "実績額(合計)"),
                    ("sales_720h", "売上金額(合計720時間)"),
                    ("orders_720h", "売上件数(合計720時間)"),
                    ("roas_720h", "ROAS(合計720時間)(%)"),
                    ("cvr_720h", "CVR(合計720時間)(%)"),
                    ("cpa_720h", "注文獲得単価(合計720時間)"),
                    ("sales_12h", "売上金額(合計12時間)"),
                    ("orders_12h", "売上件数(合計12時間)"),
                    ("roas_12h", "ROAS(合計12時間)(%)"),
                ]:
                    if idx_key in col:
                        val = row[col[idx_key]].replace(",", "")
                        result[key] = int(val) if key in ("clicks", "cost", "sales_720h", "orders_720h", "sales_12h", "orders_12h", "cpa_720h") else float(val)
                break
    return result


def compare_with_previous(current: dict, previous_path: str | None) -> str:
    """前期比を計算して傾向を返す."""
    if not previous_path:
        return "（比較データなし）"

    prev = parse_rpp_csv(previous_path)
    cr = current.get("sales_720h", 0)
    pr = prev.get("sales_720h", 1)
    change = ((cr - pr) / pr * 100) if pr else 0

    arrow = "📈" if change > 5 else "📉" if change < -5 else "➡️"
    return f"売上前期比: {arrow} {change:+.0f}%"


def generate_insights(data: dict) -> list[str]:
    """データから自動インサイトを生成."""
    insights = []

    roas = data.get("roas_720h", 0)
    if roas > 300:
        insights.append("✅ ROASが300%超。広告予算を増やせる余地あり")
    elif roas > 150:
        insights.append("✅ ROAS良好。現状維持推奨")
    elif roas > 100:
        insights.append("⚠️ ROASが100%台。キーワード見直しを検討")
    else:
        insights.append("🔴 ROASが100%未満。広告停止または大幅見直し推奨")

    cpa = data.get("cpa_720h", 0)
    if cpa and cpa > 3000:
        insights.append(f"🔴 注文獲得単価が¥{cpa:,}と高め。効率化が必要")
    elif cpa and cpa < 1000:
        insights.append(f"✅ 注文獲得単価¥{cpa:,}。非常に効率的")

    cvr = data.get("cvr_720h", 0)
    if cvr < 1.0:
        insights.append(f"⚠️ CVR（購入率）{cvr}%。商品ページの改善余地あり")

    return insights


def build_slack_report() -> str:
    """Slack投稿用のRPP分析レポートを生成."""
    files = sorted(glob.glob(str(REPORTS_DIR / "rpp_*.csv")), reverse=True)
    if not files:
        return "📊 RPPレポート: データ未取得"

    latest = files[0]
    previous = files[1] if len(files) > 1 else None
    data = parse_rpp_csv(latest)

    # 通貨フォーマット
    cost = data.get("cost", 0)
    sales = data.get("sales_720h", 0)
    roas = data.get("roas_720h", 0)
    clicks = data.get("clicks", 0)
    orders = data.get("orders_720h", 0)
    cpa = data.get("cpa_720h", 0)
    period = data.get("period", "?")

    lines = [
        f"📊 *RPP広告レポート* （{period}）",
        "",
        f"• クリック数: {clicks:,}回",
        f"• 広告費: ¥{cost:,}",
        f"• 売上(720h): ¥{sales:,}",
        f"• 注文件数: {orders}件",
        f"• ROAS: {roas:.0f}%",
        f"• 注文獲得単価: ¥{cpa:,}",
    ]

    # 前期比
    if previous:
        prev_data = parse_rpp_csv(previous)
        prev_sales = prev_data.get("sales_720h", 0)
        if prev_sales:
            change = (sales - prev_sales) / prev_sales * 100
            lines.append(f"• 前期比: {change:+.0f}% {'📈' if change>0 else '📉'}")

    # インサイト
    insights = generate_insights(data)
    if insights:
        lines.append("")
        lines.append("*📋 自動分析:*")
        for ins in insights:
            lines.append(f"  {ins}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_slack_report())
