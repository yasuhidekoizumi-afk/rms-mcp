"""RMS 残り3機能 自動化モジュール.

1. review_reply: Playwrightでレビュー返信画面にアクセスし、AI文面を投稿
2. event_monitor: イベントカレンダー監視→新規イベント発見→Slackアラート
3. rmail_composer: R-Mail文面の自動生成（配信は手動）
"""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"


async def _make_page(headless: bool = True):
    """Playwrightでブラウザ起動 + Cookie読み込み."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless, channel="chrome" if os.sys.platform == "darwin" else None,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="ja-JP")
    await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    if COOKIES_PATH.exists():
        await context.add_cookies(json.loads(COOKIES_PATH.read_text()))
    page = await context.new_page()
    return pw, browser, context, page


async def post_review_reply(review_url: str, reply_text: str, headless: bool = True) -> dict:
    """レビュー返信を投稿（Playwrightでreview.rms.rakuten.co.jpにアクセス）.

    review_url: レビューの個別URL（review.rms.rakuten.co.jp/item/...）
    reply_text: 返信文面
    """
    pw, browser, context, page = await _make_page(headless)

    try:
        await page.goto(review_url, wait_until="networkidle", timeout=30000)

        # 返信用のテキストエリアを探す
        textarea = page.locator('textarea')
        if await textarea.count() == 0:
            return {"status": "error", "message": "No textarea found for reply"}

        await textarea.fill(reply_text)
        await asyncio.sleep(1)

        # 「返信する」「投稿する」などのボタンをクリック
        btn = page.locator('button:has-text("返信"), button:has-text("投稿"), input[value*="返信"]')
        if await btn.count():
            await btn.click()
            await asyncio.sleep(3)
            return {"status": "ok", "message": "Reply posted"}
        else:
            return {"status": "error", "message": "No submit button found"}
    finally:
        await browser.close()
        await pw.stop()


async def check_new_events(headless: bool = True) -> dict:
    """イベントカレンダーを監視し、新規イベントがあればSlack通知用のメッセージを返す."""
    pw, browser, context, page = await _make_page(headless)
    try:
        await page.goto("https://ad.rms.rakuten.co.jp/ec/calendar", wait_until="networkidle", timeout=30000)

        events_text = await page.text_content('body') or ''
        events = []
        for line in events_text.split('\n'):
            line = line.strip()
            if '【' in line and any(k in line for k in ['マラソン', 'SALE', 'キャンペーン']):
                events.append(line[:120])

        # 8月のイベントがあればアラート
        has_august = any('8月' in line for line in events_text.split('\n') if len(line) < 50)

        return {
            "current_events": events,
            "has_august_events": has_august,
            "timestamp": datetime.now().isoformat(),
        }
    finally:
        await browser.close()
        await pw.stop()


def generate_rmail_draft(topic: str, products: list[str] | None = None) -> str:
    """R-Mail（メルマガ）の文面を自動生成.

    topic: 配信テーマ（例: 'お買い物マラソン', '新商品お知らせ'）
    products: 対象商品リスト
    """
    if topic == 'お買い物マラソン':
        lines = [
            "いつもORYZAEをご利用いただき、ありがとうございます。",
            "",
            "本日より「お買い物マラソン」がスタートしました！",
            "エントリー後、対象ショップでのお買い物でポイント最大10倍！",
            "",
            "【ORYZAEおすすめ商品】",
        ]
        for p in (products or ['米麹グラノーラ', '麹マヨ', 'フルーツ甘酒'])[:3]:
            lines.append(f"・{p}")
        lines += [
            "",
            "期間限定クーポンもご用意しておりますので、",
            "ぜひこの機会にご利用ください！",
            "",
            "▼ ショップはこちら",
            "https://www.rakuten.co.jp/oryzae-foodcosme",
        ]
    elif topic == '新商品':
        product = (products or ['新商品'])[0]
        lines = [
            "ORYZAEから新商品のお知らせです！",
            "",
            f"【{product}】",
            "詳細はショップページでご確認いただけます。",
            "",
            "▼ ショップはこちら",
            "https://www.rakuten.co.jp/oryzae-foodcosme",
        ]
    else:
        lines = [
            "いつもORYZAEをご利用いただき、ありがとうございます。",
            "",
            f"【{topic}】のお知らせです。",
            "",
            "▼ ショップはこちら",
            "https://www.rakuten.co.jp/oryzae-foodcosme",
        ]

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--event-check" in sys.argv:
        result = asyncio.run(check_new_events(headless="--visible" not in sys.argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif "--draft-rmail" in sys.argv:
        topic = "お買い物マラソン"
        draft = generate_rmail_draft(topic)
        print(draft)

    elif "--reply-review" in sys.argv:
        url = sys.argv[sys.argv.index("--reply-review") + 1]
        text = sys.argv[sys.argv.index("--text") + 1] if "--text" in sys.argv else "レビューありがとうございます。"
        result = asyncio.run(post_review_reply(url, text, headless="--visible" not in sys.argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print("Usage:")
        print("  --event-check     : 新規イベント監視")
        print("  --draft-rmail     : メルマガ文面生成")
        print("  --reply-review URL --text 'TEXT' : レビュー返信投稿")
