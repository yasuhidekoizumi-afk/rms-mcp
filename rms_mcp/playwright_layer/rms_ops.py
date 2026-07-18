"""RMS画面操作モジュール — レビュー・RPP・掲載予定の本格自動化.

全ての関数は Cookie 再利用（rms_browser.py）で認証済み。
"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"
DOWNLOAD_DIR = Path.home() / "Downloads" / "rms-reports"


async def _ensure_login(context, page) -> bool:
    """Cookieでログイン状態を確保。"""
    if COOKIES_PATH.exists():
        cookies = json.loads(COOKIES_PATH.read_text())
        await context.add_cookies(cookies)
    await page.goto("https://mainmenu.rms.rakuten.co.jp/", wait_until="networkidle", timeout=30000)
    return "login" not in page.url.lower()


async def _make_browser(headless: bool = True):
    """Playwrightブラウザを起動（共通処理）. Returns (pw, browser, context, page)."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless, channel="chrome",
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
    if not await _ensure_login(context, page):
        await browser.close()
        await pw.stop()
        return None, None, None, None
    return pw, browser, context, page


async def download_reviews_csv(headless: bool = True) -> str:
    """レビュー一覧をCSVダウンロード.

    Returns: CSVファイルのパス、またはエラーメッセージ
    """
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return "Login failed"

    # レビューページへ
    await page.goto("https://review.rms.rakuten.co.jp/search/index/", wait_until="networkidle", timeout=30000)

    # CSVダウンロードリンクを取得
    csv_url = await page.evaluate('''() => {
        const links = document.querySelectorAll('a');
        for (const a of links) {
            if (a.textContent.trim().includes('CSVダウンロード') || a.href.includes('/csv/')) {
                return a.href;
            }
        }
        return null;
    }''')

    if not csv_url:
        await browser.close()
        await pw.stop()
        return "CSV download link not found"

    # CSVをダウンロード（fetchで直接取得、生成に時間がかかる可能性あり）
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    csv_content = await page.evaluate(f'''async () => {{
        const resp = await fetch('{csv_url}');
        if (!resp.ok) return null;
        return await resp.text();
    }}''')
    if csv_content:
        file_path = str(DOWNLOAD_DIR / f"reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        Path(file_path).write_text(csv_content)
    else:
        # fetchが失敗したらexpect_downloadで通常DL
        async with page.expect_download(timeout=180000) as download_info:
            await page.goto(csv_url, wait_until="domcontentloaded", timeout=120000)
            await asyncio.sleep(10)
        download = await download_info.value
        file_path = str(DOWNLOAD_DIR / f"reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        await download.save_as(file_path)

    await browser.close()
    await pw.stop()
    return file_path


async def set_rpp_budget(budget: int, headless: bool = True) -> dict:
    """RPP広告の予算とCPCを設定.

    budget: 月予算（円）
    Returns: {status, message}
    """
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return {"status": "error", "message": "Login failed"}

    await page.goto("https://ad.rms.rakuten.co.jp/rpp/campaigns", wait_until="networkidle", timeout=30000)

    # 月予算入力フィールドを探す
    budget_set = await page.evaluate(f'''() => {{
        const inputs = document.querySelectorAll('input[type="text"], input[type="number"]');
        for (const inp of inputs) {{
            const label = inp.closest('tr')?.querySelector('th, td')?.textContent || '';
            const parent = inp.parentElement?.textContent || '';
            if (label.includes('予算') || parent.includes('予算')) {{
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeInputValueSetter.call(inp, '{budget}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return 'budget set';
            }}
        }}
        return 'no budget field';
    }}''')
    print(f"  Budget: {budget_set}")
    await asyncio.sleep(1)

    # 「この予算とCPCを適用する」ボタン
    apply_btn = page.locator('button:has-text("この予算とCPCを適用")')
    if await apply_btn.count():
        await apply_btn.click()
        await asyncio.sleep(2)
        print(f"  Applied! URL: {page.url}")
        result = {"status": "ok", "message": f"Budget set to ¥{budget:,}", "url": page.url}
    else:
        result = {"status": "skipped", "message": budget_set}

    await browser.close()
    await pw.stop()
    return result


async def get_event_schedule(headless: bool = True) -> list[dict]:
    """広告掲載予定（イベント・キャンペーン）をスクレイピング.

    Returns: [{date, event, url}, ...]
    """
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return [{"error": "Login failed"}]

    await page.goto("https://ad.rms.rakuten.co.jp/ec/calendar", wait_until="networkidle", timeout=30000)

    # カレンダー情報を取得（Reactベースの可能性あり）
    events = await page.evaluate('''() => {
        const text = document.body.textContent || "";
        const lines = text.split("\\n").map(l => l.trim()).filter(l => l.length > 3);
        const entries = [];
        let cur = null;
        for (const line of lines) {
            if (line.startsWith("【") && (line.includes("マラソン") || line.includes("SALE") || line.includes("企画名"))) {
                if (cur) entries.push(cur);
                cur = {event: line.substring(0, 60)};
            } else if (cur) {
                const idx = line.indexOf("：");
                if (idx > 0) {
                    const key = line.substring(0, idx).trim();
                    const val = line.substring(idx + 1).substring(0, 60).trim();
                    cur[key] = val;
                }
            }
        }
        if (cur) entries.push(cur);
        return entries;
    }''')

    await browser.close()
    await pw.stop()
    return events


from datetime import datetime

if __name__ == "__main__":
    import sys
    
    if "--reviews" in sys.argv:
        path = asyncio.run(download_reviews_csv(headless="--visible" not in sys.argv))
        print(json.dumps({"file": path}, ensure_ascii=False, indent=2))
    elif "--budget" in sys.argv:
        budget = 500000
        for i, a in enumerate(sys.argv):
            if a == "--budget" and i+1 < len(sys.argv):
                budget = int(sys.argv[i+1])
        result = asyncio.run(set_rpp_budget(budget, headless="--visible" not in sys.argv))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--events" in sys.argv:
        events = asyncio.run(get_event_schedule(headless="--visible" not in sys.argv))
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        print("Usage: python rms_mcp/playwright_layer/rms_ops.py [--reviews|--budget N|--events] [--visible]")
