---
name: instagram-workflow
description: "Instagram 圖文發文（Playwright + CDP 混合模式，v6，2026-04-27）。filechooser 事件監聽器繞過 OS 窗口上傳圖片。"
category: social-media
tags: [instagram, playwright, cdp, browser-automation]
---

# Instagram Workflow — Playwright + CDP 混合模式（v6）

## 概述

Chromium (AIpuss-browser) + Playwright CDP 控制 Instagram 網頁版發布圖文帖子。

**核心策略（v4 修復，2026-04-27）：**
- **新貼文 SVG 按鈕**：CDP JS click（Playwright click 被覆蓋層 intercept）
- **React 按鈕**（從電腦選擇、下一步、分享、完成）：Playwright `locator.click()`（CDP JS click 無法觸發 React onClick）
- **Caption**：keyboard.type()（React textbox 不接受 innerText）
- **圖片**：`page.on("filechooser")` 事件監聽器（完全繞過 OS 窗口）
- **現有 dialog**：啟動時檢測是否有殘留 dialog，有則跳過前面步驟

## 前置條件

- Chromium 運行中（port 9333）
- 已登入 Instagram
- `~/.kimaki/projects/ai-cdp-browser/` 安裝了 Playwright
- 圖片 > 1KB

## 按鈕點擊方式

### React 按鈕（從電腦選擇、下一步、分享、完成）→ Playwright locator click
```python
btn = ig.locator('button:has-text("下一步")').first
await btn.click(timeout=5000)
```

### 圖片上傳 → `page.on("filechooser")` 事件攔截（不彈 OS 窗口）
**關鍵：不要** 用 `set_input_files()` 直接注入（會觸發 macOS 原生文件選擇器，無法關閉）。
**正確方式：** 在點擊按鈕前先監聽 `filechooser` 事件，用 `file_chooser.set_files()` 繞過 OS 窗口。

```python
file_chooser = None

def on_file_chooser(fc):
    global file_chooser
    file_chooser = fc

ig.on("filechooser", on_file_chooser)
btn = ig.locator('button:has-text("從電腦選擇")').first
await btn.click(timeout=5000)

# 等 filechooser 事件觸發（最多 10 秒）
for _ in range(50):
    if file_chooser is not None:
        break
    await asyncio.sleep(0.2)
else:
    return "❌ File chooser 未出現"

# 直接設文件，繞過 OS 選擇器
await file_chooser.set_files(image_path, timeout=20_000)
ig.remove_listener("filechooser", on_file_chooser)
await asyncio.sleep(3)  # 等 IG 處理圖片
```

### SVG 導航按鈕（新貼文）→ CDP JS click
```python
await ig.evaluate('() => { var s = document.querySelectorAll(\'svg[aria-label="新貼文"]\'); if(s[0] && s[0].parentElement) s[0].parentElement.click(); }')
```

### 其他按鈕（下一步、分享、完成）→ CDP JS click + fallback 搜索 `button` 標籤
```python
async def _click_btn_by_text(page, text: str, timeout: float = 10):
    script = f"""
    () => {{
        var btns = document.querySelectorAll('[role="button"], button');
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t === '{text}') {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        // fallback: 包含 text
        for (var i = 0; i < btns.length; i++) {{
            var t = (btns[i].innerText || '').trim();
            if (t.indexOf('{text}') >= 0) {{ btns[i].click(); return 'clicked:' + t; }}
        }}
        return 'not_found';
    }}
    """
    for _ in range(int(timeout * 5)):
        try:
            r = await page.evaluate(script)
            if r != "not_found": return r
        except Exception: pass
        await asyncio.sleep(0.2)
    return "not_found"
```

## 完整流程

1. 連接 CDP，找到 IG 頁面，bring_to_front
2. **Step 0: 檢測現有 dialog**
   - `ig.evaluate()` 查 `document.querySelector('[role="dialog"]').innerText`
   - 有 dialog → 根據內容判斷狀態（裁切/caption），直接跳到對應步驟
   - 無 dialog → 繼續 Step 1
3. **Step 1: 點新貼文**（CDP JS click SVG）
4. 等初始 dialog（"建立新貼文"/"從電腦選擇"）
5. **Step 2: Playwright click「從電腦選擇」** + `page.on("filechooser")` 事件監聽器攔截
6. 等 file chooser 事件觸發 → `file_chooser.set_files()` 直接設文件（繞過 OS 窗口）
7. **Step 3: 等裁切/編輯 dialog → Playwright click 下一步**（可能按 2-3 次直到 caption）
8. **Step 4: caption dialog → keyboard.type 輸入說明**
9. **Step 5: Playwright click 分享 → 等"已分享" → click 完成**

## 已知流程分支

- 單圖：上傳 → 裁切 → 下一步 → caption
- 單圖（可編輯）：上傳 → 裁切 → 下一步 → 編輯/篩選 → 下一步 → caption
- **關鍵**：裁切頁、編輯頁都只有一個「下一步」按鈕，可能要按 2-3 次

## 陷阱

### 1. CDP JS click 無法觸發 React onClick（最關鍵！）
Instagram React 按鈕（"從電腦選擇"等）需要 Playwright `.click()`，CDP JS `element.click()` 無效。

### 2. 從電腦選擇是 `<BUTTON role=null>`
不能用 `querySelectorAll('[role="button"]')`，要同時包含 `button` 標籤。

### 3. 新貼文 SVG 被覆蓋層擋住
`<div class="x1qjc9v5 ...">` 覆蓋在 SVG 上，Playwright `.click()` 會被 intercept timeout。要用 CDP JS click 穿透。

### 4. 永遠不要用 `set_input_files()` 直接注入（在 CDP 模式）
在 CDP 模式 + macOS 上，`set_input_files()` 會觸發原生文件選擇器窗口。`keyboard.press("Escape")` 無法關閉 macOS 原生窗口。**正確做法：用 `page.on("filechooser")` 事件監聽器在按鈕點擊前攔截對話框。**

### 5. File chooser 事件監聽需在按鈕點擊前設定
`ig.on("filechooser", callback)` 必須在 `btn.click()` 之前執行，否則事件已觸發但沒有監聽器，OS 窗口會彈出。用 `finally` 塊確保 `remove_listener` 清理。

### 6. 殘留 dialog 狀態
上一次運行可能 Chromium 停留在裁切/caption dialog。啟動時**必須檢測**，否則會重複點新貼文導致藍屏。

### 7. 圖片 > 1KB
IG 拒絕 < 1KB 檔案。

### 8. Caption 需 keyboard.type
React 狀態不接受 innerText。**用 keyboard.type() 逐字輸入**，delay=40-80ms。

### 9. TargetClosedError
IG navigation 期間 CDP evaluate 會拋 TargetClosedError。**所有 evaluate 呼叫包 try/except**。

## 腳本位置

`~/.kimaki/projects/ai-cdp-browser/social_mcp/post_ig.py`

用法：
```bash
cd ~/.kimaki/projects/ai-cdp-browser
uv run python -m social_mcp.post_ig "caption text" /path/to/image.jpg
```
