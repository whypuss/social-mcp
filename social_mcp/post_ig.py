"""
post_ig.py — Instagram 圖文發文腳本（2026-04 實測 DOM 版）

流程：
1. 點擊「新貼文」按鈕
2. dialog 內 input[type=file] 上傳圖片
3. 等 filter 頁 OR 直接跳到 sharing 頁（兩種都可能發生）
4.  caption 輸入：div[aria-label="撰寫說明文字……"][contenteditable]
5. 點分享：div[role=button][text=分享]
6. 等完成

用法：
    uv run python -m social_mcp.post_ig "caption text" /path/to/image.jpg
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
        "() => { const d = document.querySelector(\"[role='dialog']\"); return d ? d.innerText.slice(0, 600) : ''; }"
    )


def _type_caption(ig, text: str) -> str:
    """輸入 caption 到 IG 的說明文字欄位（2026-04 實測：aria-label='撰寫說明文字……'）"""
    return ig.evaluate(
        """([txt]) => {
            // 找 contenteditable，aria-label 含「撰寫」or「說明文字」or「caption」
            const dialog = document.querySelector("[role='dialog']");
            if (!dialog) return 'no_dialog';

            const ces = Array.from(dialog.querySelectorAll('[contenteditable="true"]'));
            for (const ce of ces) {
                const style = window.getComputedStyle(ce);
                const aria = ce.getAttribute('aria-label') || '';
                if (style.display !== 'none' && (aria.includes('撰寫') || aria.includes('說明文字') || aria.includes('caption'))) {
                    ce.focus();
                    ce.innerText = txt;
                    ce.dispatchEvent(new Event('input', { bubbles: true }));
                    return 'caption:' + aria;
                }
            }

            // Fallback: 任意可見的 contenteditable
            for (const ce of ces) {
                const style = window.getComputedStyle(ce);
                if (style.display !== 'none') {
                    ce.focus();
                    ce.innerText = txt;
                    ce.dispatchEvent(new Event('input', { bubbles: true }));
                    return 'contenteditable_fallback';
                }
            }

            return 'no_editor';
        }""",
        [text]
    )


async def _click_share_button(ig) -> bool:
    """點擊「分享」按鈕（2026-04 實測：div[role=button]，非 <button>）"""
    # 先用 Playwright locator 找
    try:
        share_btn = ig.get_by_text("分享", exact=False).filter(has=ig.locator('[role="button"]')).first
        await share_btn.click(timeout=5000)
        print("[IG]   分享: clicked via text filter")
        return True
    except Exception as e:
        print(f"[IG]   分享 locator failed: {e}")

    # Fallback: JS 直接點擊
    result = await ig.evaluate("""() => {
        const dialog = document.querySelector("[role='dialog']");
        if (!dialog) return false;
        // 找包含「分享」文字的 role=button 元素
        const buttons = Array.from(dialog.querySelectorAll('[role="button"], button'));
        for (const btn of buttons) {
            const text = (btn.textContent || '').trim();
            if (text === '分享' || text === '分享到' || btn.getAttribute('aria-label') === '分享') {
                btn.click();
                return 'clicked:' + text;
            }
        }
        // 找 class 含 _abl- 的分享按鈕
        const shareBtns = Array.from(dialog.querySelectorAll('div[class*="_abl"]'));
        if (shareBtns.length > 0) {
            shareBtns[0].click();
            return 'clicked:_abl_btn';
        }
        return 'not_found';
    }""")
    print(f"[IG]   分享 JS: {result}")
    return 'not_found' not in result


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

            # 找已登入的 IG 頁面
            ig = None
            for pg in ctx.pages:
                u = pg.url
                if ("instagram.com" in u.lower()
                        and "/login" not in u.lower()
                        and "webworker" not in u.lower()):
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
            file_input = None
            for _ in range(20):
                file_input = await ig.query_selector("[role='dialog'] input[type='file']")
                if file_input:
                    break
                await asyncio.sleep(0.3)
            else:
                return "❌ 找不到 file input"

            await file_input.set_input_files(image_path, timeout=20000)
            print("[IG]   圖片已設定")
            await asyncio.sleep(4)

            # ── Step 3: 等 dialog 穩定（filter 頁 或 直接 sharing 頁）────────
            print("[IG] Step 3: 等 dialog 穩定")
            for _ in range(20):
                await asyncio.sleep(0.5)
                dialog_text = await _get_dialog_text(ig)
                if not dialog_text:
                    continue
                # filter 頁關鍵字：濾鏡、Aden、Clarendon、濾鏡、Next
                if any(kw in dialog_text for kw in ['濾鏡', 'Aden', 'Clarendon', '下一步']):
                    print(f"[IG]   到達 filter 頁")
                    break
                # sharing 頁關鍵字：說明文字、撰寫、分享、分享到
                if any(kw in dialog_text for kw in ['說明文字', '撰寫說明', '分享到', '分享\n']):
                    print(f"[IG]   到達 sharing 頁（跳過 filter）")
                    break
            else:
                print(f"[IG]   dialog 未出現，等待後繼續: {dialog_text[:100]}")

            # ── Step 3b: 如果在 filter 頁，點「下一步」 ───────
            dialog_text = await _get_dialog_text(ig)
            if '下一步' in dialog_text:
                print("[IG]   在 filter 頁，點下一步")
                # 找下一步按鈕
                next_clicked = False
                for attempt in range(3):
                    try:
                        # 方法1: JS click
                        result = await ig.evaluate("""() => {
                            const dialog = document.querySelector("[role='dialog']");
                            if (!dialog) return 'no_dialog';
                            const btns = Array.from(dialog.querySelectorAll('button, [role="button"], div[aria-label]'));
                            for (const btn of btns) {
                                const text = (btn.textContent || '').trim();
                                if (text === '下一步') { btn.click(); return 'clicked'; }
                            }
                            // 找 aria-label 含「下一步」
                            for (const btn of btns) {
                                if (btn.getAttribute('aria-label') === '下一步') { btn.click(); return 'aria_clicked'; }
                            }
                            return 'not_found';
                        }""")
                        print(f"[IG]   下一步 JS: {result}")
                        if 'not_found' not in result:
                            next_clicked = True
                            break
                    except Exception as e:
                        print(f"[IG]   下一步 attempt {attempt+1} failed: {e}")
                    await asyncio.sleep(1)

                # 等進入 details/sharing 頁
                for _ in range(20):
                    await asyncio.sleep(0.5)
                    dt = await _get_dialog_text(ig)
                    if '說明文字' in dt or '撰寫說明' in dt or '分享' in dt:
                        print(f"[IG]   到達 caption/sharing 頁")
                        break

            # ── Step 4: 輸入 caption ──────────────────────
            print("[IG] Step 4: 輸入 caption")
            await asyncio.sleep(1)

            dialog_text = await _get_dialog_text(ig)
            print(f"[IG]   dialog 狀態: {dialog_text[:100]}")

            for attempt in range(5):
                type_result = await _type_caption(ig, caption)
                print(f"[IG]   輸入嘗試 {attempt+1}: {type_result}")
                if "no_editor" not in type_result and "no_dialog" not in type_result:
                    break
                await asyncio.sleep(1)

            await asyncio.sleep(1)

            # ── Step 5: 點「分享」 ───────────────────────
            print("[IG] Step 5: 點擊分享")
            share_ok = await _click_share_button(ig)
            if not share_ok:
                return "❌ 找不到分享按鈕"

            print("[IG]   等待發布...")
            # 等 dialog 消失（發布成功後 dialog 會關閉）
            for _ in range(20):
                await asyncio.sleep(1)
                dt = await _get_dialog_text(ig)
                if not dt or '建立新貼文' not in dt:
                    print(f"[IG]   dialog 已關閉，發布成功")
                    break

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
