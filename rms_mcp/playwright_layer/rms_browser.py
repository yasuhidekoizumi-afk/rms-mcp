"""RMS Playwright 自動化レイヤー.

RMSの画面操作が必要な領域（API非対応）を自動化する:
- イベント申し込み（お買い物マラソン・スーパーSALE等）
- 広告レポートDL（RPP広告のパフォーマンスレポート）
- CSV一括アップロード
- レビュー取得

認証フロー:
1. R-Login（ID + パスワード）
2. 楽天会員ログイン（メール + パスワード）
3. 法令確認画面の突破
4. RMS WEB SERVICE への遷移

環境変数:
  RMS_RLOGIN_ID: R-Login ID
  RMS_RLOGIN_PASSWORD: R-Login パスワード
  RMS_USER_EMAIL: 楽天会員メールアドレス
  RMS_USER_PASSWORD: 楽天会員パスワード
"""
import asyncio
import os
import json
import time
from typing import Any
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

RMS_LOGIN_URL = "https://mainmenu.rms.rakuten.co.jp/rms"
RMS_WEB_SERVICE_URL = "https://webservice.rms.rakuten.co.jp/merchant-portal/"
COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"

# 法令確認画面のボタンが押せない問題への対応:
# JavaScript のフォーム送信を直接実行する


async def login_to_rms(page: Page) -> bool:
    """RMSにログインし、法令確認画面を突破する.

    Returns: True if login successful
    """
    rlogin_id = os.environ.get("RMS_RLOGIN_ID", "")
    rlogin_pass = os.environ.get("RMS_RLOGIN_PASSWORD", "")
    user_email = os.environ.get("RMS_USER_EMAIL", "")
    user_pass = os.environ.get("RMS_USER_PASSWORD", "")

    if not all([rlogin_id, rlogin_pass, user_email, user_pass]):
        raise RuntimeError("RMS_RLOGIN_ID, RMS_RLOGIN_PASSWORD, RMS_USER_EMAIL, RMS_USER_PASSWORD required")

    # Step 1: R-Login
    print("[rms_login] Step 1: R-Login")
    await page.goto(RMS_LOGIN_URL, wait_until="networkidle", timeout=30000)
    await page.fill('input[name="user_id"]', rlogin_id)
    await page.fill('input[name="password"]', rlogin_pass)
    await page.click('input[type="submit"], button[type="submit"]')
    await page.wait_for_load_state("networkidle", timeout=15000)

    # Step 2: 楽天会員ログイン（メールアドレス入力）
    print("[rms_login] Step 2: Rakuten member login (email)")
    try:
        email_input = page.locator('input[type="email"], input[name="user_id"], input[placeholder*="メール"]')
        await email_input.wait_for(timeout=10000)
        await email_input.fill(user_email)
        await page.click('button:has-text("次へ"), input[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # 既にログイン済みの可能性

    # Step 3: パスワード入力
    print("[rms_login] Step 3: Password")
    try:
        pass_input = page.locator('input[type="password"]')
        await pass_input.wait_for(timeout=10000)
        await pass_input.fill(user_pass)
        await page.click('button:has-text("次へ"), input[type="submit"]')
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Step 4: 「お気をつけください」画面の「次へ」
    print("[rms_login] Step 4: Safety notice")
    try:
        next_btn = page.locator('button:has-text("次へ"), input[value="次へ"]')
        if await next_btn.count() > 0:
            await next_btn.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Step 5: 法令確認画面の突破
    print("[rms_login] Step 5: Terms confirmation")
    try:
        rms_btn = page.locator('button:has-text("RMSを利用"), input[value*="RMSを利用"]')
        if await rms_btn.count() > 0:
            # JavaScript のフォーム送信を直接実行
            await page.evaluate('''() => {
                const form = document.querySelector('form');
                if (form) {
                    // フォームの action に直接POST
                    form.submit();
                }
            }''')
            await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        print(f"[rms_login] Step 5 warning: {e}")

    # ログイン成功確認
    url = page.url
    if "mainmenu.rms.rakuten.co.jp" in url and "login_error" not in url:
        print("[rms_login] ✅ Login successful")
        return True
    else:
        print(f"[rms_login] ⚠️ Login state unclear, URL: {url}")
        return "login_error" not in url


async def save_cookies(context: BrowserContext, path: Path | None = None):
    """Cookieを保存（セッション再利用用）."""
    path = path or COOKIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    cookies = await context.cookies()
    path.write_text(json.dumps(cookies))
    print(f"[rms_login] Cookies saved to {path}")


async def load_cookies(context: BrowserContext, path: Path | None = None) -> bool:
    """保存したCookieを読み込み."""
    path = path or COOKIES_PATH
    if not path.exists():
        return False
    cookies = json.loads(path.read_text())
    await context.add_cookies(cookies)
    print(f"[rms_login] Cookies loaded from {path}")
    return True


async def get_rms_page(headless: bool = True):
    """RMSログイン済みのPageを取得（Cookie再利用付き）.

    Returns: (browser, context, page)
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless)

    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )

    # Cookie再利用を試みる
    has_cookies = await load_cookies(context)
    page = await context.new_page()

    if has_cookies:
        await page.goto(RMS_LOGIN_URL, wait_until="networkidle", timeout=20000)
        url = page.url
        if "mainmenu.rms.rakuten.co.jp" in url and "login_error" not in url:
            print("[rms_login] ✅ Session reused via cookies")
            return pw, browser, context, page

    # Cookieがない or 期限切れ → 新規ログイン
    success = await login_to_rms(page)
    if success:
        await save_cookies(context)
    return pw, browser, context, page


async def fetch_ad_report(date_from: str, date_to: str, headless: bool = True) -> dict:
    """RPP広告レポートをDLして解析.

    date_from/date_to: YYYY-MM-DD
    """
    pw, browser, context, page = await get_rms_page(headless=headless)

    try:
        # 広告管理画面へ遷移
        print("[ad_report] Navigating to RPP ad report page...")
        await page.goto(
            "https://mainmenu.rms.rakuten.co.jp/?act=module&module=order%2Forder_search_list",
            wait_until="networkidle",
            timeout=20000,
        )

        # TODO: 広告レポートのDLボタンを特定してクリック
        # RMSの広告管理画面のDOM構造は変更される可能性があるため、
        # 実行時にヘルスチェックを行う

        result = {"status": "requires_manual_check", "url": page.url}
        return result

    finally:
        await browser.close()
        await pw.stop()


async def enter_event(event_name: str, headless: bool = True) -> dict:
    """イベント（お買い物マラソン等）の申し込み.

    event_name: イベント名（お買い物マラソン / スーパーSALE等）
    """
    pw, browser, context, page = await get_rms_page(headless=headless)

    try:
        # RMSのイベント申し込みページへ遷移
        print(f"[event_entry] Entering {event_name}...")
        # TODO: イベント申し込みページのURLとDOMを特定

        result = {"status": "requires_manual_check", "event": event_name}
        return result

    finally:
        await browser.close()
        await pw.stop()


async def health_check(headless: bool = True) -> dict:
    """RMSログインができるかヘルスチェック."""
    pw, browser, context, page = await get_rms_page(headless=headless)

    try:
        url = page.url
        title = await page.title()
        return {
            "status": "ok" if "login_error" not in url else "error",
            "url": url,
            "title": title,
        }
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    import sys

    if "--health-check" in sys.argv:
        result = asyncio.run(health_check(headless=False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--login-test" in sys.argv:
        async def test():
            pw, browser, context, page = await get_rms_page(headless=False)
            url = page.url
            title = await page.title()
            print(f"URL: {url}")
            print(f"Title: {title}")
            await page.screenshot(path="/tmp/rms-login-test.png")
            await browser.close()
            await pw.stop()
        asyncio.run(test())
