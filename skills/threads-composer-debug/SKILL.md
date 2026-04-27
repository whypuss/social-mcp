---
name: threads-composer-debug
description: Threads CDP Browser Hijacking — Playwright selector mode, no CDP flooding
category: browser-automation
tags: [threads, playwright, selector, no-cdp]
created: 2026-04-24
updated: 2026-04-27

# Threads Composer — Playwright Selector Mode（2026-04-27）

## 重要：5 個重疊 Dialog

Threads 頁面同時存在 **5 個可見的 `[role=dialog]`**，結構完全相同。
**所有 locator 都要加 `.last`**，否則 Playwright strict mode 報錯。

## Stable Selectors

```python
# 打開 composer（新串文入口）
'[role="button"][aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]'

# dialog 內（全部加 .last）
'[role="dialog"] div[role="textbox"]'                              # 文字框
'[role="dialog"] div[role="button"]:has-text("發佈")'               # 發佈
'[role="dialog"] div[role="button"]:has-text("取消")'               # 取消
'[role="dialog"] svg[aria-label="附加影音內容"]'                     # 附加影音
'[role="dialog"] svg[aria-label="新增 GIF"]'                        # GIF
'[role="dialog"] svg[aria-label="新增表情符號"]'                     # 表情
'[role="dialog"] svg[aria-label="新增票選活動"]'                     # 票選
'[role="dialog"] svg[aria-label="新增地點"]'                        # 地點
'[role="dialog"] input[type="file"]'                                # file input
```

## Overlay 遮擋

Threads 頁面常有 overlay（浮層元素）攔截 pointer events。
**所有 `.click()` 都要 `force=True`**，否則 Timeout。

## 打字方式

用 Playwright `keyboard.type()`（擬人速度 40-80ms/字元），不用 CDP。

```python
# 點擊文字框（force=True 繞過 overlay）
await threads_page.locator('div[role="textbox"]').last.click(force=True)
await asyncio.sleep(random.uniform(0.3, 0.6))

# 打字
delay_ms = random.randint(40, 80)
await threads_page.keyboard.type(message, delay=delay_ms)
```

## 完整流程

```python
async def post_threads(message: str, image_path: str = None) -> str:
    # 1. 確保 composer 開啟
    if not await threads_page.locator('[role="dialog"]').last.is_visible(timeout=1000):
        await threads_page.locator(
            '[role="button"][aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]'
        ).click(timeout=5000, force=True)
        await asyncio.sleep(random.uniform(1.0, 1.5))
        await threads_page.locator('[role="dialog"]').last.wait_for(state='visible')

    # 2. 點擊文字框
    await threads_page.locator('[role="dialog"] div[role="textbox"]').last.click(force=True)
    await asyncio.sleep(random.uniform(0.3, 0.6))

    # 3. 打字
    await threads_page.keyboard.type(message, delay=random.randint(40, 80))

    # 4. 點發佈
    pub_btn = threads_page.locator('[role="dialog"] div[role="button"]:has-text("發佈")').last
    await pub_btn.scroll_into_view_if_needed()
    await asyncio.sleep(random.uniform(0.3, 0.5))
    await pub_btn.click(timeout=5000, force=True)

    # 5. 驗證
    await asyncio.sleep(1.0)
    await threads_page.reload(wait_until='domcontentloaded')
```

## 禁止事項

- ❌ 記錄座標（x=942, y=466）——Threads DOM 動態變化，座標不可靠
- ❌ CDP Input.dispatchKeyEvent 轟炸——速率異常會導致 Meta 帳號被封
- ❌ `div[role=button]:has-text("有什麼新鮮事？")` —— 文字在 span 裡，不是按鈕文字
- ❌ 用 `>>` 直接子元素 combinator —— textbox/button 在 dialog 內多層嵌套，應用空格

## Browser Port

Threads 在 Chromium，port 9333（不同於 Facebook 的 Chrome 9222）。

```python
from social_mcp.browser_hijack import get_active_cdp_port
browser = await p.chromium.connect_over_cdp(f"http://localhost:{get_active_cdp_port()}")
```
