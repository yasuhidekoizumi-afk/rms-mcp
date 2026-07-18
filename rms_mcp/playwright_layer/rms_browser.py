"""RMS Playwright 自動化レイヤー.

RMSの画面操作が必要な領域（API非対応）を自動化する:
- イベント申し込み（お買い物マラソン・スーパーSALE等）
- 広告レポートDL（RPP広告のパフォーマンスレポート）
- CSV一括アップロード
- レビュー取得

認証フロー（実動作確認済み）:
1. R-Login: login_id + password → Enterキー送信
2. 楽天会員ログイン: email → Enter
3. パスワード入力 → Enter
4. 「お知らせ」画面 → 「次へ」
5. 「RMSを利用します」クリック
6. RMSメインメニュー到達 → Cookie保存

環境変数:
  RMS_RLOGIN_ID: R-Login ID
  RMS_RLOGIN_PASSWORD: R-Login パスワード
  RMS_USER_EMAIL: 楽天会員メールアドレス
  RMS_USER_PASSWORD: 楽天会員パスワード
"""
import asyncio
import os
import json
from typing import Any
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

RMS_LOGIN_URL = "https://glogin.rms.rakuten.co.jp/?sp_id=1"
RMS_MAIN_URL = "https://mainmenu.rms.rakuten.co.jp/"
RMS_WEB_SERVICE_URL = "https://webservice.rms.rakuten.co.jp/merchant-portal/"
COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"


async def login_to_rms(page: Page) -> bool:
    """RMSにログイン（Playwright + 本物のChrome）.

    headless=False + channel="chrome" で起動すること。
    Cookieは自動保存される。
    """
    env = {
        "rlogin_id": os.environ.get("RMS_RLOGIN_ID", ""),
        "rlogin_pass": os.environ.get("RMS_RLOGIN_PASSWORD", ""),
        "email": os.environ.get("RMS_USER_EMAIL", ""),
        "pass": os.environ.get("RMS_USER_PASSWORD", ""),
    }
    if not all(env.values()):
        raise RuntimeError("RMS_RLOGIN_ID, RMS_RLOGIN_PASSWORD, RMS_USER_EMAIL, RMS_USER_PASSWORD required")

    await page.goto(RMS_LOGIN_URL, wait_until="networkidle", timeout=30000)

    # Step 1: R-Login
    await page.fill('#rlogin-username-ja', env["rlogin_id"])
    await page.fill('#rlogin-password-ja', env["rlogin_pass"])
    await page.press('#rlogin-password-ja', 'Enter')
    await asyncio.sleep(5)

    # Step 2: Email
    if await page.locator('#user_id').count():
        await page.fill('#user_id', env["email"])
        await page.press('#user_id', 'Enter')
        await asyncio.sleep(3)

    # Step 3: Password
    if await page.locator('#password_current').count():
        await page.fill('#password_current', env["pass"])
        await page.press('#password_current', 'Enter')
        await asyncio.sleep(5)

    # Step 4: 「お知らせ」を全て「次へ」
    for _ in range(10):
        if await page.locator('button:has-text("次へ")').count():
            await page.locator('button:has-text("次へ")').first.click()
            await asyncio.sleep(2)
        else:
            break

    # Step 5: 「RMSを利用します」
    rms_btn = page.locator('button:has-text("RMS"), input[value*="RMS"]')
    if await rms_btn.count():
        await rms_btn.first.click()
        await asyncio.sleep(3)

    # 成功チェック
    if "mainmenu.rms" in page.url or await page.locator('[href*="merchant-portal"]').count():
        print("[rms_login] ✅ Login successful")
        return True
    print(f"[rms_login] ⚠️ Login state: {page.url}")
    return False


async def save_cookies(context: BrowserContext, path: Path | None = None):
    """Cookieを保存."""
    path = path or COOKIES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    cookies = await context.cookies()
    path.write_text(json.dumps(cookies, ensure_ascii=False))
    print(f"[rms_login] {len(cookies)} cookies saved to {path}")


async def load_cookies(context: BrowserContext, path: Path | None = None) -> bool:
    """保存したCookieを読み込み."""
    path = path or COOKIES_PATH
    if not path.exists():
        return False
    cookies = json.loads(path.read_text())
    await context.add_cookies(cookies)
    print(f"[rms_login] {len(cookies)} cookies loaded from {path}")
    return True


async def get_rms_page(headless: bool = False) -> tuple:
    """RMSログイン済みのPageを取得（Cookie再利用付き）.

    Returns: (pw, browser, context, page)
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        channel="chrome",
        args=['--disable-blink-features=AutomationControlled']
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="ja-JP",
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()

    # Cookie再利用
    await load_cookies(context)

    # WEB SERVICEに直接アクセス
    await page.goto(RMS_WEB_SERVICE_URL, wait_until="networkidle", timeout=30000)
    if "login" not in page.url.lower():
        print("[rms_login] ✅ Session valid via cookies")
        return pw, browser, context, page

    # Cookie期限切れ → 新規ログイン
    print("[rms_login] Session expired, re-logging in...")
    success = await login_to_rms(page)
    if success:
        await save_cookies(context)
    return pw, browser, context, page


async def navigate_to(page: Page, link_text: str) -> bool:
    """WEB SERVICE内のリンクをクリックして遷移."""
    link = page.locator(f'a:has-text("{link_text}")')
    if await link.count():
        await link.first.click()
        await asyncio.sleep(3)
        return True
    return False


async def health_check(headless: bool = True) -> dict:
    """RMSログイン状態を確認."""
    pw, browser, context, page = await get_rms_page(headless=headless)
    url = page.url
    title = await page.title()
    await browser.close()
    await pw.stop()
    return {"status": "ok" if "login" not in url.lower() else "error", "url": url, "title": title}


if __name__ == "__main__":
    import sys
    if "--health-check" in sys.argv:
        result = asyncio.run(health_check(headless=False))
        print(json.dumps(result, ensure_ascii=False, indent=2))
