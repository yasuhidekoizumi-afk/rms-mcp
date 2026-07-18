"""RPP広告レポート自動DL（Playwright）.

ad.rms.rakuten.co.jp にログインし、RPP広告のパフォーマンスレポートを
CSVダウンロードする。

使い方:
    python rms_mcp/playwright_layer/rpp_reports.py \
        --from-date 2026-07-01 --to-date 2026-07-17 \
        --report-type daily  # daily=日次, item=商品別, keyword=キーワード別
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"
DOWNLOAD_DIR = Path.home() / "Downloads" / "rms-reports"


async def ensure_login(page):
    """Cookieを使ってログイン済み状態にする。"""
    if COOKIES_PATH.exists():
        cookies = json.loads(COOKIES_PATH.read_text())
        context = page.context
        await context.add_cookies(cookies)
        await page.goto("https://ad.rms.rakuten.co.jp/rpp/reports", wait_until="networkidle", timeout=30000)
        if "login" not in page.url.lower():
            return True
    return False


async def download_rpp_report(from_date: str, to_date: str,
                              report_type: str = "daily",
                              headless: bool = True) -> dict:
    """RPP広告レポートをCSVダウンロード.

    report_type: daily(日次サマリー), item(商品別), keyword(キーワード別)
    Returns: {status, file_path, report_type, from_date, to_date}
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        channel="chrome" if sys.platform == "darwin" else None,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
        locale="ja-JP",
        accept_downloads=True,
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    page = await context.new_page()

    # ログイン確認
    if not await ensure_login(page):
        # Cookie切れ → 本来はここで再ログイン
        return {"status": "error", "message": "Session expired, please re-login via rms_browser.py health-check"}

    # 日付設定（React DatePicker経由で値設定）
    await page.evaluate(f'''() => {{
        const inputs = document.querySelectorAll('input[placeholder="Select start"], input[placeholder="Select end"]');
        if (inputs[0]) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inputs[0], '{from_date}');
            inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        if (inputs[1]) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inputs[1], '{to_date}');
            inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[1].dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
    }}''')
    await asyncio.sleep(1)

    # レポート種別に応じたボタンをクリック
    btn_map = {
        "daily": "この条件でダウンロード",
        "item": "全商品レポートダウンロード",
        "keyword": "全キーワードレポートダウンロード",
    }
    btn_text = btn_map.get(report_type, "この条件でダウンロード")
    
    # ダウンロードを開始
    async with page.expect_download(timeout=60000) as download_info:
        await page.click(f'button:has-text("{btn_text}")')
        await asyncio.sleep(2)
    
    download = await download_info.value
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = str(DOWNLOAD_DIR / download.suggested_filename)
    await download.save_as(file_path)

    await browser.close()
    await pw.stop()

    return {
        "status": "ok",
        "file_path": file_path,
        "suggested_filename": download.suggested_filename,
        "report_type": report_type,
        "from_date": from_date,
        "to_date": to_date,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RPP広告レポート自動DL")
    parser.add_argument("--from-date", default=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    parser.add_argument("--to-date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--report-type", default="daily", choices=["daily", "item", "keyword"])
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    
    result = asyncio.run(download_rpp_report(
        args.from_date, args.to_date, args.report_type, headless=not args.headless
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
