"""RPP広告レポート自動DL（Playwright） — 堅牢版.

ad.rms.rakuten.co.jp にログインし、RPP広告のパフォーマンスレポートを
CSVダウンロードする。UI変更に強いマルチセレクタ戦略を採用。

使い方:
    python -m rms_mcp.playwright_layer.rpp_reports \
        --from-date 2026-07-01 --to-date 2026-07-17 \
        --report-type daily  # daily=日次, item=商品別, keyword=キーワード別
"""
import asyncio
import json
import sys
import re
from pathlib import Path
from datetime import datetime, timedelta

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"
DOWNLOAD_DIR = Path.home() / "Downloads" / "rms-reports"


async def ensure_login(page) -> bool:
    """Cookieを使ってログイン済み状態にする."""
    if COOKIES_PATH.exists():
        cookies = json.loads(COOKIES_PATH.read_text())
        await page.context.add_cookies(cookies)
        await page.goto("https://ad.rms.rakuten.co.jp/rpp/reports",
                        wait_until="networkidle", timeout=30000)
        if "login" not in page.url.lower():
            return True
    return False


async def _set_date_inputs(page, from_date: str, to_date: str) -> bool:
    """日付入力を設定。複数のセレクタ戦略を試す."""
    strategies = [
        # 戦略1: 日本語プレースホルダー
        """() => {
            const patterns = ['開始', 'start', '開始日', 'from', 'From'];
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {
                const ph = (inp.placeholder || '').toLowerCase();
                const label = (inp.getAttribute('aria-label') || '').toLowerCase();
                if (patterns.some(p => ph.includes(p.toLowerCase()) || label.includes(p.toLowerCase()))) {
                    return true;
                }
            }
            return false;
        }""",
        # 戦略2: 全inputを列挙して日付型のものを探す
        """() => {
            const inputs = document.querySelectorAll('input[type="text"], input[type="date"], input:not([type])');
            return inputs.length > 0;
        }""",
    ]

    for strategy in strategies:
        try:
            has_inputs = await page.evaluate(strategy)
            if has_inputs:
                break
        except Exception:
            continue

    # 日付値を注入（全inputに対して試行、date型ならそちらを優先）
    js_set = f"""() => {{
        const inputs = document.querySelectorAll('input');
        const nativeSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;

        let fromSet = false;
        let toSet = false;

        for (const inp of inputs) {{
            const ph = (inp.placeholder || '').toLowerCase();
            const label = (inp.getAttribute('aria-label') || '');
            const name = (inp.name || '');
            const id = (inp.id || '');

            // 開始日を探す
            const isStart = ['開始', 'start', 'from', '開始日'].some(
                p => ph.includes(p) || label.includes(p) || name.includes(p) || id.includes(p)
            );
            // 終了日を探す
            const isEnd = ['終了', 'end', 'to', '終了日'].some(
                p => ph.includes(p) || label.includes(p) || name.includes(p) || id.includes(p)
            );

            if (isStart && !fromSet) {{
                nativeSetter.call(inp, '{from_date}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                fromSet = true;
            }}
            if (isEnd && !toSet) {{
                nativeSetter.call(inp, '{to_date}');
                inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('change', {{ bubbles: true }}));
                inp.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                toSet = true;
            }}
        }}

        // プレースホルダーで見つからなかった場合、先頭2つのinputに設定
        if (!fromSet && inputs.length >= 2) {{
            nativeSetter.call(inputs[0], '{from_date}');
            inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[0].dispatchEvent(new Event('change', {{ bubbles: true }}));
            nativeSetter.call(inputs[1], '{to_date}');
            inputs[1].dispatchEvent(new Event('input', {{ bubbles: true }}));
            inputs[1].dispatchEvent(new Event('change', {{ bubbles: true }}));
        }} else if (!fromSet && inputs.length >= 1) {{
            nativeSetter.call(inputs[0], '{from_date}');
            inputs[0].dispatchEvent(new Event('input', {{ bubbles: true }}));
        }}

        return {{ fromSet, toSet }};
    }}"""

    result = await page.evaluate(js_set)
    await asyncio.sleep(1.5)
    return True


async def _click_download_button(page, report_type: str) -> str | None:
    """ダウンロードボタンを探してクリック。見つけたボタンのテキストを返す。"""
    # レポート種別ごとのボタンテキスト候補（優先度順）
    candidate_sets = {
        "daily": [
            "この条件でダウンロード", "ダウンロード", "CSVダウンロード",
            "レポートダウンロード", "日次レポートダウンロード", "日次ダウンロード",
            "ダウンロードする", "DL",
        ],
        "item": [
            "全商品レポートダウンロード", "商品レポートダウンロード", "商品別ダウンロード",
            "商品別レポート", "商品別CSV", "ダウンロード",
            "全商品ダウンロード", "商品レポートDL",
        ],
        "keyword": [
            "全キーワードレポートダウンロード", "キーワードレポートダウンロード",
            "キーワード別ダウンロード", "キーワード別レポート", "キーワード別CSV",
            "ダウンロード", "全キーワードダウンロード",
        ],
    }

    candidates = candidate_sets.get(report_type, candidate_sets["daily"])

    # 戦略A: テキスト完全一致／部分一致でbuttonを探す
    for text in candidates:
        locator = page.locator(f'button:has-text("{text}")')
        try:
            count = await locator.count()
            if count > 0:
                await locator.first.click(timeout=5000)
                return text
        except Exception:
            continue

    # 戦略B: 「ダウンロード」を含む要素全般を探す
    for el_type in ['button', 'a', 'span', 'div']:
        locator = page.locator(f'{el_type}:has-text("ダウンロード")')
        try:
            count = await locator.count()
            if count > 0:
                await locator.first.click(timeout=5000)
                text = await locator.first.text_content()
                return f"{el_type}: {text[:50] if text else '?'}"
        except Exception:
            continue

    # 戦略C: 「DL」または「CSV」を含むリンク/ボタン
    for pattern in ['DL', 'CSV', 'csv', '.csv']:
        try:
            elements = page.locator(f'a:has-text("{pattern}"), button:has-text("{pattern}")')
            if await elements.count() > 0:
                await elements.first.click(timeout=5000)
                return f"pattern:{pattern}"
        except Exception:
            continue

    return None


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
        await browser.close()
        await pw.stop()
        return {"status": "error", "message": "Session expired — cookie refreshが必要です"}

    print(f"[rpp_reports] ログインOK, 日付設定中...")

    # 日付設定
    await _set_date_inputs(page, from_date, to_date)
    print(f"[rpp_reports] 日付: {from_date} ~ {to_date}")

    # ダウンロードボタン探索と実行
    print(f"[rpp_reports] ダウンロードボタン探索中 (type={report_type})...")
    btn_found = await _click_download_button(page, report_type)

    if not btn_found:
        # デバッグ情報を取得
        try:
            body_text = await page.text_content('body')
            snippet = body_text[:500] if body_text else '(empty)'
        except Exception:
            snippet = '(error reading page)'

        await browser.close()
        await pw.stop()
        return {
            "status": "error",
            "message": f"ダウンロードボタンが見つかりません (type={report_type})",
            "page_preview": snippet,
            "url": page.url,
        }

    print(f"[rpp_reports] ボタン発見: '{btn_found}' → ダウンロード待機...")

    # ダウンロード完了を待つ
    try:
        async with page.expect_download(timeout=120000) as download_info:
            await asyncio.sleep(3)  # クリック後の反応を待つ
        download = await download_info.value
    except Exception as e:
        await browser.close()
        await pw.stop()
        return {
            "status": "error",
            "message": f"ダウンロードタイムアウト: {e}",
            "button_found": btn_found,
        }

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = download.suggested_filename or f"rpp_{report_type}_{from_date}.csv"
    file_path = str(DOWNLOAD_DIR / filename)
    await download.save_as(file_path)

    await browser.close()
    await pw.stop()

    print(f"[rpp_reports] ✅ 完了: {file_path}")
    return {
        "status": "ok",
        "file_path": file_path,
        "suggested_filename": filename,
        "report_type": report_type,
        "from_date": from_date,
        "to_date": to_date,
        "button_used": btn_found,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="RPP広告レポート自動DL")
    parser.add_argument("--from-date",
                        default=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"))
    parser.add_argument("--to-date",
                        default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--report-type", default="daily",
                        choices=["daily", "item", "keyword"])
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(download_rpp_report(
        args.from_date, args.to_date, args.report_type,
        headless=not args.headless,
    ))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") != "ok":
        sys.exit(1)
