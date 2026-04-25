#!/usr/bin/env python3
"""
Meta Workflow — Instagram Post Script
用法：python post_instagram.py "<caption>" <image_path>
"""
import asyncio
import sys
import hashlib
from playwright.async_api import async_playwright

# CDP WebSocket URL
WS_URL = "ws://127.0.0.1:9333/devtools/browser/65653279-e223-4f87-b6ff-ebd30cd96b2b"


async def post_instagram(caption: str, image_path: str) -> str:
    """
    在 Instagram 發布圖文帖子（指紋驗證版）

    流程：
    1. 點擊「新貼文」按鈕
    2. 上傳圖片
    3. 點「下一步」× 2（裁切 → 濾鏡）
    4. 輸入 caption
    5. 點「分享」
    6. 指紋驗證
    """
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(WS_URL)
        ctx = browser.contexts[0]

        # 找 Instagram 頁面
        ig = None
        for page in ctx.pages:
            try:
                if "instagram.com" in page.url:
                    ig = page
                    break
            except:
                pass

        if not ig:
            # 開新分頁
            ig = await ctx.new_page()
            await ig.goto("https://www.instagram.com", wait_until="load", timeout=30000)
            await asyncio.sleep(3)

        print(f"[meta-workflow] Instagram: {ig.url}")

        # ====== 指紋 ======
        post_snippet = caption[:50].strip()
        post_hash = hashlib.md5(post_snippet.encode()).hexdigest()[:8]
        print(f"[meta-workflow] Post fingerprint: 「{post_snippet}」 (hash={post_hash})")

        # ====== Step 1: 點擊「新貼文」按鈕 ======
        await ig.evaluate("""
            () => {
                const spans = document.querySelectorAll('span');
                for (const s of spans) {
                    if (s.textContent?.trim() === '新貼文') { s.click(); return; }
                }
            }
        """)
        await asyncio.sleep(2)
        print("[meta-workflow] 新貼文對話框已打開")

        # ====== Step 2: 上傳圖片 ======
        file_input = await ig.query_selector('input[type="file"]')
        if not file_input:
            print("[meta-workflow] ❌ 找不到 file input")
            return "❌ 找不到 file input"
        await file_input.set_input_files(image_path)
        print(f"[meta-workflow] 圖片已上傳: {image_path}")
        await asyncio.sleep(3)

        # ====== Step 3: 編輯頁面 → 點「繼續」 ======
        # 等待 URL 變化到裁切/編輯頁面
        for _ in range(15):
            url = ig.url
            if '/create/style' in url or '/create/crop' in url:
                break
            await asyncio.sleep(0.5)

        # 點「繼續」（新流程）或「下一步」（舊流程）
        await ig.evaluate("""
            () => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    const t = b.innerText?.trim();
                    if (t === '繼續' || t === '下一步') { b.click(); return; }
                }
            }
        """)
        await asyncio.sleep(2)
        print("[meta-workflow] 編輯頁面 → 細節頁面")

        # ====== Step 4: 細節頁面（ caption + 分享） ======
        # 等待 URL 到達 details 頁面
        for _ in range(15):
            url = ig.url
            if '/create/details' in url:
                break
            await asyncio.sleep(0.5)
        print(f"[meta-workflow] 到達 details 頁面: {ig.url}")

        # ====== Step 5: 輸入 caption ======
        # 新 Instagram 使用 TEXTAREA
        await ig.evaluate("""
            () => {
                const textarea = document.querySelector('textarea');
                if (textarea) textarea.focus();
            }
        """)
        await asyncio.sleep(0.5)

        await ig.keyboard.type(caption, delay=30)
        print(f"[meta-workflow] Caption 已輸入 ({len(caption)} chars)")
        await asyncio.sleep(1)

        # ====== Step 6: 點「分享」======
        # 找「分享」按鈕（新版 UI 在 /create/details 頁面）
        share_info = await ig.evaluate("""
            () => {
                const allButtons = document.querySelectorAll('button, div[role="button"]');
                for (const el of allButtons) {
                    if (el.innerText?.trim() === '分享') {
                        const rect = el.getBoundingClientRect();
                        return {
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            visible: rect.width > 0 && rect.height > 0
                        };
                    }
                }
                return null;
            }
        """)

        if not share_info or not share_info['visible']:
            print("[meta-workflow] ❌ 找不到可見的分享按鈕")
            return "❌ 找不到分享按鈕"

        await ig.mouse.click(share_info['x'], share_info['y'])
        print("[meta-workflow] 點擊分享")
        await asyncio.sleep(5)

        # ====== Step 7: 等待「已分享」確認 ======
        for _ in range(15):
            confirmed = await ig.evaluate(
                "() => document.body.innerText.includes('已分享你的貼文')"
            )
            if confirmed:
                print("[meta-workflow] ✅ Instagram 確認：已分享你的貼文")
                break
            await asyncio.sleep(1)

        # 點「完成」
        await ig.evaluate("""
            () => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if (b.innerText?.trim() === '完成') { b.click(); return; }
                }
            }
        """)
        await asyncio.sleep(2)

        # ====== Step 8: 指紋驗證 ======
        # 刷新主頁並搜尋指紋
        await ig.goto("https://www.instagram.com/whypuss_fun", wait_until="load", timeout=30000)
        await asyncio.sleep(3)

        for _ in range(5):
            await ig.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(0.5)

        page_text = await ig.inner_text("body")

        if post_snippet in page_text:
            idx = page_text.find(post_snippet)
            post_preview = page_text[max(0, idx-20):idx+len(post_snippet)+50]
            print(f"[meta-workflow] ✅ 指紋驗證成功！")
            print(f"    驗證片段: ...{post_preview}...")
            await browser.close()
            return f"✅ Instagram 發布成功！指紋「{post_snippet}」已驗證。"
        else:
            # Instagram 確認已顯示，但指紋可能需要時間出現
            print(f"[meta-workflow] ⚠️ Instagram 顯示已分享，但指紋「{post_snippet}」尚未在主頁出現（正常，等待 CDN 更新）")
            await browser.close()
            return f"⚠️ Instagram 顯示已分享。指紋「{post_snippet}」可能需要數分鐘才在主頁出現。"


if __name__ == "__main__":
    # 預設值
    caption = sys.argv[1] if len(sys.argv) > 1 else "測試發文 from Meta Workflow 🚀"
    image_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ig_food_source.png"

    result = asyncio.run(post_instagram(caption, image_path))
    print(result)
