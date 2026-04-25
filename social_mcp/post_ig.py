"""
post_ig.py — Instagram 圖文發文腳本（2026-04 新 UI）

流程（Instagram 上傳 → 濾鏡 → 細節）：
1. 點擊「新貼文」（svg[aria-label]）
2. dialog input[type=file] 上傳圖片
3. 用 locator.focus() + locator.press("Enter") 跳過 filter 步驟直接到 details
4. 在細節頁輸入 caption（找 textarea[aria-label]）
5. 點「分享」
6. 點「完成」

用法：
    uv run python -m social_mcp.post_ig "caption" /path/to/image.jpg
"""
import asyncio
import sys
from playwright.async_api import async_playwright

IG_URL = "https://www.instagram.com"


def _get_active_port():
    import urllib.request
    for port in [9333, 9222]:
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
    return 9333


def _get_dialog_text(ig) -> str:
    return ig.evaluate(
        """() => {
            const d = document.querySelector("[role='dialog']");
            return d ? d.innerText?.slice(0, 500) : '';
        }"""
    )


def _type_caption(ig, text: str) -> str:
    return ig.evaluate(
        """([txt]) => {
            // Try textarea with aria-label containing "說明文字" or "撰寫"
            const tas = Array.from(document.querySelectorAll('textarea'));
            for (const ta of tas) {
                const aria = ta.getAttribute('aria-label') || '';
                if (aria.includes('說明文字') || aria.includes('撰寫') || aria.includes('caption')) {
                    const style = window.getComputedStyle(ta);
                    if (style.display !== 'none') {
                        ta.focus();
                        ta.value = txt;
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                        return 'textarea(' + aria + ')';
                    }
                }
            }
            
            // contenteditable inside dialog
            const dialog = document.querySelector("[role='dialog']");
            if (dialog) {
                const ces = Array.from(dialog.querySelectorAll('[contenteditable="true"]'));
                for (const ce of ces) {
                    if (ce.isContentEditable) {
                        const style = window.getComputedStyle(ce);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            ce.focus();
                            ce.innerText = txt;
                            ce.dispatchEvent(new Event('input', { bubbles: true }));
                            return 'contenteditable';
                        }
                    }
                }
            }
            
            // direct input in body
            const inputs = Array.from(document.querySelectorAll('input[aria-label*="說明"], textarea'));
            for (const inp of inputs) {
                const style = window.getComputedStyle(inp);
                if (style.display !== 'none') {
                    inp.focus();
                    inp.value = txt;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    return 'input(' + (inp.getAttribute('aria-label') || 'text') + ')';
                }
            }
            
            return 'no editor';
        }""",
        [text]
    )


async def post_ig(caption: str, image_path: str) -> str:
    port = _get_active_port()
    if not port:
        return "❌ 找不到 CDP Server"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://localhost:{port}", timeout=20000
            )
            ctx = browser.contexts[0]

            ig = None
            for pg in ctx.pages:
                u = pg.url
                if ("instagram.com" in u.lower()
                        and "/login" not in u.lower()
                        and "webworker" not in u):
                    ig = pg
                    break

            if not ig:
                ig = await ctx.new_page()
                await ig.goto(IG_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

            await ig.bring_to_front()
            await asyncio.sleep(2)

            # ── Step 1: 點擊「新貼文」 ─────────────────────
            print("[IG] Step 1: 點擊「新貼文」")
            await ig.evaluate(
                "() => document.querySelector('svg[aria-label=\"新貼文\"]')?.parentElement?.click()"
            )
            await asyncio.sleep(3)

            # ── Step 2: 上傳圖片 ─────────────────────────
            print(f"[IG] Step 2: 上傳 {image_path}")
            for _ in range(10):
                file_input = await ig.query_selector("[role='dialog'] input[type='file']")
                if file_input:
                    break
                await asyncio.sleep(0.5)
            else:
                return "❌ 找不到 file input"

            await file_input.set_input_files(image_path, timeout=15000)
            print("[IG]   圖片已設定")
            await asyncio.sleep(5)

            # ── Step 3: 跳到 details 頁 ─────────────────
            # Instagram 新 UI：上傳後直接到 filter 頁，點「下一步」一次就到 details
            print("[IG] Step 3: 跳到 details 頁")
            
            # 等待 filter 頁加載
            for _ in range(10):
                dialog = await _get_dialog_text(ig)
                if '濾鏡' in dialog or 'Aden' in dialog or 'Clarendon' in dialog:
                    break
                await asyncio.sleep(0.5)
            
            # 用 locator.focus() + press("Enter") 點擊「下一步」
            # 這樣 React synthetic event 才能正確觸發
            try:
                btn = ig.get_by_text("下一步", exact=True).first
                await btn.focus()
                await asyncio.sleep(0.3)
                await btn.press("Enter")
                print("[IG]   下一步: pressed Enter on focused button")
            except Exception as e:
                print(f"[IG]   下一步 failed: {e}")
                # Fallback: mouse click at coords
                rect = await ig.get_by_text("下一步", exact=True).first.bounding_box()
                if rect:
                    await ig.mouse.click(rect["x"] + rect["width"]/2, rect["y"] + rect["height"]/2)
                    print("[IG]   下一步: fallback mouse click")
            
            # 等 details 頁出現
            for _ in range(15):
                await asyncio.sleep(0.5)
                dialog = await _get_dialog_text(ig)
                if '說明文字' in dialog or '撰寫' in dialog or '分享' in dialog:
                    print(f"[IG]   到達 details 頁")
                    break

            # ── Step 4: 輸入 caption ──────────────────────
            print("[IG] Step 4: 輸入 caption")
            await asyncio.sleep(1)
            
            dialog_text = await _get_dialog_text(ig)
            print(f"[IG]   當前 dialog: {dialog_text[:80]}")

            for attempt in range(5):
                type_result = await _type_caption(ig, caption)
                print(f"[IG]   輸入嘗試 {attempt+1}: {type_result}")
                if "no editor" not in type_result:
                    break
                await asyncio.sleep(1)

            # ── Step 5: 點「分享」 ───────────────────────
            print("[IG] Step 5: 點擊「分享」")
            
            share_found = False
            try:
                share_btn = ig.get_by_text("分享", exact=True).first
                await share_btn.focus()
                await asyncio.sleep(0.3)
                await share_btn.press("Enter")
                share_found = True
                print("[IG]   分享: pressed Enter")
            except Exception as e:
                print(f"[IG]   分享 locator failed: {e}")
            
            if not share_found:
                return f"❌ 找不到「分享」"

            print("[IG]   等待發布...")
            await asyncio.sleep(8)

            # ── Step 6: 點「完成」（非阻塞，dialog 可能已自動關閉）────────
            print("[IG] Step 6: 點擊「完成」")
            try:
                # 先等最多 5 秒，超時就跳過（IG 發文後 dialog 常自動關閉）
                done_btn = ig.get_by_text("完成", exact=True).first
                await done_btn.wait_for(timeout=5000)
                await done_btn.press("Enter")
                print("[IG]   完成: pressed Enter")
            except Exception as e:
                print(f"[IG]   完成 (auto-closed or not shown, skipping): {e}")

            await asyncio.sleep(2)
            await browser.close()
            return "✅ Instagram 發文完成"

    except Exception as e:
        return f"❌ 錯誤: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python post_ig.py <caption> <image_path>")
        sys.exit(1)

    caption = sys.argv[1]
    image_path = sys.argv[2]
    result = asyncio.run(post_ig(caption, image_path))
    print(result)
