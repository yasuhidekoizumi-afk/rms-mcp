"""RMS画面操作モジュール — レビュー・RPP・掲載予定の本格自動化.

全ての関数は Cookie 再利用で認証済み。
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
    """Cookieでログイン状態を確保."""
    if COOKIES_PATH.exists():
        cookies = json.loads(COOKIES_PATH.read_text())
        await context.add_cookies(cookies)
    await page.goto("https://mainmenu.rms.rakuten.co.jp/",
                    wait_until="networkidle", timeout=30000)
    return "login" not in page.url.lower()


async def _make_browser(headless: bool = True):
    """Playwrightブラウザを起動（共通処理）."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        channel="chrome" if sys.platform == "darwin" else None,
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
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


async def download_reviews_csv(headless: bool = True) -> dict:
    """レビュー一覧をCSVダウンロード.

    Returns: {status, file_path} or {status, error}
    """
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return {"status": "error", "message": "Login failed"}

    try:
        await page.goto("https://review.rms.rakuten.co.jp/search/index/",
                        wait_until="networkidle", timeout=30000)

        # CSVダウンロードURLを取得（複数戦略）
        csv_url = None
        strategies = [
            # 戦略1: a要素のテキストで検索
            """() => {
                const links = document.querySelectorAll('a, button');
                for (const el of links) {
                    const text = (el.textContent || '').trim();
                    if (['CSVダウンロード', 'CSV', 'ダウンロード', 'DL'].some(
                        t => text.includes(t)
                    )) {
                        return el.href || el.getAttribute('onclick') || '';
                    }
                }
                return null;
            }""",
            # 戦略2: href に csv を含む要素
            """() => {
                const links = document.querySelectorAll('a[href*="csv"], a[href*="download"]');
                return links.length > 0 ? links[0].href : null;
            }""",
            # 戦略3: formのaction
            """() => {
                const forms = document.querySelectorAll('form');
                for (const f of forms) {
                    if ((f.action || '').includes('csv')) return f.action;
                }
                return null;
            }""",
        ]

        for strategy in strategies:
            try:
                result = await page.evaluate(strategy)
                if result:
                    csv_url = result
                    break
            except Exception:
                continue

        if not csv_url:
            await browser.close()
            await pw.stop()
            return {"status": "error", "message": "CSV download link not found on review page"}

        print(f"[reviews] CSV URL: {csv_url[:80]}...")

        # fetchで直接取得を試みる
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        try:
            csv_content = await page.evaluate(f"""async () => {{
                const resp = await fetch('{csv_url}');
                if (!resp.ok) return null;
                return await resp.text();
            }}""")
        except Exception:
            csv_content = None

        if csv_content and len(csv_content) > 100:
            file_path = str(DOWNLOAD_DIR / f"reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            Path(file_path).write_text(csv_content, encoding="shift_jis", errors="replace")
            print(f"[reviews] ✅ fetch成功: {file_path} ({len(csv_content)} bytes)")
        else:
            # fetch失敗 → expect_downloadで取得
            print("[reviews] fetch失敗 → expect_downloadでリトライ...")
            try:
                async with page.expect_download(timeout=180000) as download_info:
                    await page.goto(csv_url, wait_until="domcontentloaded", timeout=120000)
                    await asyncio.sleep(8)
                download = await download_info.value
                file_path = str(DOWNLOAD_DIR / f"reviews_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                await download.save_as(file_path)
                print(f"[reviews] ✅ download成功: {file_path}")
            except Exception as e:
                await browser.close()
                await pw.stop()
                return {"status": "error", "message": f"Download failed: {e}"}

        await browser.close()
        await pw.stop()
        return {"status": "ok", "file_path": file_path}

    except Exception as e:
        await browser.close()
        await pw.stop()
        return {"status": "error", "message": str(e)}


async def set_rpp_budget(budget: int, headless: bool = True) -> dict:
    """RPP広告の予算とCPCを設定.

    budget: 月予算（円）
    """
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return {"status": "error", "message": "Login failed"}

    await page.goto("https://ad.rms.rakuten.co.jp/rpp/campaigns",
                    wait_until="networkidle", timeout=30000)

    budget_set = await page.evaluate(f"""() => {{
        const inputs = document.querySelectorAll('input[type="text"], input[type="number"]');
        for (const inp of inputs) {{
            const label = inp.closest('tr')?.querySelector('th, td')?.textContent || '';
            const parent = inp.parentElement?.textContent || '';
            if (label.includes('予算') || parent.includes('予算')) {{
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(inp, '{budget}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return 'budget set';
            }}
        }}
        return 'no budget field';
    }}""")
    await asyncio.sleep(1)

    # 適用ボタン（複数パターン）
    apply_patterns = [
        "この予算とCPCを適用", "予算を適用", "適用する", "保存", "更新",
    ]
    result = {"status": "skipped", "message": budget_set}
    for pattern in apply_patterns:
        btn = page.locator(f'button:has-text("{pattern}")')
        if await btn.count():
            await btn.first.click()
            await asyncio.sleep(2)
            result = {"status": "ok", "message": f"Budget set to ¥{budget:,}"}
            break

    await browser.close()
    await pw.stop()
    return result


async def get_event_schedule(headless: bool = True) -> list[dict]:
    """広告掲載予定（イベント・キャンペーン）をスクレイピング."""
    pw, browser, context, page = await _make_browser(headless)
    if not page:
        return [{"error": "Login failed"}]

    await page.goto("https://ad.rms.rakuten.co.jp/ec/calendar",
                    wait_until="networkidle", timeout=30000)

    events = await page.evaluate("""() => {
        const text = document.body.textContent || "";
        const lines = text.split("\\n").map(l => l.trim()).filter(l => l.length > 3);
        const entries = [];
        let cur = null;
        for (const line of lines) {
            if (line.startsWith("【") && (
                line.includes("マラソン") || line.includes("SALE") || line.includes("企画名")
            )) {
                if (cur) entries.push(cur);
                cur = {event: line.substring(0, 60)};
            } else if (cur) {
                const idx = line.indexOf("：");
                if (idx > 0) {
                    cur[line.substring(0, idx).trim()] = line.substring(idx + 1, idx + 61).trim();
                }
            }
        }
        if (cur) entries.push(cur);
        return entries;
    }""")

    await browser.close()
    await pw.stop()
    return events


if __name__ == "__main__":
    if "--reviews" in sys.argv:
        result = asyncio.run(download_reviews_csv(
            headless="--visible" not in sys.argv
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--budget" in sys.argv:
        budget = 500000
        for i, a in enumerate(sys.argv):
            if a == "--budget" and i + 1 < len(sys.argv):
                budget = int(sys.argv[i + 1])
        result = asyncio.run(set_rpp_budget(
            budget, headless="--visible" not in sys.argv
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--events" in sys.argv:
        events = asyncio.run(get_event_schedule(
            headless="--visible" not in sys.argv
        ))
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        print("Usage: python rms_mcp/playwright_layer/rms_ops.py [--reviews|--budget N|--events] [--visible]")
