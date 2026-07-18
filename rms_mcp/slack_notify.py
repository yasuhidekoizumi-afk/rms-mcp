"""GHAからSlackに通知を送る共通モジュール.

使い方:
    python -m rms_mcp.slack_notify "在庫同期が完了しました: 15 SKU更新"
    
環境変数:
    SLACK_BOT_TOKEN: Slack Bot User OAuth Token (xoxb-...)
    SLACK_CHANNEL: 投稿先チャンネル (例: #楽天運用)
"""
import json
import os
import sys
import urllib.request


def send_slack(message: str, channel: str | None = None) -> bool:
    """Slackにメッセージを投稿."""
    token = os.environ.get("SLACK_BOT_TOKEN", "") or os.environ.get("SLACK_XOXP_TOKEN", "")
    ch = channel or os.environ.get("SLACK_CHANNEL", "#楽天運用")

    if not token:
        print("SLACK_BOT_TOKEN not set, skipping")
        return False

    payload = {
        "channel": ch,
        "text": message,
        "unfurl_links": False,
    }

    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
        if body.get("ok"):
            print(f"Slack通知: OK ({ch})")
            return True
        print(f"Slack通知: ERROR {body.get('error')}")
        return False


def notify_shipping(result: dict, channel: str | None = None):
    """出荷同期の結果を通知."""
    processed = result.get("processed", 0)
    success = result.get("success", 0)
    failed = result.get("failed", 0)
    skipped = result.get("skipped", 0)

    lines = ["📦 楽天 出荷同期"]
    emoji = "✅" if failed == 0 else "⚠️"
    lines.append(f"{emoji} 成功 {success}件 / 処理 {processed}件 / スキップ {skipped}件")
    if failed:
        errors = result.get("errors", [])
        lines.append(f"❌ 失敗 {failed}件")
        for e in errors[:3]:
            lines.append(f"  • {e.get('orderNumber', '?')}: {e.get('error', '?')[:60]}")

    send_slack("\n".join(lines), channel)


def notify_inventory(result: dict, channel: str | None = None):
    """在庫同期の結果を通知."""
    matched = result.get("matched", 0)
    updated = result.get("updated", 0)
    unmatched = result.get("unmatched_logiless", []) or []

    lines = ["📊 楽天 在庫同期"]
    lines.append(f"✅ マッチ {matched}件 / 更新 {updated}件")
    if unmatched:
        lines.append(f"⚠️ 未マッチ {len(unmatched)}件: {', '.join(unmatched[:5])}")

    send_slack("\n".join(lines), channel)


def notify_daily_summary(shipping: dict, inventory: dict):
    """日次サマリーをSlackに投稿."""
    lines = ["🌅 楽天 日次自動化レポート", ""]

    # 出荷状況
    processed = shipping.get("processed", "?")
    lines.append(f"📦 出荷同期: {processed}件処理")

    # 在庫状況
    matched = inventory.get("matched", "?")
    lines.append(f"📊 在庫同期: {matched} SKUマッチ")

    # 未確認注文の有無は別途クエリが必要（ここでは簡易版）
    lines.append("")
    lines.append("詳細はGitHub Actionsを確認してください")

    send_slack("\n".join(lines))


if __name__ == "__main__":
    if not sys.argv[1:]:
        print("Usage: python -m rms_mcp.slack_notify <message>")
        sys.exit(1)

    message = " ".join(sys.argv[1:])
    send_slack(message)
