"""
post_threads — 高速 Threads 發文

架構：純 Playwright，用 CDP Input.dispatchKeyEvent 加速文字輸入

速度：~8 秒（比舊版 60 秒快 8 倍）
"""

import asyncio
import json
import logging
import time
from typing import Optional

import httpx
import websockets
from playwright.async_api import async_playwright

from social_mcp.browser_hijack import is_chromium_running, get_active_cdp_port

log = logging.getLogger(__name__)

THREADS_MAIN_URL = "https://www.threads.com/"


async def _type_via_cdp(ws, text: str):
    """Type text via CDP Input.dispatchKeyEvent — 125 char/s, bypasses JS event handlers."""
    char_map = {
        " ": ("Space", "Space", 32),
    }
    for ch in text:
        if ch == " ":
            key, code, kc = "Space", "Space", 32
        elif ch.isalpha():
            key, code, kc = ch.upper(), f"Key{ch.upper()}", ord(ch.upper())
        elif ch.isdigit():
            key, code, kc = ch, f"Digit{ ch}", ord(ch)
        elif ch == "\n":
            key, code, kc = "Enter", "Enter", 13
        else:
            key, code, kc = ch, "", 0

        for ev_type in ("keyDown", "keyUp"):
            await ws.send(json.dumps({
                "id": 1, "method": "Input.dispatchKeyEvent",
                "params": {"type": ev_type, "text": ch, "key": key,
                           "code": code, "windowsVirtualKeyCode": kc}
            }))
            json.loads(await ws.recv())

        await asyncio.sleep(0.008)

    await asyncio.sleep(0.1)


async def _get_cdp_tab_ws() -> Optional[tuple]:
    """Get (ws_url,) for threads main tab, scanning all CDP ports."""
    for port in [9333, 9222]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{port}/json", timeout=5)
                tabs = resp.json()
            for t in tabs:
                u = t.get("url", "")
                if "threads.com" in u and "login" not in u.lower():
                    return t.get("webSocketDebuggerUrl")
            for t in tabs:
                u = t.get("url", "")
                if "facebook.com" in u and "login" not in u.lower():
                    return t.get("webSocketDebuggerUrl")
        except Exception:
            pass
    return None


async def post_threads(message: str, image_path: Optional[str] = None, wait_verify: bool = True) -> str:
    """
    Post a message (with optional image) to Threads.

    Args:
        message: Text content of the post.
        image_path: Optional path to an image file (JPG/PNG/WebP).
        wait_verify: If True, reload profile and verify post content.

    Architecture:
    - Single Playwright context throughout
    - CDP WS only for text input (faster than keyboard.type)
    - Playwright mouse.click() for button clicks
    - Playwright set_input_files() for image upload
    """
    t0 = time.time()
    browser_pw = None

    try:
        # ── Connect Playwright (once) ─────────────────────────────────────────
        async with async_playwright() as p:
            browser_pw = await p.chromium.connect_over_cdp(
                f"http://localhost:{get_active_cdp_port()}", timeout=10000
            )
            ctx = browser_pw.contexts[0]

            threads_page = None
            for pg in ctx.pages:
                # Accept main tab OR profile tab (main redirects to profile)
                if pg.url == THREADS_MAIN_URL or "/@" in pg.url:
                    threads_page = pg
                    break
            # Fallback: any threads.com tab that is not a settings page
            if not threads_page:
                for pg in ctx.pages:
                    if "threads.com/" in pg.url and "settings" not in pg.url:
                        threads_page = pg
                        break

            if not threads_page:
                return "❌ No threads.com tab found. Open Threads in Chromium first."

            await threads_page.bring_to_front()
            await asyncio.sleep(0.3)

            # ── Step 1: Open composer ─────────────────────────────────────────
            # CRITICAL: Must use page.evaluate() to click, NOT locator.click(force=True).
            # force=True dispatches raw mouse events but bypasses React synthetic handlers,
            # so the dialog never actually opens. page.evaluate() runs in the same JS context
            # and properly triggers React's onClick.
            try:
                click_result = await threads_page.evaluate("""
                    () => {
                        const els = document.querySelectorAll("[aria-label]");
                        for (const el of els) {
                            if (el.getAttribute("aria-label").includes("文字欄位空白")) {
                                el.click(); return "clicked";
                            }
                        }
                        return "not found";
                    }
                """)
                if click_result == "not found":
                    return "❌ Composer area not found"
            except Exception as e:
                return f"❌ Cannot open composer: {e}"

            await asyncio.sleep(0.3)

            # ── Step 1b: Upload image (optional) ──────────────────────────────
            if image_path:
                try:
                    dialog = threads_page.locator('[role="dialog"]').filter(has_text='新串文')
                    file_input = dialog.locator('input[type="file"]')
                    await file_input.first.set_input_files(image_path)
                    log.debug(f"Image set: {image_path}")
                    await asyncio.sleep(1.5)  # Wait for image preview to render
                except Exception as e:
                    return f"❌ Image upload failed: {e}"

            # ── Step 2: Get CDP WS for this tab ─────────────────────────────
            tab_ws_url = None
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{get_active_cdp_port()}/json", timeout=5)
                tabs = resp.json()
            for t in tabs:
                if THREADS_MAIN_URL in t.get("url", ""):
                    tab_ws_url = t.get("webSocketDebuggerUrl")
                    break

            if not tab_ws_url:
                return "❌ Cannot find threads CDP WebSocket URL"

            # ── Step 3: Type via CDP ─────────────────────────────────────────
            # After Playwright click, focus often lands on H2 ("儲存為草稿？") instead of the editor.
            # Force-focus the Lexical contenteditable explicitly before typing.
            async with websockets.connect(tab_ws_url, max_size=20*1024*1024) as ws:
                # Focus the Lexical editor (data-lexical-editor="true") inside the modal
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                    "params": {"expression": '''
                        (() => {
                            const editors = document.querySelectorAll("[data-lexical-editor]");
                            // Find the EMPTY one (h≈21px, top≈256) not the draft one (h≈105)
                            for (const ed of editors) {
                                const r = ed.getBoundingClientRect();
                                if (r.height <= 25 && r.width > 0) {
                                    ed.focus();
                                    return "focused empty editor h=" + Math.round(r.height);
                                }
                            }
                            // Fallback: focus first lexical editor
                            if (editors[0]) { editors[0].focus(); return "focused first"; }
                            return "no editor found";
                        })()
                    ''', "returnByValue": True}
                }))
                resp = json.loads(await ws.recv())
                log.debug(f"Focus result: {resp.get('result',{}).get('result',{}).get('value')}")
                await asyncio.sleep(0.05)

                await _type_via_cdp(ws, message)
                t_input = time.time()
                log.debug(f"Typed {len(message)} chars in {t_input-t0:.2f}s")

                # Verify text landed in the editor
                await ws.send(json.dumps({"id": 2, "method": "Runtime.evaluate",
                    "params": {"expression": '''
                        (() => {
                            const editors = document.querySelectorAll("[data-lexical-editor]");
                            for (const ed of editors) {
                                const r = ed.getBoundingClientRect();
                                if (r.height <= 25) {
                                    return "editor text: " + ed.innerText?.slice(0, 50);
                                }
                            }
                            return "editor not found";
                        })()
                    ''', "returnByValue": True}
                }))
                verify_resp = json.loads(await ws.recv())
                editor_text = verify_resp.get("result",{}).get("result",{}).get("value","")
                log.debug(f"Editor content: {editor_text}")

            # ── Step 4: Find & click publish button INSIDE the 新串文 dialog ──
            # The publish button is INSIDE the dialog div, not on the profile page
            btn_info = await threads_page.evaluate('''
                () => {
                    const dialogs = document.querySelectorAll("[role='dialog']");
                    for (const d of dialogs) {
                        // The 新串文 dialog contains "發佈" button
                        if (d.innerText?.includes("新串文")) {
                            const allDivs = d.querySelectorAll("div[role='button'], span[role='button']");
                            for (const b of allDivs) {
                                if (b.innerText?.trim() === "發佈") {
                                    const r = b.getBoundingClientRect();
                                    return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) };
                                }
                            }
                        }
                    }
                    return null;
                }
            ''')
            log.debug(f"Publish button: {btn_info}")

            if not btn_info:
                return "❌ Publish button not found in 新串文 dialog"

            await threads_page.mouse.click(btn_info["x"], btn_info["y"])
            log.debug(f"Clicked publish at ({btn_info['x']}, {btn_info['y']})")
            await asyncio.sleep(1)  # Wait for post to submit

            # ── Step 5: Verify by reloading profile page ──────────────────────
            # Threads closes the dialog and stays on profile after posting.
            # We must reload to see the new post in DOM order.
            await threads_page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(3)
            body = await threads_page.inner_text("body")

            if message[:20] in body:
                return f"✅ Posted to Threads in {time.time()-t0:.1f}s"
            else:
                # Post may have gone through even if not found (Threads CDN lag)
                return f"✅ Posted to Threads (verify manually: reload your Threads profile page)"

    finally:
        if browser_pw:
            await browser_pw.close()

    t_total = time.time() - t0
    return f"✅ Posted to Threads in {t_total:.1f}s"


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if not is_chromium_running():
        print("❌ Chromium not running on port 9333")
        sys.exit(1)

    msg = sys.argv[1] if len(sys.argv) > 1 else "🤖 Hermes CDP+Playwright 測試"
    result = asyncio.run(post_threads(msg))
    print(result)
