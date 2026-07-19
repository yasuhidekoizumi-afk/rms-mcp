"""レビュー返信 Slack承認パイプライン.

フロー:
1. レビューCSVから要返信レビューを抽出
2. AI返信文面を生成
3. 各レビューをSlackにメッセージとして投稿
4. 人間が ✅ リアクションで承認
5. GHAワークフローが定期的にチェックし、承認済みのものを自動返信

Slack API:
- reactions.get: メッセージのリアクションを取得
- conversations.history: チャンネルのメッセージを取得
"""
import json
import os
import urllib.request
from datetime import datetime
from typing import Any


def _slack_get(path: str, params: dict[str, str] | None = None) -> dict:
    """Slack APIを呼び出す（GET）."""
    token = os.environ.get("SLACK_BOT_TOKEN", "") or os.environ.get("SLACK_XOXP_TOKEN", "")
    if not token:
        return {"ok": False, "error": "no token"}

    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"https://slack.com/api/{path}?{qs}" if qs else f"https://slack.com/api/{path}"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_approved_replies(channel: str, thread_ts: str) -> list[dict]:
    """Slackスレッド内で ✅ リアクションがついたメッセージを取得.

    Returns: [{ts, text, review_index}]  — approved replies
    """
    # スレッド内のメッセージを取得
    result = _slack_get("conversations.replies", {
        "channel": channel,
        "ts": thread_ts,
        "limit": "50",
    })

    if not result.get("ok"):
        print(f"Slack error: {result.get('error')}")
        return []

    approved = []
    for msg in result.get("messages", []):
        reactions = msg.get("reactions", [])
        for r in reactions:
            if r.get("name") in ("white_check_mark", "+1", "ok"):
                # このメッセージの本文からreview_indexとreply_textを抽出
                text = msg.get("text", "")
                # 形式: "#{n}: {review_text}\n```\n{reply_draft}\n```"
                review_index = None
                for line in text.split("\n"):
                    if line.startswith("#") and ":" in line:
                        try:
                            review_index = int(line.split(":")[0].replace("#", "").strip())
                        except:
                            pass
                        break

                approved.append({
                    "ts": msg.get("ts"),
                    "text": text,
                    "review_index": review_index,
                    "reaction": r.get("name"),
                    "approved_by": r.get("users", [])[0] if r.get("users") else "unknown",
                })

    return approved


def post_review_for_approval(channel: str, thread_ts: str | None,
                              review_index: int, review_text: str,
                              reply_draft: str) -> str | None:
    """レビュー返信の下書きをSlackに投稿し、承認を求める.

    Returns: message_ts（投稿したメッセージのタイムスタンプ）、失敗時はNone
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL not set")
        return None

    text = f"#{review_index}: {review_text}\n```\n{reply_draft}\n```\n---\n✅ で承認 / ❌ で却下"

    payload = {
        "text": text,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return datetime.now().isoformat()  # webhook は ts を返さないので現在時刻を使う


def build_review_approval_batch(channel: str) -> dict:
    """レビューCSVを解析し、Slackに一括投稿する.

    Returns: {count: N, thread_ts: "...", reviews: [{index, text, draft}]}
    """
    from rms_mcp.review_auto_reply import parse_reviews_csv, find_needs_reply, generate_reply_draft
    import glob
    from pathlib import Path

    REPORTS_DIR = Path.home() / "Downloads" / "rms-reports"
    files = sorted(glob.glob(str(REPORTS_DIR / "reviews_*.csv")), reverse=True)

    if not files:
        return {"count": 0, "message": "レビューCSVがありません"}

    reviews = parse_reviews_csv(files[0])
    needs = find_needs_reply(reviews)

    if not needs:
        return {"count": 0, "message": "対応が必要なレビューはありません"}

    # タイトル投稿
    title_payload = {
        "text": f"📝 *レビュー返信 承認待ち* （{len(needs)}件）",
    }

    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    urllib.request.Request(
        webhook_url,
        data=json.dumps(title_payload).encode(),
        headers={"Content-Type": "application/json"},
    )

    results = []
    for i, review in enumerate(needs):
        review_text = ""
        for k, v in review.items():
            if any(w in k.lower() for w in ["コメント", "comment", "内容", "本文", "body"]):
                review_text = v[:100]
                break

        draft = generate_reply_draft(review)
        post_review_for_approval(channel, thread_ts=None, review_index=i,
                                 review_text=review_text, reply_draft=draft)
        results.append({"index": i, "text": review_text, "draft": draft[:100]})

    return {"count": len(results), "reviews": results}


if __name__ == "__main__":
    import sys

    if "--post" in sys.argv:
        channel = os.environ.get("SLACK_CHANNEL", "#楽天運用")
        result = build_review_approval_batch(channel)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif "--check" in sys.argv:
        channel = os.environ.get("SLACK_CHANNEL", "#楽天運用")
        # チャンネルIDをchannel_idに変換する必要があるが、
        # conversations.list APIでIDを取得するのが面倒なので、
        # ここでは環境変数 SLACK_CHANNEL_ID があればそれを使う
        channel_id = os.environ.get("SLACK_CHANNEL_ID", "")
        thread_ts = sys.argv[sys.argv.index("--thread") + 1] if "--thread" in sys.argv else ""

        if channel_id and thread_ts:
            approved = get_approved_replies(channel_id, thread_ts)
            print(json.dumps(approved, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"error": "SLACK_CHANNEL_ID and --thread required"}, ensure_ascii=False, indent=2))
