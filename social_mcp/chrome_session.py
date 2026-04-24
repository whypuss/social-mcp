"""
Chrome Session — 透過 CDP (Chrome DevTools Protocol) 取得 Facebook/IG/Threads 的 cookies。

策略：
1. 嘗試連接 AIpuss daemon 的 CDP WebSocket（已連接 Chrome for Testing，包含你的登入狀態）
2. 萬一 AIpuss 沒運行，則啟動一個獨立的 headful Chrome 並馬上注入 CDP session
3. 從 CDP 的 Network.getCookies 取得目標域名的 cookies
"""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
import websockets
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

AIpuss_STREAM_FILE = os.path.expanduser("~/.agent-browser/default.stream")
AIpuss_SOCK_FILE = os.path.expanduser("~/.agent-browser/default.sock")
CHROME_FOR_TESTING_BROWSER_DIR = os.path.expanduser(
    "~/.agent-browser/browsers/chrome-147.0.7727.56"
)
CHROME_PROFILE_HELPERS = {
    "Default": "Default",
    "Profile 1": "Profile 1",
    "Profile 2": "Profile 2",
    "Profile 3": "Profile 3",
    "MY": "Profile 3",
}
CHROME_APP = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@dataclass
class ChromeCookie:
    name: str
    value: str
    domain: str
    path: str


async def get_aipuss_cdp_ws_url() -> Optional[str]:
    """從 AIpuss daemon 的 WebSocket 取得 CDP URL。"""
    try:
        stream_port = open(AIpuss_STREAM_FILE).read().strip()
        ws_url = f"ws://localhost:{stream_port}"
        log.info(f"[ChromeSession] AIpuss CDP WebSocket: {ws_url}")
        return ws_url
    except Exception as e:
        log.warning(f"[ChromeSession] 無法讀取 AIpuss stream port: {e}")
        return None


async def cdp_send(ws, method: str, params: dict = None, id: int = 1) -> dict:
    """發送 CDP 命令，等待回應。"""
    payload = {"id": id, "method": method}
    if params:
        payload["params"] = params
    await ws.send(json.dumps(payload))
    while True:
        msg = await ws.recv()
        data = json.loads(msg)
        if data.get("id") == id:
            return data.get("result", {})


async def get_cookies_via_aipuss(domains: list[str]) -> list[ChromeCookie]:
    """透過 AIpuss daemon 的 CDP 取得指定域名的 cookies。"""
    ws_url = await get_aipuss_cdp_ws_url()
    if not ws_url:
        return []

    try:
        async with websockets.connect(ws_url, ping_interval=None) as ws:
            # 先確保 Target 已創建（等一下讓子頁面初始化）
            await asyncio.sleep(1)

            # CDP: Page.navigate 觸發新目標
            # 不——我們直接問現有目標的 cookies
            # 透過 Storage.getCookies
            result = await cdp_send(ws, "Storage.getCookies", {"browser": True})
            all_cookies = result.get("cookies", [])
            log.info(f"[ChromeSession] 瀏覽器總 cookies 數: {len(all_cookies)}")

            # 只取目標域名
            cookies = []
            for c in all_cookies:
                domain = c.get("domain", "")
                if any(d in domain or domain.endswith(d.lstrip(".")) for d in domains):
                    cookies.append(ChromeCookie(
                        name=c.get("name", ""),
                        value=c.get("value", ""),
                        domain=domain,
                        path=c.get("path", "/"),
                    ))
            log.info(f"[ChromeSession] 過濾後 {len(cookies)} 個 cookies")
            return cookies

    except Exception as e:
        log.error(f"[ChromeSession] CDP 連接失敗: {e}")
        return []


def get_cookies_from_chrome_for_testing(domains: list[str]) -> list[ChromeCookie]:
    """
    直接啟動 Chrome for Testing 並馬上 CDP，繞過 headless 限制。
    這是備用方案（Profile 3 在 Chrome for Testing 裡）。
    """
    log.info("[ChromeSession] 啟動 Chrome for Testing (CDP mode)...")

    # 找一個閒置的端口
    with socket.socket() as s:
        s.bind(("", 0))
        cdp_port = s.getsockname()[1]

    chrome_path = os.path.join(
        CHROME_FOR_TESTING_BROWSER_DIR,
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    )
    if not os.path.exists(chrome_path):
        log.error(f"[ChromeSession] Chrome for Testing 不存在: {chrome_path}")
        return []

    user_data_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome for Testing")
    profile_dir = os.path.join(user_data_dir, "Profile 3")

    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={os.path.dirname(user_data_dir)}",
        f"--profile-directory=Profile 3",
        "--no-first-run",
        "--no-default-browser-check",
        "--headless=new",
        "--enable-unsafe-swiftshader",
        "https://www.facebook.com",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)  # 等 Chrome 啟動

    try:
        import chrome_remote_interface as cri
        client = cri.Client(port=cdp_port)
        client.Page.enable()
        client.Network.enable()

        # 等待 page load 完成
        time.sleep(4)

        # 取得所有 cookies
        _, resp = client.Network.getCookies()
        raw_cookies = resp.get("cookies", [])
        client.close()

        cookies = []
        for c in raw_cookies:
            domain = c.get("domain", "")
            if any(d in domain or domain.endswith(d.lstrip(".")) for d in domains):
                cookies.append(ChromeCookie(
                    name=c.get("name", ""),
                    value=c.get("value", ""),
                    domain=domain,
                    path=c.get("path", "/"),
                ))
        return cookies
    except Exception as e:
        log.error(f"[ChromeSession] Chrome for Testing CDP 失敗: {e}")
        return []
    finally:
        proc.terminate()


async def get_meta_cookies(domains: list[str] = None) -> list[ChromeCookie]:
    """
    主入口：優先用 AIpuss CDP，失敗則用 Chrome for Testing 備用。
    """
    if domains is None:
        domains = [".facebook.com", ".instagram.com", ".threads.net"]

    # 策略1: AIpuss CDP
    cookies = await get_cookies_via_aipuss(domains)
    if cookies:
        return cookies

    # 策略2: 獨立 Chrome for Testing
    return get_cookies_from_chrome_for_testing(domains)


def cookies_to_dict(cookies: list[ChromeCookie]) -> dict:
    """轉成 requests 用的 dict（name=value; Domain=...; Path=...）"""
    parts = []
    for c in cookies:
        part = f"{c.name}={c.value}; Domain={c.domain}; Path={c.path}; SameSite=None"
        parts.append(part)
    return "; ".join(parts)


def cookies_to_simple_dict(cookies: list[ChromeCookie]) -> dict:
    """轉成簡單的 name=value dict（用於 httpx）"""
    return {c.name: c.value for c in cookies}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cookies = asyncio.run(get_meta_cookies())
    for c in cookies:
        print(f"{c.name}={c.value[:30]}... (domain={c.domain})")
