"""
post_facebook.py — Facebook 圖文發文（Playwright CDP 模式，v2）

和 post_ig.py 同一架構：
- Playwright connect_over_cdp 到 Chromium
- page.on("filechooser") 事件監聽器攔截文件選擇對話框
- React 按鈕用 Playwright locator click
- Caption 用 keyboard.type

流程：
1. 連接 CDP，找到 FB 主頁
2. 點 Facebook 選單 → 帖子
3. 點「相片/影片」→ filechooser 事件注入圖片
4. execCommand("insertText") 打字
5. 下一頁 → 發佈
"""

import asyncio
import logging
import os
import random
import time
from playwright.async_api import async_playwright
from social_mcp.browser_hijack import get_active_cdp_port

log = logging.getLogger(__name__)
_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))


async def _click_btn_by_text(fb, text: str, timeout: float = 10):
    """CDP JS click 按鈕（含 fallback 包含匹配）。"""
    script = f"""
    () => {{
        var btns = document.querySelectorAll('[role="button"], button');
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t === '{text}') {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t.indexOf('{text}') >= 0) {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        return 'not_found';
    }}
    """
    for _ in range(int(timeout * 5)):
        try:
            r = await fb.evaluate(script)
            if r != "not_found":
                log.debug(f"CDP JS click [{text}]: {r}")
                return r
        except Exception as e:
            log.debug(f"click [{text}] evaluate err: {e}")
        await asyncio.sleep(0.2)
    return "not_found"


async def _wait_dialog_contains(fb, keywords: list, timeout: float = 30) -> bool:
    script = """
    () => {
        var d = document.querySelector('[role="dialog"]');
        if (!d) return '';
        return d.innerText.slice(0, 300);
    }
    """
    for _ in range(int(timeout * 5)):
        try:
            dt = await fb.evaluate(script)
            if dt and any(k in dt for k in keywords):
                log.debug(f"Dialog ready: {[k for k in keywords if k in dt]}")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


async def post_facebook(message: str, image_path: str = None) -> str:
    if image_path and not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path) if image_path else 0
    if image_path and file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），要求 > 1KB"

    port = get_active_cdp_port()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port}", timeout=15_000
        )
        ctx = browser.contexts[0]

        # 找到 FB 頁面（已登入）
        fb = None
        for pg in ctx.pages:
            if "facebook.com/" in pg.url.lower() and "/login" not in pg.url.lower():
                fb = pg
                break
        if not fb:
            await browser.close()
            return "❌ 找不到已登入的 Facebook 頁面"

        await fb.bring_to_front()

        # ── Step 0: 檢查是否已有 composer dialog ───────────────────────────
        existing = await fb.evaluate(
            "() => { var d = document.querySelector('[role=\"dialog\"]'); "
            "return d ? d.innerText.slice(0, 100) : ''; }"
        )

        if not existing:
            # ── Step 1 & 2: 直接點擊「在想什麼」composer ────────────────────
            # Playwright locator click 被攔截，改用 CDP JS click
            for attempt in range(3):
                try:
                    r = await fb.evaluate("""() => {
                        var btn = document.querySelector('[role="button"]');
                        if (btn && btn.innerText.includes('想')) { btn.click(); return 'clicked'; }
                        return 'not_found';
                    }""")
                    log.debug(f"在想什麼 composer clicked: {r}")
                    await asyncio.sleep(2)
                    # 等 composer dialog
                    if await _wait_dialog_contains(fb, ["粉絲專頁", "限時動態", "發佈", "相片", "影片"], timeout=5):
                        break
                    log.debug(f"composer dialog 未出現，重試 {attempt+1}/3")
                except Exception as e:
                    log.debug(f"點 composer attempt {attempt+1}: {e}")
                    if attempt == 2:
                        await browser.close()
                        return f"❌ 點 composer 失敗: {e}"
                await asyncio.sleep(2)

        # ── Step 3: 相片/影片（base64 DataTransfer 注入）──────────────────
        if image_path:
            # 讀取圖片為 base64
            import base64 as _b64
            with open(image_path, "rb") as f:
                b64_data = _b64.b64encode(f.read()).decode()

            # CDP JS: 把 base64 注入為 Blob/File，繞過 React input.files 限制
            inject_r = await fb.evaluate("""(b64) => {
                try {
                    const binaryString = atob(b64);
                    const bytes = new Uint8Array(binaryString.length);
                    for (let i = 0; i < binaryString.length; i++) {
                        bytes[i] = binaryString.charCodeAt(i);
                    }
                    const blob = new Blob([bytes], { type: 'image/jpeg' });
                    const file = new File([blob], 'upload.jpg', { type: 'image/jpeg', lastModified: Date.now() });

                    const inputs = document.querySelectorAll('input[type=file]');
                    for (let i = 0; i < inputs.length; i++) {
                        const inp = inputs[i];
                        const dt = new DataTransfer();
                        dt.items.add(file);
                        Object.defineProperty(inp, 'files', {
                            value: dt.files,
                            writable: true,
                            configurable: true
                        });
                        const tracker = inp._valueTracker;
                        if (tracker) { tracker.setValue(''); }
                        inp.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                    }
                    return { ok: true, inputsUpdated: inputs.length };
                } catch(e) {
                    return { error: e.message };
                }
            }""", b64_data)

            if not inject_r or not inject_r.get("ok"):
                await browser.close()
                return f"❌ 圖片注入失敗: {inject_r}"

            log.debug(f"Image injected via DataTransfer: {image_path} ({file_size} bytes)")

            # 等 FB 處理圖片（blob URL 出現 = 成功）
            for _ in range(10):
                await asyncio.sleep(1)
                preview = await fb.evaluate("""() => {
                    var d = document.querySelector('[role=dialog]');
                    if (!d) return null;
                    var imgs = d.querySelectorAll('img[src]');
                    for (var img of imgs) {
                        var src = img.src || '';
                        if (src.startsWith('blob:')) return src.slice(0, 80);
                    }
                    return null;
                }""")
                if preview:
                    log.debug(f"圖片預覽出現: {preview}")
                    break
            else:
                log.debug("⚠️ 圖片預覽未出現（可能上傳失敗）")
        else:
            log.debug("Step 3: Skip (no image)")

        # ── Step 4: 打字（execCommand insertText）───────────────────────────
        for _ in range(5):
            try:
                r = await fb.evaluate(f"""
                () => {{
                    var d = document.querySelector('[role="dialog"]');
                    if (!d) return "no_dialog";
                    var e = d.querySelector('[contenteditable="true"]');
                    if (!e) return "no_editor";
                    e.focus();
                    document.execCommand("insertText", false, {repr(message)});
                    return "done";
                }}
                """)
                if r == "done":
                    log.debug(f"Text inserted ({len(message)} chars)")
                    break
                log.debug(f"打字重試 {_}/5: {r}")
            except Exception as e:
                log.debug(f"打字 err: {e}")
            await asyncio.sleep(1)
        else:
            await browser.close()
            return "❌ 無法輸入文字"

        await _random_delay(0.5, 1.0)

        # ── Step 5: 下一頁 ───────────────────────────────────────────────
        r = await _click_btn_by_text(fb, "下一頁")
        if r == "not_found":
            # fallback: aria-label
            try:
                await fb.locator('[aria-label="下一頁"]').click(timeout=5000)
                log.debug("下一頁 clicked (aria-label)")
            except Exception:
                await browser.close()
                return "❌ 找不到「下一頁」按鈕"
        log.debug("下一頁 clicked")
        await asyncio.sleep(3)

        # ── Step 6: 發佈 ───────────────────────────────────────────────
        r = await _click_btn_by_text(fb, "發佈")
        if r == "not_found":
            try:
                await fb.locator('[aria-label="發佈"]').click(timeout=5000)
                log.debug("發佈 clicked (aria-label)")
            except Exception:
                await browser.close()
                return "❌ 找不到「發佈」按鈕"
        log.debug("發佈 clicked")

        # 等發佈完成（dialog 消失）
        for i in range(20):
            await asyncio.sleep(1)
            try:
                still_open = await fb.evaluate(
                    "() => !!document.querySelector('[role=\"dialog\"]')"
                )
                if not still_open:
                    log.debug(f"✅ Dialog closed（{i+1}s）")
                    break
            except Exception:
                pass
        else:
            await browser.close()
            return "❌ 發佈超時"

        await browser.close()
        return "✅ Facebook 發文成功"


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 2:
        print("用法: python -m social_mcp.post_facebook <message> [image_path]")
        sys.exit(1)

    msg = sys.argv[1]
    img = sys.argv[2] if len(sys.argv) > 2 else None
    result = asyncio.run(post_facebook(msg, img))
    print(result)
