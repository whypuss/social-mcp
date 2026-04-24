import asyncio, os, sys
from playwright.async_api import async_playwright

WS_URL = "ws://127.0.0.1:9333/devtools/browser/65653279-e223-4f87-b6ff-ebd30cd96b2b"
SESSION_DIR = os.path.expanduser("~/Library/Application Support/Chromium/FacebookMCP")

async def post_facebook(message: str) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(WS_URL)
        ctx = browser.contexts[0]
        page = await ctx.new_page()

        # 1. Go to Facebook home
        await page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        body = await page.inner_text("body")
        if "登入" in body[:400] and "電子郵件" in body[:400]:
            await browser.close()
            return "❌ Not logged in. Open Chromium and log in to Facebook first."

        # 2. Click the composer (status box)
        try:
            composer = page.locator('[aria-label="在想些什麼？"]').first
            await composer.click(timeout=8000)
            await asyncio.sleep(2)
        except Exception as e:
            await browser.close()
            return f"❌ Could not find post composer: {e}"

        # 3. Type the message
        await page.keyboard.type(message, delay=30)
        await asyncio.sleep(1)

        # 4. Click Post button
        try:
            post_btn = page.locator('[aria-label="發布"]').first
            await post_btn.click(timeout=5000)
            await asyncio.sleep(3)
        except Exception as e:
            await browser.close()
            return f"❌ Could not click Post button: {e}"

        # 5. Check result
        url = page.url
        body = await page.inner_text("body")
        await browser.close()

        if "發佈" in body or "已發佈" in body or url != "https://www.facebook.com/":
            return f"✅ Post published successfully! URL: {url}"
        else:
            return f"⚠️ Post may have been published. URL: {url}"

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "測試發文 from Hermes Agent 🚀"
    result = asyncio.run(post_facebook(msg))
    print(result)
