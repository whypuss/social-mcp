"""
Meta Workflow — MCP Server via CDP Browser Hijacking

用法：
  uv run social-mcp                    # 直接啟動（blocking，等待 stdio）
  uv run python -m social_mcp.post_facebook "你的發文內容"   # 單獨發文

在 Hermes Agent 的 ~/.hermes/config.yaml 加入：
  mcp_servers:
    personal-social:
      command: "/path/to/.venv/bin/python"
      args: ["/path/to/social_mcp/mcp_server.py"]

在 Claude Desktop 的 claude_desktop_config.json 加入：
{
  "mcpServers": {
    "social": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/social-mcp", "social-mcp"]
    }
  }
}
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from mcp.server.fastmcp import FastMCP

from .browser_hijack import (
    ensure_chromium,
    connect_to_facebook_page,
    is_chromium_running,
    launch_chromium,
    CHROMIUM_PROFILE,
)

log = logging.getLogger(__name__)

mcp = FastMCP("Personal_Social")

# ─────────────────────────────────────────────
# TOOL 1: Open login window
# ─────────────────────────────────────────────
@mcp.tool()
async def open_login_window():
    """
    Launch visible Chromium browser so you can manually log in to Facebook.
    Run this ONCE when setting up — or whenever the session expires.

    The browser will stay open. After you log in, close the browser window.
    Subsequent calls to other tools will use this logged-in session.
    """
    await ensure_chromium()

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            CHROMIUM_PROFILE,
            headless=False,
            viewport={"width": 1280, "height": 800},
            no_viewport=False,
        )
        page = await browser.new_page()
        await page.goto("https://www.facebook.com")
        await browser.wait_for_close()

    return "Browser closed. Session saved in FacebookMCP profile."


# ─────────────────────────────────────────────
# TOOL 2: Post to Facebook (personal wall)
# ─────────────────────────────────────────────
@mcp.tool()
async def post_facebook(message: str):
    """
    Post a text message to your personal Facebook wall.

    Requires: Chromium running on port 9333 with a logged-in FacebookMCP profile.
    First run open_login_window() if you haven't logged in yet.
    """
    if not is_chromium_running():
        return "❌ Chromium not running. Run open_login_window() first."

    await ensure_chromium()
    await asyncio.sleep(2)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:9333")
        ctx = browser.contexts[0]
        fb_page = None
        for pg in ctx.pages:
            if "facebook.com" in pg.url:
                fb_page = pg
                break

        if not fb_page:
            return "❌ No Facebook page found. Open facebook.com in the Chromium tab first."

        await fb_page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        body = await fb_page.inner_text("body")
        if "登入" in body[:400] and "電子郵件" in body[:400]:
            return "❌ Not logged in. Run open_login_window() first."

        # Click composer
        try:
            composer = fb_page.locator('[aria-label="建立帖子"]').first
            await composer.click(timeout=8000)
            await asyncio.sleep(2)
        except Exception as e:
            return f"❌ Could not open composer: {e}"

        # Type message
        await fb_page.keyboard.type(message, delay=20)
        await asyncio.sleep(1)

        # Click "下一頁" to advance multi-step composer
        try:
            next_btn = fb_page.locator('[aria-label="下一頁"]').first
            await next_btn.click(timeout=5000)
            await asyncio.sleep(2)
        except Exception:
            pass  # Some accounts don't need this

        # Click "發佈"
        try:
            post_btn = fb_page.locator('[aria-label="發佈"]').first
            await post_btn.click(timeout=8000)
            await asyncio.sleep(4)
        except Exception as e:
            return f"❌ Could not click post button: {e}"

        # Verify
        body = await fb_page.inner_text("body")
        if "Hermes" in body or "自動發文" in body or "發佈" in body:
            return f"✅ Post published successfully!"
        else:
            return "⚠️ Post may have been published. Check your Facebook wall."


# ─────────────────────────────────────────────
# TOOL 3: Read Messenger inbox
# ─────────────────────────────────────────────
@mcp.tool()
async def read_messenger():
    """
    Read your Messenger conversations as a markdown table.

    Requires: Chromium running with logged-in FacebookMCP profile.
    """
    if not is_chromium_running():
        return "❌ Chromium not running. Run open_login_window() first."

    await ensure_chromium()
    await asyncio.sleep(2)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:9333")
        ctx = browser.contexts[0]
        fb_page = None
        for pg in ctx.pages:
            if "facebook.com" in pg.url:
                fb_page = pg
                break

        if not fb_page:
            return "❌ No Facebook page found."

        await fb_page.goto(
            "https://www.facebook.com/messages", wait_until="domcontentloaded"
        )
        await asyncio.sleep(3)

        body = await fb_page.inner_text("body")
        if "登入" in body[:400] and "電子郵件" in body[:400]:
            return "❌ Not logged in. Run open_login_window() first."

        # Find conversation list
        selectors = [
            'div[role="gridcell"]',
            'ul[role="listbox"] li',
            '[aria-label*="訊息"]',
        ]

        entries = []
        for sel in selectors:
            try:
                elems = await fb_page.locator(sel).all()
                if elems:
                    for e in elems:
                        txt = (await e.inner_text()).strip()
                        if txt:
                            entries.append(txt)
                    if entries:
                        break
            except Exception:
                pass

        if not entries:
            return f"⚠️ Could not read conversations. Check if Messenger loaded."

        res = "### Messenger 私訊摘要\n\n| 對話 |\n| :--- |\n"
        for entry in entries[:8]:
            lines = [l.strip() for l in entry.split("\n") if l.strip()]
            clean = " | ".join(lines[:3])
            res += f"| {clean} |\n"
        return res


# ─────────────────────────────────────────────
# TOOL 4: Read Facebook notifications
# ─────────────────────────────────────────────
@mcp.tool()
async def read_notifications():
    """
    Fetch your Facebook notifications as a markdown table.

    Requires: Chromium running with logged-in FacebookMCP profile.
    """
    if not is_chromium_running():
        return "❌ Chromium not running. Run open_login_window() first."

    await ensure_chromium()
    await asyncio.sleep(2)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://localhost:9333")
        ctx = browser.contexts[0]
        fb_page = None
        for pg in ctx.pages:
            if "facebook.com" in pg.url:
                fb_page = pg
                break

        if not fb_page:
            return "❌ No Facebook page found."

        await fb_page.goto(
            "https://www.facebook.com/notifications", wait_until="domcontentloaded"
        )
        await asyncio.sleep(3)

        body = await fb_page.inner_text("body")
        if "登入" in body[:400] and "電子郵件" in body[:400]:
            return "❌ Not logged in. Run open_login_window() first."

        selectors = [
            'div[role="article"]',
            'div[data-pagelet*="Noti"]',
            'div.notificationsItem',
        ]

        entries = []
        for sel in selectors:
            try:
                elems = await fb_page.locator(sel).all()
                if elems:
                    for e in elems:
                        txt = (await e.inner_text()).strip()
                        if txt and len(txt) > 5:
                            entries.append(txt)
                    if entries:
                        break
            except Exception:
                pass

        if not entries:
            return f"⚠️ Could not read notifications."

        res = "### Facebook 通知摘要\n\n| 通知 |\n| :--- |\n"
        for entry in entries[:8]:
            lines = [l.strip() for l in entry.split("\n") if l.strip()]
            clean = " | ".join(lines[:2])
            res += f"| {clean} |\n"
        return res


if __name__ == "__main__":
    # Parse --debug flag
    parser = argparse.ArgumentParser(description="social-mcp")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
    else:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    log.info("[meta-workflow] Starting MCP server via CDP Browser Hijacking...")
    log.info("[meta-workflow] Profile: %s", CHROMIUM_PROFILE)
    log.info("[meta-workflow] CDP port: %d", CDP_PORT)
    log.info(
        "[meta-workflow] First time? Run open_login_window() to log in to Facebook."
    )

    mcp.run()
