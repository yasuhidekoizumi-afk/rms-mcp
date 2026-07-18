"""レビュー返信 半自動化モジュール.

レビューCSVから要返信レビューを抽出し、
AI返信文面の下書きを生成する。
"""
import csv
import glob
from pathlib import Path
from typing import Any

REPORTS_DIR = Path.home() / "Downloads" / "rms-reports"


def parse_reviews_csv(csv_path: str) -> list[dict]:
    """レビューCSVをパース."""
    reviews = []
    with open(csv_path, "r", encoding="cp932", errors="replace") as f:
        reader = csv.reader(f)
        headers = None
        for row in reader:
            if not row:
                continue
            # ヘッダー検出
            if row[0] and ("レビュー" in row[0] or "商品名" in row[0] or "コメント" in row[0]):
                headers = row
                continue
            if headers and len(row) >= len(headers):
                review = {}
                for i, h in enumerate(headers):
                    if i < len(row):
                        review[h.strip()] = row[i].strip()
                reviews.append(review)
    return reviews


def find_needs_reply(reviews: list[dict]) -> list[dict]:
    """返信が必要なレビューを抽出.

    条件:
    - 星1-2の低評価
    - 未返信
    """
    needs = []
    for r in reviews:
        # 返信フィールドが空
        reply_cols = [k for k in r if "返信" in k or "reply" in k.lower()]
        has_reply = any(r.get(k, "") for k in reply_cols)

        if not has_reply:
            # 星評価を探す
            rating = None
            for k, v in r.items():
                if "★" in k or "評価" in k or "rating" in k.lower():
                    try:
                        rating = int(v.replace("★", "").strip() or "0")
                    except:
                        pass

            if rating is not None and rating <= 2:
                r["_priority"] = "high"
                needs.append(r)
            elif rating is not None and rating == 3:
                r["_priority"] = "medium"
                needs.append(r)

    return needs


def generate_reply_draft(review: dict) -> str:
    """レビューへの返信文面を生成.

    レビュー内容に基づいて適切な返信文面のテンプレートを返す。
    AIによる高度な生成は別途モデルが必要。ここではルールベース。
    """
    # レビュー内容を取得
    comment = ""
    for k, v in review.items():
        if any(w in k.lower() for w in ["コメント", "comment", "内容", "本文", "body"]):
            comment = v
            break

    star = review.get("_priority", "")
    product_name = ""
    for k, v in review.items():
        if "商品" in k or "product" in k.lower():
            product_name = v
            break

    if star == "high":
        return f"""この度は「{product_name}」についてご期待に添えず、申し訳ございません。

いただいたご意見を真摯に受け止め、商品改善に活かしてまいります。
よろしければ、改めてご利用いただけますと幸いです。

何かご不明な点がございましたら、お気軽にお問い合わせください。
ORYZAEカスタマーサポート"""

    elif star == "medium":
        return f"""「{product_name}」へのレビューをいただき、ありがとうございます。

貴重なご意見として参考にさせていただきます。
引き続き、より良い商品をお届けできるよう努めてまいります。

ORYZAEカスタマーサポート"""

    # 高評価レビュー
    return f"""「{product_name}」への嬉しいレビューをありがとうございます！

これからもお客様に喜んでいただける商品づくりを続けてまいります。
またのご利用を心よりお待ちしております。

ORYZAEカスタマーサポート"""


def build_slack_review_report() -> str:
    """Slack投稿用のレビュー対応レポートを生成."""
    files = sorted(glob.glob(str(REPORTS_DIR / "reviews_*.csv")), reverse=True)
    if not files:
        return "📝 レビュー: データ未取得"

    reviews = parse_reviews_csv(files[0])
    needs = find_needs_reply(reviews)

    lines = [f"📝 *レビュー返信 要対応* （{len(reviews)}件中{len(needs)}件）", ""]

    if not needs:
        lines.append("✅ 返信が必要なレビューはありません")
        return "\n".join(lines)

    for r in needs[:5]:
        review_text = ""
        for k, v in r.items():
            if any(w in k.lower() for w in ["コメント", "comment", "内容", "本文", "body"]):
                review_text = v[:80]
                break

        draft = generate_reply_draft(r)
        priority_emoji = "🔴" if r.get("_priority") == "high" else "🟡"
        lines.append(f"{priority_emoji} {review_text}")
        lines.append(f"```")
        lines.append(draft[:200])
        lines.append(f"```")
        lines.append("")

    lines.append("---")
    lines.append("返信文面を確認後、RMS画面からコピー＆ペーストで返信してください。")
    lines.append("（自動返信機能は現在開発中）")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_slack_review_report())
