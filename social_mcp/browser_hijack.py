"""
browser_hijack — CDP Browser Hijacking for Meta Social Platforms

原理：
1. 啟動 ungoogled-chromium（Chromium）with --remote-debugging-port
   使用獨立的 FacebookMCP profile（你自己在瀏覽器裡登入一次）
2. CDP 連線到 running browser
3. Playwright 接管頁面 DOM，直接操作 Facebook Web UI

關鍵洞察：
- macOS Chrome 的 cookies 使用 macOS Keychain 加密
- 外部程序無法直接讀取（crypto key 在 login keychain 裡）
- 解決方案：用 Chromium 的 remote debugging，透過 CDP 直接操控瀏覽器 session

流程：
1. 用戶在 Chromium（FacebookMCP profile）手動登入 Facebook（只需一次）
2. CDP 接管 → 完全 headless 操作 DOM
3. 永遠不需要 API Token、不需要 OAuth、不需要開發者帳號
"""

import asyncio
import json
import logging
import os
import subprocess
import urllib.request
from typing import Optional

from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

# ungoogled-chromium 預設路徑
CHROMIUM_PATH = "/Applications/Chromium.app/Contents/MacOS/Chromium"
CHROMIUM_PROFILE = os.path.expanduser("~/Library/Application Support/Chromium/FacebookMCP")

# 支援多個 CDP 端口，自動選擇已登入的那個
CDP_PORTS = [9333, 9222]


def _get_active_port() -> Optional[int]:
    """Return the first CDP port that has a running Chromium, or None."""
    for port in CDP_PORTS:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/json/version",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status == 200:
                    log.info(f"[BrowserHijack] Active CDP port: {port}")
                    return port
        except Exception:
            pass
    return None


def find_chromium_ws() -> Optional[str]:
    """Find the WebSocket URL for the first Chrome tab on any active CDP port."""
    for port in CDP_PORTS:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/json",
                headers={"User-Agent": "Mozilla/5.0 Chrome-CDP-Client"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                tabs = json.loads(resp.read())
                for tab in tabs:
                    if tab.get("type") == "page":
                        return tab.get("webSocketDebuggerUrl")
                if tabs:
                    return tabs[0].get("webSocketDebuggerUrl")
        except Exception:
            pass
    return None


def is_chromium_running() -> bool:
    """Check if Chromium is already running on any CDP port."""
    return _get_active_port() is not None


def get_active_cdp_port() -> int:
    """Return the active CDP port, or first port as default."""
    return _get_active_port() or CDP_PORTS[0]


def launch_chromium() -> Optional[int]:
    """Launch ungoogled-chromium with remote debugging. Returns PID or None."""
    port = _get_active_port()
    if port:
        log.info(f"[BrowserHijack] Chromium already running on port {port}")
        return None

    # Clear singleton locks that might prevent launch
    for lock_file in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        path = os.path.join(CHROMIUM_PROFILE, lock_file)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    cmd = [
        CHROMIUM_PATH,
        "--remote-debugging-port=9333",
        f"--user-data-dir={CHROMIUM_PROFILE}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1280,720",
    ]

    log.info(f"[BrowserHijack] Launching Chromium with profile: {CHROMIUM_PROFILE}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for CDP port to be ready
    for _ in range(15):
        if is_chromium_running():
            log.info(f"[BrowserHijack] Chromium ready, PID={proc.pid}")
            return proc.pid
        asyncio.sleep(1)

    log.error("[BrowserHijack] Chromium failed to start")
    return None


async def connect_to_facebook_page() -> Optional:
    """
    Connect to the running Chromium and return the Facebook page CDP connection.
    Automatically uses whichever port has an active Chromium session.
    """
    ws_url = find_chromium_ws()
    if not ws_url:
        log.error("[BrowserHijack] No Chromium tab found")
        return None

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url)
        ctx = browser.contexts[0]
        fb_page = None
        for pg in ctx.pages:
            if "facebook.com" in pg.url:
                fb_page = pg
                break
        return browser, ctx, fb_page


async def ensure_chromium():
    """Ensure Chromium is running with CDP enabled."""
    if not is_chromium_running():
        log.info("[BrowserHijack] Chromium not running, launching...")
        pid = launch_chromium()
        if not pid:
            raise RuntimeError("Failed to launch Chromium")
        await asyncio.sleep(3)  # Wait for browser to fully start
