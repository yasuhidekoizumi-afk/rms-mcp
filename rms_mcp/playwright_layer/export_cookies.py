"""RMS Cookie エクスポートツール.

RMSにログインして、セッションCookieをGitHub Secrets用のbase64形式で出力する。
これを GitHub リポジトリの Secrets → RMS_PLAYWRIGHT_COOKIES に登録すると、
GHA上でPC不要の自動処理が動く。

使い方:
    python rms_mcp/playwright_layer/export_cookies.py

出力を GitHub Secrets にコピー:
    gh secret set RMS_PLAYWRIGHT_COOKIES --body "$(python rms_mcp/playwright_layer/export_cookies.py)" --repo yasuhidekoizumi-afk/oryzae

注意: Cookieの有効期限はRMSの仕様上、翌朝5時まで。
有効期限が切れたら再実行してSecretsを更新する。
"""
import asyncio
import base64
import json
from pathlib import Path

from playwright.async_api import async_playwright

COOKIES_PATH = Path.home() / ".cache" / "rms-playwright-cookies.json"


async def export_cookies():
    """RMSにログインしてCookieをエクスポート。

    ブラウザを開いてユーザーにログインさせる。Cookieはbase64エンコードしてstdoutに出力。
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=False,  # ログインは可視モード
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

    print("=== RMS Cookie エクスポート ===", flush=True)
    print("ブラウザが開きました。RMSにログインしてください。", flush=True)
    print("ログインが完了したら、このターミナルで Enter を押してください...", flush=True)
    
    # RMSログインページを開く
    await page.goto("https://glogin.rms.rakuten.co.jp/?sp_id=1", wait_until="networkidle", timeout=30000)
    
    # ユーザーがEnterを押すのを待つ
    await asyncio.get_event_loop().run_in_executor(None, input)
    
    # RMSメインメニューにいることを確認
    await page.goto("https://mainmenu.rms.rakuten.co.jp/", wait_until="networkidle", timeout=30000)
    url = page.url
    if "login" in url.lower():
        print("❌ ログインが確認できません。もう一度試してください。", flush=True)
        await browser.close()
        await pw.stop()
        return None

    # 全サブドメインのCookieを収集するため主要ページにアクセス
    domains = [
        "https://webservice.rms.rakuten.co.jp/merchant-portal/",
        "https://ad.rms.rakuten.co.jp/ec/top",
        "https://review.rms.rakuten.co.jp/search/index/",
    ]
    for d in domains:
        try:
            await page.goto(d, wait_until="load", timeout=30000)
            await asyncio.sleep(2)
        except:
            pass

    # Cookieを保存
    cookies = await context.cookies()
    COOKIES_PATH.write_text(json.dumps(cookies))
    print(f"✅ {len(cookies)}件のCookieを保存しました", flush=True)

    # base64エンコードして出力
    encoded = base64.b64encode(json.dumps(cookies).encode()).decode()
    print(f"\n=== GitHub Secretsに以下をコピー ===", flush=True)
    print(f"\n{encoded}\n")
    print(f"=== 登録コマンド ===", flush=True)
    print(f'gh secret set RMS_PLAYWRIGHT_COOKIES --body "{encoded[:20]}...{encoded[-20:]}" --repo yasuhidekoizumi-afk/oryzae', flush=True)

    await browser.close()
    await pw.stop()
    return encoded


if __name__ == "__main__":
    result = asyncio.run(export_cookies())
