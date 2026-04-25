import asyncio, json, sys, urllib.request
from playwright.async_api import async_playwright
from social_mcp.browser_hijack import CDP_PORTS

FB_URL = "https://www.facebook.com/"


def _get_active_port():
    """Return first active CDP port."""
    for port in CDP_PORTS:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/json/version",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status == 200:
                    return port
        except Exception:
            pass
    return None


async def post_facebook(message: str, image_path: str = None) -> str:
    """Post to Facebook with optional image."""
    port = _get_active_port()
    if not port:
        return "❌ No Chromium CDP running."

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://localhost:{port}",
                timeout=20000
            )
            ctx = browser.contexts[0]

            # Find FB page
            fb_page = None
            for pg in ctx.pages:
                u = pg.url
                if (u == FB_URL or (u.startswith("https://www.facebook.com/") and "/login" not in u and "/static/" not in u)) and "webworker" not in u:
                    fb_page = pg
                    break

            if not fb_page:
                await browser.close()
                return "❌ Facebook tab not found."

            await fb_page.bring_to_front()
            await asyncio.sleep(2)

            # Check logged in
            body = await fb_page.inner_text("body")
            if "登入" in body[:400] and "電子郵件" in body[:400]:
                await browser.close()
                return "❌ Not logged in."

            # Click composer
            try:
                composer = fb_page.locator('div[role="button"]').filter(has_text="在想什麼").first
                await composer.click(timeout=8000, force=True)
                await asyncio.sleep(1.5)
            except Exception as e:
                await browser.close()
                return f"❌ Composer not found: {e}"

            await fb_page.wait_for_selector('[role="dialog"]', timeout=5000)

            # Click the contenteditable area inside the main composer dialog
            # The dialog with file input is the one with aria-labelledby (index 0 after opening)
            await asyncio.sleep(0.5)
            try:
                editor = fb_page.locator('[role="dialog"]').nth(0).locator('[contenteditable="true"]').first
                await editor.click(timeout=5000)
                await asyncio.sleep(0.3)
            except Exception:
                pass

            # Type message
            await fb_page.keyboard.type(message, delay=30)
            await asyncio.sleep(0.5)

            # Upload image if provided
            # CRITICAL: Do NOT click "新增到帖子" - the file input is already in dialog[0]
            # The Facebook composer dialog has input[type=file] (display:none) in the same
            # dialog as the text editor. Clicking "新增到帖子" opens a separate media picker
            # that does NOT have the file input in it.
            if image_path:
                try:
                    # Use the file input in the first dialog (main composer)
                    file_input = fb_page.locator('[role="dialog"]').nth(0).locator('input[type="file"]').first
                    await file_input.set_input_files(image_path, timeout=15000)
                    # Wait for Facebook to process the image (thumbnail generation)
                    await asyncio.sleep(3)
                except Exception as e:
                    return f"❌ Image upload failed: {e}"

            # Submit via CDP form.submit — the input[type=submit] is always display:none
            # so we dispatch the submit event directly to the form element.
            result = await fb_page.evaluate('''
                () => {
                    const dlgs = document.querySelectorAll("[role=dialog]");
                    const mainDlg = dlgs[0];
                    if (!mainDlg) return "no dialog";
                    const submit = mainDlg.querySelector("input[type=submit]");
                    if (!submit) return "no submit";
                    const form = submit.closest("form");
                    if (form) {
                        form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
                        return "submitted";
                    }
                    submit.click();
                    return "clicked";
                }
            ''')
            if result != "submitted":
                return f"❌ Submit failed: {result}"

            await asyncio.sleep(3)

            url = fb_page.url
            await browser.close()
            return f"✅ Post published! URL: {url}"

    except Exception as e:
        return f"❌ Error: {e}"


if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "測試發文 from Hermes Agent 🚀"
    img = sys.argv[2] if len(sys.argv) > 2 else None
    result = asyncio.run(post_facebook(msg, img))
    print(result)
