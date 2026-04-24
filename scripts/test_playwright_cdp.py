#!/usr/bin/env python3
"""
Test Playwright CDP attach to Chrome started with remote-debugging-port.
"""
import asyncio
import subprocess
import time
import sys

async def main():
    import os
    from playwright.async_api import async_playwright

    SYS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    TEMP_DIR = "/tmp/chrome-debug-social-mcp"
    PORT = 9333

    # 清理舊的 temp dir
    subprocess.run(["/bin/rm", "-rf", TEMP_DIR], check=False)
    os.makedirs(TEMP_DIR, exist_ok=True)

    print(f"[1] 啟動 Chrome with remote-debugging-port={PORT}, user-data-dir={TEMP_DIR}")

    cmd = [
        SYS_CHROME,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={TEMP_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--headless=new",
        "--enable-unsafe-swiftshader",
        "--window-size=1280,800",
        "--disable-background-networking",
        "--enable-features=NetworkService",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"    PID={proc.pid}")

    # 等 Chrome 啟動
    await asyncio.sleep(5)

    if proc.poll() is not None:
        print("[!] Chrome 啟動後立即退出（可能是 Singleton lock）")
        print("    需要先關閉所有 Chrome 實例，或用 profile-directory 參考現有 profile")
        return

    print(f"[2] Playwright 嘗試連線到 ws://localhost:{PORT}")
    try:
        async with async_playwright() as p:
            # 嘗試 CDP 連線
            browser = await p.chromium.connect_over_cdp(f"http://localhost:{PORT}")
            print(f"[3] CDP 連線成功！Browser: {browser.browser_type.name}")

            contexts = browser.contexts
            print(f"    {len(contexts)} browser contexts")

            for i, ctx in enumerate(contexts):
                print(f"    Context[{i}]: {len(ctx.pages)} pages")
                for j, page in enumerate(ctx.pages):
                    print(f"      Page[{j}]: {page.url}")

            # 嘗試開一個新 page 導航到 Facebook
            print(f"[4] 導航到 https://www.messenger.com")
            ctx = contexts[0] if contexts else browser.contexts[0]
            page = await ctx.new_page()
            await page.goto("https://www.messenger.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            print(f"    URL: {page.url}")
            title = await page.title()
            print(f"    Title: {title}")

            # 看看有沒有登入相關文字
            content = await page.content()
            if "登入" in content or "登入" in str(content):
                print("    ⚠️  可能未登入（檢測到登入相關文字）")
            else:
                print("    ✅ 可能有登入狀態")

            await page.close()

    except Exception as e:
        print(f"[!] 連線失敗: {e}")

    finally:
        print("[5] 關閉 Chrome")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

if __name__ == "__main__":
    asyncio.run(main())
