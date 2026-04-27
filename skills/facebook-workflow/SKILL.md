---
name: facebook-workflow
description: "Facebook 圖文發文（Playwright + CDP 混合模式，v6，2026-04-27）。圖片用 base64 DataTransfer 注入（繞過 React input.files 限制）。"
category: social-media
tags: [facebook, playwright, cdp, browser-automation]
---

# Facebook Workflow — Playwright + CDP 混合模式（v6）

## 概述

Chromium (AIpuss-browser) + Playwright CDP 控制 Facebook 網頁版發布圖文帖子。

**核心策略（v6，2026-04-27）：**
- **Composer**：CDP JS click「在想什麼」div
- **圖片**：base64 DataTransfer 注入（React 不吃 input.files）
- **Caption**：keyboard.type()
- **下一頁/發佈**：CDP JS click
- **成功信號**：dialog 關閉

## 前置條件

- Chromium 運行中（port 9333）
- 已登入 Facebook
- `~/.kimaki/projects/ai-cdp-browser/` 安裝了 Playwright

## 圖片上傳：base64 DataTransfer 注入

FB 的 React 組件攔截 `input.files` 替換，filechooser 事件雖然能觸發但 React 狀態不更新。
**解決方法：** 用 CDP JS 把圖片轉成 base64 → Blob → File → DataTransfer，直接注入 input.files 並 dispatch change 事件。

```python
import base64 as _b64
with open(image_path, "rb") as f:
    b64_data = _b64.b64encode(f.read()).decode()

inject_r = await fb.evaluate("""(b64) => {
    const binaryString = atob(b64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
        bytes[i] = binaryString.charCodeAt(i);
    }
    const blob = new Blob([bytes], { type: 'image/jpeg' });
    const file = new File([blob], 'upload.jpg', { type: 'image/jpeg', lastModified: Date.now() });
    const inputs = document.querySelectorAll('input[type=file]');
    for (let i = 0; i < inputs.length; i++) {
        const inp = inputs[i];
        const dt = new DataTransfer();
        dt.items.add(file);
        Object.defineProperty(inp, 'files', {
            value: dt.files,
            writable: true,
            configurable: true
        });
        const tracker = inp._valueTracker;
        if (tracker) { tracker.setValue(''); }
        inp.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
    }
    return { ok: true, inputsUpdated: inputs.length };
}""", b64_data)
```

**驗證：等 blob URL 出現**
```python
for _ in range(10):
    await asyncio.sleep(1)
    preview = await fb.evaluate("""() => {
        var d = document.querySelector('[role=dialog]');
        if (!d) return null;
        var imgs = d.querySelectorAll('img[src]');
        for (var img of imgs) {
            if (img.src.startsWith('blob:')) return img.src.slice(0, 80);
        }
        return null;
    }""")
    if preview: break
```

## 按鈕點擊方式

### CDP JS click（composer、下一頁、發佈）
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

1. 連接 CDP，找到 FB 頁面，bring_to_front
2. **Step 0: 檢測並關閉殘留 dialog** → 嘗試點擊關閉按鈕
3. **Step 1: 點擊 composer**（CDP JS click「在想什麼」div）
4. **Step 2: 圖片上傳** → base64 DataTransfer 注入（見上）
5. **Step 3: 等 blob URL 預覽出現**（驗證上傳成功）
6. **Step 4: keyboard.type 輸入 caption**
7. **Step 5: CDP JS click 下一頁**
8. **Step 6: CDP JS click 發佈 → 等 dialog 關閉（成功信號）**

## 陷阱

### 1. Facebook React 不吃 input.files（最關鍵！）
FB 的 React input[type=file] 組件用 `Object.defineProperty` 替換了 `files` getter，攔截外部賦值。filechooser 事件能觸發但 React state 不更新。
**解決：base64 → Blob → File → DataTransfer → dispatchEvent（見上）。**

### 2. 永遠不要用 `set_input_files()` 直接注入（在 CDP 模式）
在 CDP 模式 + macOS 上，`set_input_files()` 會觸發原生文件選擇器窗口。`keyboard.press("Escape")` 無法關閉 macOS 原生窗口。

### 3. Caption 需 keyboard.type
React 狀態不接受 innerText。**用 keyboard.type() 逐字輸入**，delay=40-80ms。

### 4. 殘留 dialog 狀態
上一次運行可能 Chromium 停留在 dialog。啟動時**必須檢測並關閉**。

### 5. 發佈成功信號
Dialog 關閉 = 發佈成功。等待 20 秒超時。

## 腳本位置

`~/.kimaki/projects/ai-cdp-browser/social_mcp/post_facebook.py`

用法：
```bash
cd ~/.kimaki/projects/ai-cdp-browser
uv run python -m social_mcp.post_facebook "caption text" /path/to/image.jpg
```
