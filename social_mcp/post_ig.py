"""
post_ig.py — Instagram 圖文發文腳本（2026-04-26 實測）

完整流程：
1. 點擊「新貼文」→ 選擇圖片 → 打開
2. 裁切頁 → 按下一步
3. 編輯/濾鏡頁 → 按下一步
4. caption頁（撰寫說明文字）→ 輸入 caption → 分享
5. 「已分享你的貼文」→ 按完成

用法：
    uv run python -m social_mcp.post_ig "caption text" /path/to/image.jpg
"""
import asyncio
import json
import sys
import time
import urllib.request
import websockets
from playwright.async_api import async_playwright

IG_URL = "https://www.instagram.com"


def _get_active_port():
    for port in [9333, 9222]:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/json/version",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                if r.status == 200:
                    return port
        except Exception:
            pass
    return 9333


def _get_ig_ws_url(port: int) -> str | None:
    try:
        req = urllib.request.Request(
            f"http://localhost:{port}/json",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            tabs = json.loads(r.read())
        for t in tabs:
            u = t.get("url", "")
            if "instagram.com" in u and "/login" not in u.lower():
                return t.get("webSocketDebuggerUrl")
    except Exception:
        pass
    return None


async def _dialog_text(ig) -> str:
    return await ig.evaluate(
        "() => { var d = document.querySelector('[role=dialog]'); return d ? d.innerText.slice(0, 600) : ''; }"
    )


async def _click_next_button(ig) -> bool:
    """
    按「下一步」。在裁切頁和編輯頁都需要。
    實測：CDP Enter key 對 div[role=button] 的下一步有效。
    """
    ws_url = _get_ig_ws_url(_get_active_port())
    if not ws_url:
        print("[IG]   無 WS，JS fallback")
        return False

    async with websockets.connect(ws_url, max_size=20*1024*1024) as ws:
        # Focus 下一步 button
        await ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {
                "expression": """
                (function() {
                    var d = document.querySelector('[role=dialog]');
                    if (!d) return 'no_dialog';
                    var all = d.querySelectorAll('*');
                    for (var i = 0; i < all.length; i++) {
                        if ((all[i].textContent||'').trim() === '下一步') {
                            all[i].focus();
                            return 'focused';
                        }
                    }
                    return 'not_found';
                })()
                """,
                "returnByValue": True
            }
        }))
        resp = json.loads(await ws.recv())
        if "focused" not in resp.get("result", {}).get("result", {}).get("value", ""):
            print(f"[IG]   focus failed: {resp}")
            return False

        # CDP Enter
        for ev in ["keyDown", "keyUp"]:
            await ws.send(json.dumps({
                "id": 2, "method": "Input.dispatchKeyEvent",
                "params": {
                    "type": ev, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13
                }
            }))
            json.loads(await ws.recv())

        print("[IG]   CDP Enter sent")
        return True


def _type_caption(ig, text: str) -> str:
    return ig.evaluate(
        """([txt]) => {
            var d = document.querySelector('[role=dialog]');
            if (!d) return 'no_dialog';
            var ces = d.querySelectorAll('[contenteditable=true]');
            for (var i = 0; i < ces.length; i++) {
                var aria = ces[i].getAttribute('aria-label') || '';
                var style = window.getComputedStyle(ces[i]);
                if (style.display !== 'none' &&
                    (aria.indexOf('撰寫') >= 0 || aria.indexOf('說明文字') >= 0)) {
                    ces[i].focus();
                    ces[i].innerText = txt;
                    ces[i].dispatchEvent(new Event('input', {bubbles:true}));
                    return 'caption:' + aria;
                }
            }
            for (var i = 0; i < ces.length; i++) {
                var style = window.getComputedStyle(ces[i]);
                if (style.display !== 'none') {
                    ces[i].focus();
                    ces[i].innerText = txt;
                    ces[i].dispatchEvent(new Event('input', {bubbles:true}));
                    return 'fallback';
                }
            }
            return 'no_editor';
        }""",
        [text]
    )


def _click_share(ig) -> str:
    return ig.evaluate(
        """() => {
            var d = document.querySelector('[role=dialog]');
            if (!d) return 'no_dialog';
            var btns = d.querySelectorAll('button, div[role=button]');
            for (var i = 0; i < btns.length; i++) {
                if ((btns[i].textContent||'').trim() === '分享') {
                    btns[i].click(); return 'clicked:分享';
                }
            }
            return 'not_found';
        }"""
    )


def _click_done(ig) -> str:
    """按「完成」關閉 dialog。"""
    return ig.evaluate(
        """() => {
            var d = document.querySelector('[role=dialog]');
            if (!d) return 'no_dialog';
            var btns = d.querySelectorAll('button, div[role=button], [aria-label]');
            for (var i = 0; i < btns.length; i++) {
                if ((btns[i].textContent||'').trim() === '完成') {
                    btns[i].click(); return 'clicked:完成';
                }
                if (btns[i].getAttribute('aria-label') === '關閉' ||
                    btns[i].getAttribute('aria-label') === 'Close') {
                    btns[i].click(); return 'clicked:關閉';
                }
            }
            return 'not_found';
        }"""
    )


async def post_ig(caption: str, image_path: str) -> str:
    t0 = time.time()
    port = _get_active_port()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                f"http://localhost:{port}", timeout=20000
            )
            ctx = browser.contexts[0]

            # 找 IG 頁面
            ig = None
            for pg in ctx.pages:
                u = pg.url
                if ("instagram.com" in u.lower()
                        and "/login" not in u.lower()
                        and "webworker" not in u.lower()):
                    ig = pg
                    break

            if not ig:
                ig = await ctx.new_page()
                await ig.goto(IG_URL, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

            await ig.bring_to_front()
            await asyncio.sleep(2)

            # ── Step 1: 點擊「新貼文」 ────────────────────────────
            print("[IG] Step 1: 點擊「新貼文」")
            await ig.evaluate(
                "() => { var s = document.querySelectorAll('svg[aria-label=\"新貼文\"]'); "
                "if (s.length > 0 && s[0].parentElement) s[0].parentElement.click(); }"
            )
            await asyncio.sleep(3)

            # ── Step 2: 上傳圖片 ────────────────────────────────
            print(f"[IG] Step 2: 上傳 {image_path}")
            file_input = None
            for _ in range(20):
                file_input = await ig.query_selector("[role='dialog'] input[type='file']")
                if file_input:
                    break
                await asyncio.sleep(0.3)
            else:
                return "❌ 找不到 file input"

            await file_input.set_input_files(image_path, timeout=20000)
            print("[IG]   圖片已設定（請用戶點「打開」）")
            # 等待用戶操作：我們無法自動觸發 macOS 的檔案選擇對話框
            # 等待 dialog 變化（裁切頁出現）
            for _ in range(60):
                await asyncio.sleep(1)
                dt = await _dialog_text(ig)
                if "裁切" in dt or "下一步" in dt:
                    print(f"[IG]   到達裁切頁")
                    break
                print(f"[IG]   等待裁切頁... ({dt[:50]})")
            else:
                return "❌ 等待裁切頁超時"

            # ── Step 3: 裁切頁 → 按下一步 ────────────────────────
            print("[IG] Step 3: 裁切頁 → 按下一步")
            ok = await _click_next_button(ig)
            if not ok:
                return "❌ 裁切頁下一步失敗"
            await asyncio.sleep(2)

            # ── Step 4: 編輯/濾鏡頁 → 按下一步 ─────────────────
            print("[IG] Step 4: 編輯/濾鏡頁 → 按下一步")
            for _ in range(10):
                dt = await _dialog_text(ig)
                if "編輯" in dt or "濾鏡" in dt or "Aden" in dt:
                    print(f"[IG]   到達編輯/濾鏡頁")
                    break
                await asyncio.sleep(0.5)

            ok = await _click_next_button(ig)
            if not ok:
                return "❌ 編輯頁下一步失敗"
            await asyncio.sleep(2)

            # ── Step 5: Caption 頁 → 輸入 caption ──────────────
            print("[IG] Step 5: 輸入 caption")
            for _ in range(15):
                dt = await _dialog_text(ig)
                if "說明文字" in dt or "撰寫" in dt or "分享" in dt:
                    print(f"[IG]   到達 caption 頁")
                    break
                await asyncio.sleep(0.5)
            else:
                return "❌ 等待 caption 頁超時"

            for attempt in range(5):
                result = _type_caption(ig, caption)
                print(f"[IG]   輸入 {attempt+1}: {result}")
                if "no_editor" not in result:
                    break
                await asyncio.sleep(1)
            else:
                return "❌ 找不到 caption 輸入框"

            # ── Step 6: 分享 ─────────────────────────────────
            print("[IG] Step 6: 按分享")
            share_result = _click_share(ig)
            print(f"[IG]   {share_result}")
            if "not_found" in share_result:
                return "❌ 找不到分享按鈕"

            # 等待「已分享你的貼文」
            for i in range(30):
                await asyncio.sleep(1)
                dt = await _dialog_text(ig)
                if "已分享" in dt:
                    print(f"[IG]   ✅ 已分享！（{i+1}s）")
                    break
                if "分享中" in dt or "分享" in dt:
                    print(f"[IG]   發布中... {i+1}s")
            else:
                print("[IG]   ⚠️  未檢測到已分享")

            # ── Step 7: 按完成 ────────────────────────────────
            print("[IG] Step 7: 按完成")
            done_result = _click_done(ig)
            print(f"[IG]   {done_result}")
            await asyncio.sleep(2)

            await browser.close()
            return f"✅ Instagram 發文完成（{time.time()-t0:.1f}s）"

    except Exception as e:
        return f"❌ 錯誤: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: python -m social_mcp.post_ig <caption> <image_path>")
        sys.exit(1)
    result = asyncio.run(post_ig(sys.argv[1], sys.argv[2]))
    print(result)
