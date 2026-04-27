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

THREADS_MAIN_URL = "https://www.threads.com/"
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
                if "threads.com/" in pg.url and "settings" not in pg.url:
                    threads_page = pg
                    break

            if not threads_page:
                return "❌ No threads.com tab. Open Threads in Chromium first."

            await threads_page.bring_to_front()
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
                # Dialog 沒打開 → 點 composer 按鈕
                # force=True：Threads 頁面常有 overlay 遮擋，強制點擊
                try:
                    await threads_page.locator(
                        'div[role="button"][aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]'
                    ).click(timeout=5000, force=True)
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
            # Step 3: 上傳圖片（可選）
            # ════════════════════════════════════════════════════════════════
            if image_path:
                try:
                    # 點「附加影音內容」
                    await threads_page.locator(
                        _svg_btn("附加影音內容")
                    ).last.click(timeout=3000, force=True)
                    await _random_delay(0.5, 0.8)

                    # Playwright set_input_files 注入 hidden file input
                    file_input = threads_page.locator('[role="dialog"] input[type="file"]').last
                    await file_input.set_input_files(image_path)
                    log.debug(f"Image set: {image_path}")
                    await _random_delay(1.5, 2.0)  # 等圖片預覽 render
                except Exception as e:
                    return f"❌ Image upload failed: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 4: 輸入文字（Playwright keyboard，擬人速度）
            # ════════════════════════════════════════════════════════════════
            try:
                # 輸入前先隨機滾動一點，模擬真實用戶
                await threads_page.evaluate("""
                    () => window.scrollBy(0, -window.innerHeight * 0.1)
                """)
                await _random_delay(0.2, 0.4)

                # 用 keyboard.type 輸入，delay 40-80ms/字元（擬人）
                delay_ms = random.randint(40, 80)
                await threads_page.keyboard.type(message, delay=delay_ms)
                await _random_delay(0.3, 0.6)

                # 驗證文字有沒有進去
                tb_text = await threads_page.locator(_textbox()).last.inner_text(timeout=3000)
                if not tb_text.strip():
                    return "❌ Text did not land in editor"
                log.debug(f"Editor text: {tb_text[:50]}")

            except Exception as e:
                return f"❌ Typing failed: {e}"

            # ════════════════════════════════════════════════════════════════
            # Step 5: 點「發佈」（dialog 內的 button，text 完全匹配）
            # ════════════════════════════════════════════════════════════════
            try:
                pub_btn = threads_page.locator(_btn("發佈")).last
                await pub_btn.scroll_into_view_if_needed(timeout=2000)
                await _random_delay(0.3, 0.5)
                await pub_btn.click(timeout=5000, force=True)
            except Exception as e:
                return f"❌ Cannot click 發佈: {e}"

            await _random_delay(1.0, 1.5)  # 等發佈完成

            # ════════════════════════════════════════════════════════════════
            # Step 6: 驗證（reload profile 確認 dialog 消失 + post 存在）
            # ════════════════════════════════════════════════════════════════
            if wait_verify:
                try:
                    await threads_page.locator('[role="dialog"]').last.wait_for(
                        timeout=5000, state="hidden"
                    )
                except Exception:
                    pass  # dialog 可能在發佈後自動 close

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
