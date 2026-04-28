"""
post_ig_human.py — Instagram 擬人發文流程（CDP 版）

流程：
1. 點小屋圖標 → 確保在首頁餒
2. 點新貼文（+）按鈕
3. 等「建立新帖子」dialog → 點「從電腦選擇」
4. file_chooser.set_files() 注入圖片
5. 等 3s（IG 處理圖片）
6. 右上角「下一步」(裁切頁)
7. 右上角「下一步」(濾鏡頁)
8. Caption 頁輸入文字
9. 右上角「分享」
10. 等「已分享」→「完成」

按鈕全部用 [aria-label] 定位，隨機延遲模拟人類操作。
"""

import asyncio
import logging
import os
import random
import time
from playwright.async_api import async_playwright

from social_mcp.browser_hijack import get_active_cdp_port

log = logging.getLogger(__name__)

_random = lambda a, b: random.uniform(a, b)


# ── Dialog 按鈕點擊（用 aria-label）───────────────────────────────────────────

async def _click_btn_in_dialog(page, target: str, timeout: float = 10) -> bool:
    """
    在 [role=dialog] 內找按鈕並點擊。
    策略1: aria-label 匹配
    策略2: textContent 包含 target
    策略3: Playwright locator click (React 原生)
    """
    # 把 target 的 f-string 替換準備好（避免嵌套替換問題）
    _target = target  # noqa: F841 used in eval

    for _ in range(int(timeout * 5)):
        # ── 策略0: Playwright 原生 .click()（觸發 React 事件）───────────────
        try:
            # 用 Playwright locator + has-text 找元素
            locator = page.locator(f'[role="dialog"] :text-is("{target}")').first
            count = await locator.count()
            if count == 0:
                # fallback: has-text (contains)
                locator = page.locator(f'[role="dialog"] :text("{target}")').first
                count = await locator.count()
            if count > 0:
                await locator.click(timeout=5000)
                log.debug(f"[dialog] clicked '{target}' (Playwright .click())")
                return True
        except Exception as e:
            log.debug(f"[dialog] playwright '{target}': {e}")

        # ── 策略1: aria-label ──────────────────────────────────────────────
        try:
            r = await page.evaluate(f"""
            () => {{
                var dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return 'no_dialog';
                var targets = dialog.querySelectorAll('[aria-label="{target}"]');
                if (!targets.length) return 'not_aria';
                var btn = targets[0];
                var rect = btn.getBoundingClientRect();
                if (!rect || rect.width === 0 || rect.height === 0) return 'hidden';
                var cx = rect.left + rect.width / 2;
                var cy = rect.top + rect.height / 2;
                var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
                             isPrimary: true, pointerId: 1, view: window }};
                btn.dispatchEvent(new MouseEvent('mousedown', opts));
                btn.dispatchEvent(new MouseEvent('mouseup', opts));
                btn.dispatchEvent(new MouseEvent('click', opts));
                return 'aria_ok';
            }}
            """)
            if r == "aria_ok":
                log.debug(f"[dialog] clicked '{target}' (aria-label)")
                return True
        except Exception as e:
            log.debug(f"[dialog] aria try '{target}': {e}")

        # ── 策略2: textContent 包含 target ────────────────────────────────
        try:
            r2 = await page.evaluate(f"""
            () => {{
                var dialog = document.querySelector('[role="dialog"]');
                if (!dialog) return 'no_dialog';
                var btns = dialog.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {{
                    var tc = (btns[i].textContent || '').trim();
                    if (tc === '{target}' || tc.includes('{target}')) {{
                        var rect = btns[i].getBoundingClientRect();
                        if (!rect || rect.width === 0 || rect.height === 0) continue;
                        var cx = rect.left + rect.width / 2;
                        var cy = rect.top + rect.height / 2;
                        var opts = {{ bubbles: true, cancelable: true, clientX: cx, clientY: cy,
                                     isPrimary: true, pointerId: 1, view: window }};
                        btns[i].dispatchEvent(new MouseEvent('mousedown', opts));
                        btns[i].dispatchEvent(new MouseEvent('mouseup', opts));
                        btns[i].dispatchEvent(new MouseEvent('click', opts));
                        return 'tc_ok:' + tc.slice(0, 20);
                    }}
                }}
                return 'not_tc';
            }}
            """)
            if r2.startswith("tc_ok"):
                log.debug(f"[dialog] clicked '{target}' (textContent): {r2}")
                return True
        except Exception as e:
            log.debug(f"[dialog] tc try '{target}': {e}")

        # ── 策略3: Playwright 原生 click ─────────────────────────────────
        try:
            btn = page.locator(f'[role="dialog"] button:has-text("{target}")').first
            if await btn.count() > 0:
                await btn.click(timeout=3000, force=True)
                log.debug(f"[dialog] clicked '{target}' (Playwright)")
                return True
        except Exception as e:
            log.debug(f"[dialog] playwright try '{target}': {e}")

        await asyncio.sleep(0.3)
    log.warning(f"[dialog] could not click '{target}'")
    return False


async def _wait_dialog_contains(page, keyword: str, timeout: float = 20) -> bool:
    """等 dialog 的 innerText 包含關鍵字。"""
    for _ in range(int(timeout * 5)):
        try:
            dt = await page.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if keyword in dt:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


# ── 找 nav 圖標（href=# 的 <a>）────────────────────────────────────────────

async def _click_nav_by_aria(page, aria_label: str) -> bool:
    """點擊導航欄上 aria-label 匹配的 <a> 標籤。"""
    script = f"""
    () => {{
        var anchors = document.querySelectorAll('a[href="#"]');
        for (var i = 0; i < anchors.length; i++) {{
            var svg = anchors[i].querySelector('svg');
            var label = svg ? (svg.getAttribute('aria-label') || '') : '';
            if (label === '{aria_label}') {{
                anchors[i].click();
                return 'clicked';
            }}
        }}
        return 'not_found';
    }}
    """
    try:
        r = await page.evaluate(script)
        log.debug(f"[nav] clicked '{aria_label}': {r}")
        return r == "clicked"
    except Exception as e:
        log.warning(f"[nav] click '{aria_label}' failed: {e}")
        return False


# ── 等 IG 首頁 ready（帖子動態可見）──────────────────────────────────────────

async def _wait_ig_feed(page, timeout: float = 10) -> bool:
    """等 IG 首頁 feed 帖子出現（[role='article']）。"""
    for _ in range(int(timeout * 5)):
        try:
            count = await page.evaluate(
                "() => document.querySelectorAll('article').length"
            )
            if count >= 1:
                log.debug(f"[feed] ready, {count} articles found")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    return False


# ── CDP 注入文件到 input[type=file] ─────────────────────────────────────────

async def _inject_file_cdp(page, image_path: str) -> bool:
    """
    用 Playwright 的 set_input_files 直接注入文件到 hidden input[type=file]。
    繞過 OS 文件對話框。
    """
    escaped_path = image_path.replace("\\", "\\\\").replace('"', '\\"')
    result = await page.evaluate(f"""
    async () => {{
        const d = document.querySelector('[role="dialog"]');
        if (!d) return 'no_dialog';
        const fileInput = d.querySelector('input[type=file]');
        if (!fileInput) return 'no_file_input';

        // IG React 監聽 'change' 事件
        // 直接調用 Playwright 的 CDP 來 set_input_files
        return 'need_playwright_set';
    }}
    """)
    log.debug(f"[inject] CDP result: {result}")

    if result == "need_playwright_set":
        # 找到 input[type=file]
        locator = page.locator('[role="dialog"] input[type="file"]').first
        try:
            await locator.set_input_files(image_path, timeout=15_000)
            log.debug(f"[inject] set_input_files succeeded")
            await asyncio.sleep(1.5)
            # 手動觸發 change 事件
            await page.evaluate("""
            () => {
                const d = document.querySelector('[role="dialog"]');
                const fileInput = d ? d.querySelector('input[type=file]') : null;
                if (fileInput) fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """)
            return True
        except Exception as e:
            log.warning(f"[inject] set_input_files failed: {e}")
    return False


# ── 主流程 ───────────────────────────────────────────────────────────────────

async def post_ig_human(caption: str, image_path: str) -> str:
    """擬人化 IG 發文。返回 '✅ ...' 或 '❌ ...'。"""
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

        # ── Step 0: 確保在首頁 ──────────────────────────────────────────────
        await ig.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        await asyncio.sleep(_random(1.5, 2.5))

        # 如果有殘留 dialog，關掉
        try:
            dt = await ig.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 100) : ''; }"
            )
            if dt:
                log.debug(f"Closing residual dialog: {repr(dt[:50])}")
                await ig.keyboard.press("Escape")
                await asyncio.sleep(1.2)
        except Exception:
            pass

        # ── Step 1: 點「新貼文」按鈕 ─────────────────────────────────────────
        await asyncio.sleep(_random(0.5, 1.0))

        for attempt in range(3):
            try:
                await ig.evaluate("""
                () => {
                    var s = document.querySelector('svg[aria-label="新貼文"]');
                    if (s && s.parentElement && s.parentElement.tagName === 'A') {
                        s.parentElement.click();
                    } else if (s && s.parentElement) {
                        s.parentElement.click();
                    }
                }
                """)
                log.debug(f"[step1] 新貼文 clicked (attempt {attempt + 1})")
                await asyncio.sleep(_random(2.0, 2.5))
                break
            except Exception as e:
                log.warning(f"[step1] attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    await browser.close()
                    return f"❌ 點新貼文失敗: {e}"
                await asyncio.sleep(2)

        # ── Step 2: 等「建立新帖子」dialog，注入圖片 ──────────────────────────
        if not await _wait_dialog_contains(ig, "從電腦選擇", timeout=15):
            await asyncio.sleep(2)
            dt = await ig.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 100) : ''; }"
            )
            if "從電腦選擇" not in dt:
                await browser.close()
                return "❌ 建立新帖子 dialog 未出現"

        log.debug("[step2] Injecting image via set_input_files (CDP)")
        await asyncio.sleep(_random(0.5, 1.0))

        # 方案：用 context.expect_file_chooser() 等 dialog 出現
        # 如果超時（dialog 沒出現），直接用 set_input_files 注入
        try:
            fc = await ig.context.wait_for_file_chooser(timeout=3000)
            log.debug(f"[step2] file_chooser intercepted: {fc}")
            await fc.set_files(image_path, timeout=20_000)
            log.debug(f"[step2] File set via file_chooser: {image_path}")
        except Exception as fc_err:
            # file_chooser 沒出現，用 set_input_files 直接注入（不走 OS dialog）
            log.warning(f"[step2] file_chooser not intercepted ({fc_err}), using set_input_files")
            try:
                inp = ig.locator('[role="dialog"] input[type="file"]').first
                await inp.set_input_files(image_path, timeout=15_000)
                log.debug(f"[step2] set_input_files succeeded: {image_path}")
                # 手動觸發 change 事件
                await ig.evaluate("""
                () => {
                    const d = document.querySelector('[role="dialog"]');
                    const fileInput = d ? d.querySelector('input[type=file]') : null;
                    if (fileInput) fileInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """)
            except Exception as inject_err:
                log.error(f"[step2] set_input_files also failed: {inject_err}")
                await ig.keyboard.press("Escape")  # 清理殘留 dialog
                raise Exception(f"Image upload failed: {inject_err}")

        # IG 處理圖片
        await asyncio.sleep(_random(3.0, 3.5))
        log.debug("[step2] Image processing...")

        # ── Step 3: 右上角「下一步」(裁切/調整) ──────────────────────────────
        if not await _click_btn_in_dialog(ig, "下一步", timeout=8):
            await browser.close()
            return "❌ 裁切頁「下一步」找不到"
        log.debug("[step3] Crop page → 下一步 clicked")
        await asyncio.sleep(_random(1.5, 2.0))

        # ── Step 4: 右上角「下一步」(濾鏡) ─────────────────────────────────
        if not await _click_btn_in_dialog(ig, "下一步", timeout=8):
            await browser.close()
            return "❌ 濾鏡頁「下一步」找不到"
        log.debug("[step4] Filter page → 下一步 clicked")
        await asyncio.sleep(_random(2.0, 2.5))

        # ── Step 5: Caption 頁 ───────────────────────────────────────────────
        # 等說明文字出現（最多 15 秒，不斷重試）
        caption_found = False
        for _ in range(30):
            dt = await ig.evaluate(
                "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                "return d ? d.innerText.slice(0, 300) : ''; }"
            )
            if "說明文字" in dt or ("分享" in dt and len(dt) > 10):
                caption_found = True
                log.debug(f"[step5] Caption page detected: {repr(dt[:80])}")
                break
            if "裁切" in dt and _ > 3:
                # 仍在裁切頁，再點一次下一步
                log.debug(f"[step5] Still on crop page, retrying 下一步...")
                await _click_btn_in_dialog(ig, "下一步", timeout=5)
                await asyncio.sleep(1.5)
            await asyncio.sleep(0.5)

        if not caption_found:
            await browser.close()
            try:
                dt = await ig.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
            except Exception:
                dt = ""
            return f"❌ 輸入說明文字頁未出現: {repr(dt[:80])}"

        # 找 caption textbox（contenteditable div）
        for _ in range(10):
            try:
                boxes = ig.locator('[role="dialog"] [role="textbox"]')
                if await boxes.count() > 0:
                    await boxes.first.click(timeout=2000, force=True)
                    log.debug("[step5] Caption textbox clicked")
                    await asyncio.sleep(_random(0.3, 0.5))
                    break
            except Exception as e:
                log.debug(f"[step5] textbox attempt err: {e}")
            await asyncio.sleep(_random(0.3, 0.5))
        else:
            await browser.close()
            return "❌ 找不到 caption textbox"

        # 輸入 caption
        textbox = ig.locator('[role="dialog"] [role="textbox"]').first
        await textbox.fill(caption)
        log.debug(f"[step5] Caption filled: {len(caption)} chars")
        await asyncio.sleep(_random(1.0, 1.5))

        # 模擬人類打字後的光標操作（觸發 React onChange）
        await ig.keyboard.press("ArrowRight")
        await asyncio.sleep(_random(0.3, 0.5))

        # ── Step 6: 右上角「分享」────────────────────────────────────────────
        if not await _click_btn_in_dialog(ig, "分享", timeout=8):
            await browser.close()
            return "❌ 找不到「分享」按鈕"
        log.debug("[step6] 分享 clicked")
        await asyncio.sleep(_random(1.0, 1.5))

        # ── Step 7: 等「已分享」→「完成」────────────────────────────────────
        for i in range(50):
            await asyncio.sleep(1)
            try:
                dt = await ig.evaluate(
                    "() => { var d = document.querySelector('[role=\"dialog\"]'); "
                    "return d ? d.innerText.slice(0, 200) : ''; }"
                )
                if "已分享" in dt:
                    log.debug(f"[step7] ✅ 已分享（{i + 1}s）")
                    break
                if "發生錯誤" in dt or "錯誤" in dt:
                    await browser.close()
                    return f"❌ IG 發文錯誤: {repr(dt[:80])}"
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

        await asyncio.sleep(_random(0.5, 1.0))

        # 點「完成」或按 Escape
        found_done = await _click_btn_in_dialog(ig, "完成", timeout=5)
        if not found_done:
            log.debug("[step7] 完成 not found, pressing Escape")
            await ig.keyboard.press("Escape")
        else:
            log.debug("[step7] 完成 clicked")

        await asyncio.sleep(1)
        await browser.close()
        return "✅ Instagram 發文成功"


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if len(sys.argv) < 3:
        print("用法: python -m social_mcp.post_ig_human <caption> <image_path>")
        sys.exit(1)

    result = asyncio.run(post_ig_human(sys.argv[1], sys.argv[2]))
    print(result)
