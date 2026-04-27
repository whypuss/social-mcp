"""
post_ig.py — Instagram 圖文發文（CDP JS click 模式，v2 穩定版）

每次運行從乾淨的 IG 首頁開始，確保狀態穩定。

流程：
1. 連接 CDP，確認 IG 頁面
2. navigate 到 IG 首頁等完全加載（新貼文按鈕可見）
3. CDP JS click「新貼文」
4. 等初始 dialog → set_input_files 注入圖片
5. 裁切頁/濾鏡頁 CDP JS click「下一步」
6. Caption 頁 keyboard.type 輸入說明
7. CDP JS click「分享」
8. 等「已分享」→「完成」
"""

import asyncio
import logging
import os
import random
import time
from typing import Optional

from playwright.async_api import async_playwright

from social_mcp.browser_hijack import get_active_cdp_port

log = logging.getLogger(__name__)

_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))


# ── CDP JS Helpers ─────────────────────────────────────────────────────────

async def _click_btn_by_text(ig, text: str, timeout: float = 10):
    """在 [role=dialog] 內找 text === text 的按鈕，用完整 pointer+mouse 事件鏈點擊。"""
    script = f"""
    () => {{
        var dialog = document.querySelector('[role="dialog"]');
        if (!dialog) return 'no_dialog';
        var btns = dialog.querySelectorAll('[role="button"], button');
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t === '{text}') {{
                var rect = btns[i].getBoundingClientRect();
                var cx = rect.left + rect.width / 2;
                var cy = rect.top + rect.height / 2;
                var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy, isPrimary: true, pointerId: 1, view: window }};
                btns[i].dispatchEvent(new PointerEvent('pointerdown', opts));
                btns[i].dispatchEvent(new PointerEvent('pointerup', opts));
                btns[i].dispatchEvent(new MouseEvent('mousedown', opts));
                btns[i].dispatchEvent(new MouseEvent('mouseup', opts));
                btns[i].dispatchEvent(new MouseEvent('click', opts));
                return 'clicked:' + t;
            }}
        }}
        return 'not_found';
    }}
    """
    for _ in range(int(timeout * 5)):
        try:
            r = await ig.evaluate(script)
            if r != "not_found":
                log.debug(f"CDP JS click [{text}]: {r}")
                return r
        except Exception as e:
            log.debug(f"click [{text}] evaluate err: {e}")
        await asyncio.sleep(0.2)
    return "not_found"


async def _wait_dialog_contains(ig, keywords: list[str], timeout: float = 30) -> bool:
    """等任意 dialog 的 innerText 包含任意關鍵字。"""
    script = """
    () => {
        var d = document.querySelector('[role="dialog"]');
        if (!d) return '';
        return d.innerText.slice(0, 300);
    }
    """
    for _ in range(int(timeout * 5)):
        try:
            dt = await ig.evaluate(script)
            if dt and any(k in dt for k in keywords):
                log.debug(f"Dialog ready: {[k for k in keywords if k in dt]}")
                return True
        except Exception as e:
            log.debug(f"_wait_dialog evaluate err (retry): {e}")
        await asyncio.sleep(0.2)
    return False


# ── Main ──────────────────────────────────────────────────────────────────

async def post_ig(caption: str, image_path: str) -> str:
    if not os.path.exists(image_path):
        return f"❌ 圖片不存在: {image_path}"
    file_size = os.path.getsize(image_path)
    if file_size < 1024:
        return f"❌ 圖片太小（{file_size} bytes），IG 要求 > 1KB"

    port = get_active_cdp_port()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port}", timeout=15_000
        )
        ctx = browser.contexts[0]

        # 找到 IG 頁面
        ig = None
        for pg in ctx.pages:
            if "instagram.com/" in pg.url.lower() and "/login" not in pg.url.lower():
                ig = pg
                break
        if not ig:
            await browser.close()
            return "❌ 找不到已登入的 Instagram 頁面"

        await ig.bring_to_front()

        # ── Step 0: 檢查是否已有 dialog 開著 ─────────────────────────────
        existing_dialog = await ig.evaluate(
            "() => { var d = document.querySelector('[role=\"dialog\"]'); "
            "return d ? d.innerText.slice(0, 200) : ''; }"
        )

        if existing_dialog:
            log.debug(f"發現殘留 dialog: {repr(existing_dialog[:60])}")
            # 關掉殘留 dialog，重新開始
            await ig.keyboard.press("Escape")
            await asyncio.sleep(1)
            existing_dialog = ""  # 強制作為「無 dialog」處理

        if not existing_dialog:
            log.debug(f"當前 URL: {ig.url}")
            # 等 SVG 新貼文按鈕可見
            for attempt in range(5):
                try:
                    await ig.wait_for_load_state("networkidle", timeout=8000)
                    await asyncio.sleep(0.5)
                    svg_count = await ig.evaluate(
                        "() => document.querySelectorAll('svg[aria-label=\"新貼文\"]').length"
                    )
                    log.debug(f"新貼文 SVG count: {svg_count}")
                    if svg_count > 0:
                        break
                    log.debug(f"新貼文按鈕未就緒，重試 {attempt+1}/5")
                    await asyncio.sleep(2)
                except Exception as e:
                    log.debug(f"wait load state err: {e}")
                    await asyncio.sleep(2)
            else:
                await browser.close()
                return "❌ 找不到「新貼文」按鈕，請確認已登入 IG"

            # ── Step 0: 關閉殘留 dialog ───────────────────────────────────────
            try:
                dt = await ig.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 100) : ''; }"
                )
                if dt:
                    log.debug(f"發現殘留 dialog: {repr(dt[:60])}")
                    await ig.keyboard.press("Escape")
                    await asyncio.sleep(1.5)
            except Exception:
                pass

            # ── Step 1: 點「新貼文」──────────────────────────────────────────
            for attempt in range(3):
                try:
                    await ig.evaluate(
                        '() => { var s = document.querySelectorAll(\'svg[aria-label=\"新貼文\"]\'); '
                        'if(s[0] && s[0].parentElement) s[0].parentElement.click(); }'
                    )
                    log.debug("新貼文 clicked")
                    await _random_delay(2.0, 2.5)
                    break
                except Exception as e:
                    log.debug(f"點新貼文 attempt {attempt+1} failed: {e}")
                    if attempt == 2:
                        await browser.close()
                        return f"❌ 點新貼文失敗: {e}"
                    await asyncio.sleep(2)

            # 等初始 dialog（選擇檔案頁）
            for _ in range(30):
                try:
                    dt = await ig.evaluate(
                        "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                        "return d ? d.innerText.slice(0, 100) : ''; }"
                    )
                    if "建立新貼文" in dt or "從電腦選擇" in dt or "拖曳" in dt:
                        log.debug(f"初始 dialog: {repr(dt[:60])}")
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.3)
            else:
                await browser.close()
                return "❌ 新貼文 dialog 未出現"

            # ── Step 2: Playwright click「從電腦選擇」（React 需原生點擊）───────────
            # 用事件監聽方式攔截文件選擇器，避免 OS 窗口彈出
            file_chooser = None

            def on_file_chooser(fc):
                nonlocal file_chooser
                file_chooser = fc

            ig.on("filechooser", on_file_chooser)
            try:
                btn = ig.locator('button:has-text("從電腦選擇")').first
                await btn.click(timeout=5000)
                log.debug("從電腦選擇 clicked (Playwright)")
            except Exception as e:
                ig.remove_listener("filechooser", on_file_chooser)
                await browser.close()
                return f"❌ 點從電腦選擇失敗: {e}"

            # 等文件選擇器觸發（最多 10 秒）
            for _ in range(50):
                if file_chooser is not None:
                    break
                await asyncio.sleep(0.2)
            else:
                ig.remove_listener("filechooser", on_file_chooser)
                await browser.close()
                return "❌ File chooser 未出現"

            # 直接用 file_chooser.set_files 設文件（繞過 OS 窗口）
            try:
                await file_chooser.set_files(image_path, timeout=20_000)
                log.debug(f"File set via file_chooser event: {image_path} ({file_size} bytes)")
            except Exception as e:
                ig.remove_listener("filechooser", on_file_chooser)
                await browser.close()
                return f"❌ File chooser set_files failed: {e}"
            finally:
                ig.remove_listener("filechooser", on_file_chooser)

            # 等 IG 處理圖片
            await asyncio.sleep(3)
            log.debug("等待圖片載入完成")

        # ── Step 3: 裁切/編輯/篩選 → 下一步（直接找下一步，不用等特定 dialog）───
        # 等任意 dialog 出現，然後點下一步。重試直到 caption 頁。
        for i in range(10):
            # 等 1.5s 讓 dialog 出現
            await asyncio.sleep(1.5)

            # 檢查是否已到 caption 頁
            try:
                dt = await ig.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
                if "說明文字" in dt or "分享" in dt:
                    log.debug("已到 caption 頁")
                    break
            except Exception:
                pass

            # 嘗試點下一步
            r = await _click_btn_by_text(ig, "下一步")
            if r != "not_found":
                log.debug(f"下一步 clicked ({i+1})")
                await _random_delay(1.0, 1.5)
            else:
                log.debug(f"下一步未找到，等待重試 ({i+1}/10)")
        else:
            await browser.close()
            return "❌ 無法從裁切/編輯頁前進"

        # ── Step 5: Caption ────────────────────────────────────────────
        if not await _wait_dialog_contains(ig, ["說明文字", "分享"]):
            await browser.close()
            return "❌ caption 頁未出現"

        # 找到 caption textbox（contenteditable DIV，不是 input）
        for _ in range(10):
            try:
                boxes = ig.locator('[role="dialog"] [role="textbox"]')
                if await boxes.count() > 0:
                    await boxes.first.click(timeout=2000, force=True)
                    log.debug("Caption textbox (contenteditable) clicked")
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        else:
            await browser.close()
            return "❌ 找不到 caption textbox"

        # contenteditable DIV 用 fill() 並等待 5 秒
        textbox = ig.locator('[role="dialog"] [role="textbox"]').first
        await textbox.fill(caption)
        await asyncio.sleep(5.0)

        # 強迫編輯器同步 React state
        await ig.keyboard.press("ArrowRight")
        await asyncio.sleep(0.5)

        log.debug(f"Caption filled: {len(caption)} chars")

        r = await _click_btn_by_text(ig, "分享")
        if r == "not_found":
            await browser.close()
            return "❌ 找不到「分享」按鈕"
        log.debug("分享 clicked")

        # ── Step 7: 等「已分享」→「完成」───────────────────────────────
        for i in range(40):
            await asyncio.sleep(1)
            try:
                dt = await ig.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
                if "已分享" in dt:
                    log.debug(f"✅ 已分享（{i+1}s）")
                    break
            except Exception:
                pass
        else:
            await browser.close()
            try:
                dt = await ig.evaluate(
                    "() => (document.querySelector('[role=\"dialog\"]')||{}).innerText||''"
                )
                return f"❌ 分享超時，dialog: {repr(dt[:80])}"
            except Exception:
                return "❌ 分享超時"

        await _random_delay(0.5, 1.0)
        r = await _click_btn_by_text(ig, "完成")
        if r == "not_found":
            log.warning("找不到「完成」，按 Escape")
            await ig.keyboard.press("Escape")
        else:
            log.debug("完成 clicked")

        await browser.close()
        return "✅ Instagram 發文成功"


# ── CLI ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 3:
        print("用法: python -m social_mcp.post_ig <caption> <image_path>")
        sys.exit(1)

    result = asyncio.run(post_ig(sys.argv[1], sys.argv[2]))
    print(result)
