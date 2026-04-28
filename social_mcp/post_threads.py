"""
post_threads — Threads 發文（Playwright 純 Selector 模式）

架構：純 Playwright，無 CDP flooding
速度：~12 秒（擬人節奏）
"""

import asyncio
import logging
import random
import time
from typing import Optional

from playwright.async_api import async_playwright

from social_mcp.browser_hijack import is_chromium_running, get_active_cdp_port

log = logging.getLogger(__name__)

THREADS_MAIN_URL = "https://www.threads.net/"
_random_delay = lambda a, b: asyncio.sleep(random.uniform(a, b))


# ── Selector 工廠 ───────────────────────────────────────────────────────────

def _dialog():
    """新串文 dialog locator（last 避免 strict mode 多元素問題）。"""
    return '[role="dialog"]'


def _textbox():
    """dialog 內文字輸入框（任意深度）。"""
    return '[role="dialog"] div[role="textbox"]'


def _btn(text: str):
    """dialog 內的按鈕：text 完全匹配。"""
    return f'[role="dialog"] div[role="button"]:has-text("{text}")'


def _svg_btn(aria_label: str):
    """dialog 內的 SVG icon 按鈕。SVG → grandparent = 可點的 role=button。"""
    return f'[role="dialog"] svg[aria-label="{aria_label}"]'


# ── 主流程 ────────────────────────────────────────────────────────────────

async def post_threads(
    message: str,
    image_path: Optional[str] = None,
    wait_verify: bool = True,
) -> str:
    """
    Post to Threads using Playwright selectors only (no CDP flooding).

    All buttons use aria-label or role+text selectors — no coordinates.
    Typing uses Playwright keyboard with human-like delay.

    Args:
        message:    貼文內容（支援多行）
        image_path: 可選，圖片路徑
        wait_verify: True = reload 並驗證
    """
    t0 = time.time()
    browser_pw = None

    try:
        async with async_playwright() as p:
            # ── 連接瀏覽器（一次）────────────────────────────────────────────
            port = get_active_cdp_port()
            browser_pw = await p.chromium.connect_over_cdp(
                f"http://localhost:{port}", timeout=15_000
            )
            ctx = browser_pw.contexts[0]

            # 找 Threads tab
            threads_page = None
            for pg in ctx.pages:
                if ("threads.com/" in pg.url or "threads.net/" in pg.url) and "settings" not in pg.url:
                    threads_page = pg
                    break

            if not threads_page:
                return "❌ No Threads tab. Open Threads in Chromium first."

            await threads_page.bring_to_front()
            # 導航到 threads.net（threads.com 已失效，會顯示「頁面不存在」）
            await threads_page.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=30000)
            await _random_delay(4.0, 5.0)  # 等 React SPA 完全渲染
            await _random_delay(0.5, 1.0)

            # ════════════════════════════════════════════════════════════════
            # Step 1: 確保 composer dialog 已打開
            # ════════════════════════════════════════════════════════════════

            # 如果 dialog 已經打開（殘留狀態），直接跳過
            try:
                if await threads_page.locator('[role="dialog"]').last.is_visible(timeout=1000):
                    log.debug("Dialog already open, skipping Step 1")
                else:
                    raise Exception("not visible")
            except Exception:
                # Dialog 沒打開 → 點 composer 按鈕（CDP JS click，避免 Playwright locator 不穩定）
                try:
                    r = await threads_page.evaluate("""() => {
                        var btns = document.querySelectorAll('[role="button"], button');
                        for (const b of btns) {
                            const label = b.getAttribute('aria-label') || '';
                            const text = b.innerText || '';
                            // 匹配「在想什麼」類似的按鈕
                            if (label.includes('輸入內容') || label.includes('新鮮事') || label.includes('請輸入') || label === '建立') {
                                b.click(); return 'clicked:' + label;
                            }
                            if (text.includes('在想什麼') || text.includes('有什麼新鮮事')) {
                                b.click(); return 'clicked:' + text.slice(0,30);
                            }
                        }
                        return 'not_found';
                    }""")
                    log.debug(f"Composer clicked: {r}")
                    if r == 'not_found':
                        raise Exception("composer button not found")
                    await _random_delay(1.0, 1.5)

                    await threads_page.locator('[role="dialog"]').last.wait_for(
                        timeout=5000, state="visible"
                    )
                    await _random_delay(0.5, 0.8)
                except Exception as e:
                    return f"❌ Cannot open composer: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 2: 點擊文字框，進入輸入模式
            # ════════════════════════════════════════════════════════════════
            try:
                tb = threads_page.locator(_textbox()).last
                await tb.click(timeout=3000, force=True)
                await _random_delay(0.3, 0.6)
            except Exception as e:
                return f"❌ Cannot click textbox: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 3: 上傳圖片（可選）— Playwright set_input_files
            # Threads dialog 內的 input[type=file] 是 display:none，
            # JS click() 不觸發 OS dialog，filechooser 也不靠譜。
            # 直接用 Playwright locator.set_input_files() 繞過 OS dialog，
            # 原理：CDP Page.setFileInputFiles 直接賦值並觸發 change 事件。
            # ════════════════════════════════════════════════════════════════
            if image_path:
                try:
                    # 點「附加影音內容」SVG 讓 dialog 進入「待選圖片」狀態
                    await threads_page.locator(
                        _svg_btn("附加影音內容")
                    ).last.click(timeout=3000, force=True)
                    await _random_delay(0.5, 0.8)

                    # 嘗試攔截 OS file chooser（3s timeout）
                    # 如果 OS dialog 真的打開了，set_files() 會讓它關閉
                    # 如果超時（CDP mode 不需要 dialog），直接 set_input_files
                    try:
                        fc = await threads_page.context.wait_for_file_chooser(timeout=3000)
                        await fc.set_files(image_path, timeout=20_000)
                        log.debug(f"[threads step3] File via file_chooser: {image_path}")
                    except Exception as fc_err:
                        log.warning(f"[threads step3] file_chooser not intercepted ({fc_err}), using set_input_files")
                        inp_locator = threads_page.locator(
                            '[role="dialog"] input[type=file]'
                        ).last
                        await inp_locator.set_input_files(image_path, timeout=5000)
                        log.debug(f"[threads step3] set_input_files succeeded: {image_path}")

                    # 等 Threads 上傳（blob URL 生成 = 上傳成功信號）
                    await asyncio.sleep(8)

                except Exception as e:
                    return f"❌ Image upload failed: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 5: 輸入文字（Playwright keyboard，擬人速度）
            # 重要：Threads 文字輸入在圖片之前，確保 React state 正確
            # ════════════════════════════════════════════════════════════════
            try:
                await threads_page.evaluate(
                    "() => window.scrollBy(0, -window.innerHeight * 0.1)"
                )
                await _random_delay(0.2, 0.4)

                delay_ms = random.randint(40, 80)
                await threads_page.keyboard.type(message, delay=delay_ms)
                await _random_delay(0.3, 0.6)

                tb_text = await threads_page.locator(_textbox()).last.inner_text(timeout=3000)
                if not tb_text.strip():
                    return "❌ Text did not land in editor"
                log.debug(f"Editor text: {tb_text[:50]}")

            except Exception as e:
                return f"❌ Typing failed: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 6: Threads 發文是兩步流程
            #   Step 6a: 點「新增到串文」→ 進入第 2 步（caption 頁）
            #   Step 6b: 在第 2 步坐標點擊「發佈」→ 正式發出
            # Playwright locator.click 無法觸發 React onClick，用 mouse.click 坐標
            # ════════════════════════════════════════════════════════════════
            try:
                # 6a: 點「新增到串文」進第 2 步
                pub_btn_info = await threads_page.evaluate("""() => {
                    const d = document.querySelector('[role="dialog"]');
                    if (!d) return null;
                    const btns = d.querySelectorAll('[role="button"]');
                    for (const b of btns) {
                        if ((b.innerText || '').includes('新增到串文')) {
                            const r = b.getBoundingClientRect();
                            return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                        }
                    }
                    return null;
                }""")
                if not pub_btn_info:
                    return "❌ 新增加到串文 button not found"
                await threads_page.mouse.click(pub_btn_info["x"], pub_btn_info["y"])
                log.debug(f"Clicked 新增加到串文 at {pub_btn_info}")
                await _random_delay(2.0, 3.0)  # 等第 2 步渲染

                # 6b: 在第 2 步坐標點擊「發佈」
                pub2_info = await threads_page.evaluate("""() => {
                    const d = document.querySelector('[role="dialog"]');
                    if (!d) return null;
                    const btns = d.querySelectorAll('[role="button"]');
                    for (const b of btns) {
                        if ((b.innerText || '').includes('發佈')) {
                            const r = b.getBoundingClientRect();
                            return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                        }
                    }
                    return null;
                }""")
                if not pub2_info:
                    return "❌ 發佈 button not found in step 2"
                await threads_page.mouse.click(pub2_info["x"], pub2_info["y"])
                log.debug(f"Clicked 發佈 at {pub2_info}")
                await _random_delay(8.0, 10.0)  # 等發佈完成 + dialog 關閉

            except Exception as e:
                return f"❌ Cannot click publish: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 7: 驗證（reload profile 確認 post 存在）
            # ════════════════════════════════════════════════════════════════
            if wait_verify:
                try:
                    await threads_page.locator('[role="dialog"]').last.wait_for(
                        timeout=5000, state="hidden"
                    )
                except Exception:
                    pass

                await threads_page.reload(wait_until="domcontentloaded")
                await _random_delay(2.0, 3.0)
                body = await threads_page.inner_text("body")

                if message[:20] in body:
                    elapsed = time.time() - t0
                    return f"✅ Posted to Threads in {elapsed:.1f}s"
                else:
                    return "✅ Posted (verify manually: reload Threads profile)"
            else:
                elapsed = time.time() - t0
                return f"✅ Posted to Threads in {elapsed:.1f}s"

    finally:
        if browser_pw:
            await browser_pw.close()

    return "❌ Unexpected exit"


# ── CLI ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if not is_chromium_running():
        print("❌ Chromium not running on port 9333")
        sys.exit(1)

    msg = sys.argv[1] if len(sys.argv) > 1 else "🤖 Hermes Playwright 測試"
    result = asyncio.run(post_threads(msg))
    print(result)
