"""RMS Playwright 運用自動化モジュール — イベント・メルマガ・レビュー.

RMSログイン済みCookieを使って、API未対応の画面操作を自動化する。

使い方:
    python rms_mcp/playwright_layer/rms_ops.py --help
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"


async def _ensure_login(context, page) -> bool:
    """Cookieを使ってログイン状態を確保。"""
    if COOKIES_PATH.exists():
        cookies = json.loads(COOKIES_PATH.read_text())
        await context.add_cookies(cookies)
    await page.goto("https://mainmenu.rms.rakuten.co.jp/", wait_until="networkidle", timeout=30000)
    return "login" not in page.url.lower()


async def get_rpp_campaigns(headless: bool = True) -> dict:
    """RPPキャンペーン一覧取得。"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, channel="chrome",
        args=['--disable-blink-features=AutomationControlled'])
    context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="ja-JP")
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()

    if not await _ensure_login(context, page):
        return {"error": "Login required"}

    await page.goto("https://ad.rms.rakuten.co.jp/rpp/campaigns", wait_until="networkidle", timeout=30000)
    campaigns = await page.evaluate('''() => {
        const table = document.querySelector('table');
        if (!table) return [];
        return Array.from(table.querySelectorAll('tr')).slice(1).map(row => {
            const cells = row.querySelectorAll('td, th');
            return Array.from(cells).map(c => c.textContent?.trim() || '');
        }).filter(r => r.length > 0 && r.some(c => c.length > 0));
    }''')

    await browser.close()
    await pw.stop()
    return {"campaigns": campaigns[:20]}


async def get_reviews(headless: bool = True) -> dict:
    """レビュー一覧取得。"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, channel="chrome",
        args=['--disable-blink-features=AutomationControlled'])
    context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="ja-JP")
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()

    if not await _ensure_login(context, page):
        return {"error": "Login required"}

    # レビュー管理画面（RMSのレビュー一覧）
    await page.goto("https://review.rms.rakuten.co.jp/search/index/", wait_until="networkidle", timeout=30000)
    print(f"Review URL: {page.url}")
    print(f"Review Title: {await page.title()}")

    links = await page.evaluate('''() => Array.from(document.querySelectorAll('a')).map(a => ({
        text: a.textContent?.trim().substring(0, 60),
        href: (a.href || '').substring(0, 150)
    })).filter(l => l.text && l.text.length > 3)''')

    await browser.close()
    await pw.stop()
    return {"reviews": links[:20]}


async def get_event_calendar(headless: bool = True) -> dict:
    """広告イベントカレンダー取得。"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless, channel="chrome",
        args=['--disable-blink-features=AutomationControlled'])
    context = await browser.new_context(viewport={"width": 1280, "height": 800}, locale="ja-JP")
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()

    if not await _ensure_login(context, page):
        return {"error": "Login required"}

    await page.goto("https://ad.rms.rakuten.co.jp/ec/calendar", wait_until="networkidle", timeout=30000)
    links = await page.evaluate('''() => Array.from(document.querySelectorAll('a')).map(a => ({
        text: a.textContent?.trim().substring(0, 80),
        href: (a.href || '').substring(0, 150)
    })).filter(l => l.text && l.text.length > 3)''')

    await browser.close()
    await pw.stop()
    return {"links": links}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    headless = args.headless

    # 3機能を順次実行
    result = {}
    result["campaigns"] = asyncio.run(get_rpp_campaigns(headless=headless))
    result["reviews"] = asyncio.run(get_reviews(headless=headless))
    result["calendar"] = asyncio.run(get_event_calendar(headless=headless))

    print(json.dumps(result, ensure_ascii=False, indent=2))
