---
name: facebook-mcp-browser-setup
description: 使用 Chromium (ungoogled-chromium) + Playwright CDP 為 Facebook MCP 設置專用瀏覽器 session，解決 Chrome 衝突問題
triggers:
  - facebook mcp playwright
  - chromium playwright cdp facebook
  - just_facebook_mcp
category: browser-automation
---

## 背景教訓

Chrome / Chrome for Testing 在 macOS 上共享同一個代碼簽名，無法同時運行同一個 profile。Chrome 的 Singleton lock 機制會讓第二個實例直接退出。如果已有一個 Chrome 在跑任何 profile，另一個實例（無論是否 headless）都會立即崩潰。

**正確方案：使用 ungoogled-chromium（開源 Chromium），完全與 Google Chrome 隔離。**

## 完整流程

### 步驟 1：確認已安裝 Chromium

```bash
brew install --cask ungoogled-chromium
# 或
ls /Applications/Chromium.app
```

### 步驟 2：啟動隔離的 Chromium session

```bash
# 停止所有 Chrome/Chromium，避免端口衝突
pkill -f "Chrome" 2>/dev/null
pkill -f "Chromium" 2>/dev/null
sleep 2

# 清理任何殘留 lock
rm -f ~/Library/Application\ Support/Google/Chrome/Singleton* 2>/dev/null

# 建立乾淨的隔離 profile 目錄
rm -rf /tmp/chromium-fb
mkdir -p /tmp/chromium-fb

# 啟動 Chromium（NOT headless，这样用户可以操作窗口）
/Applications/Chromium.app/Contents/MacOS/Chromium \
  --remote-debugging-port=0 \
  --user-data-dir=/tmp/chromium-fb \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1280,720 \
  >> /tmp/chromium-fb.log 2>&1 &
echo "PID: $!"
sleep 8
```

### 步驟 3：取得 CDP port

```bash
PORT=$(lsof -i -P | grep "Chromium" | grep LISTEN | awk -F: '{print $2}' | head -1)
WS=$(cat /tmp/chromium-fb.log | grep "DevTools listening" | awk '{print $3}' | tail -1)
echo "CDP port: $PORT"
echo "WebSocket: $WS"
```

### 步驟 4：用戶登入

用戶手動打開 `/Applications/Chromium.app`，在窗口中打開 facebook.com，登入帳號。完成後告知 agent。

### 步驟 5：Playwright CDP 接管

```python
import asyncio
from playwright.async_api import async_playwright

async def接管(session_url: str):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(session_url)
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto('https://www.facebook.com', wait_until='networkidle', timeout=30000)
        await asyncio.sleep(5)
        # 驗證登入
        cookies = await ctx.cookies(['https://www.facebook.com'])
        has_c_user = any(c['name'] == 'c_user' for c in cookies)
        print(f"登入狀態: {'✅' if has_c_user else '❌'}")
        return has_c_user
```

### 步驟 6：重啟時無需重新登入

下次啟動 Chromium 時，指定同樣的 `--user-data-dir=/tmp/chromium-fb`，cookies 會保留。除非手動登出或 cookies 過期。

## 常見問題

| 問題 | 原因 | 解決 |
|------|------|------|
| Chrome 打不開 | Singleton lock 殘留 | `rm ~/Library/Application\ Support/Google/Chrome/Singleton*` |
| CDP 連不上 | Chromium 未啟動或端口被佔 | 檢查 `lsof -i -P \| grep Chromium` |
| c_user cookie 找不到 | session 未真正建立 | 確認用戶看到的是動態時報而非登入表單 |
| 登入後視窗被關閉 | 可能有 two-instance 衝突 | 確保只有一個 Chromium 實例在跑 |
| `TargetClosedError` + `kill ESRCH` | Playwright 啟動了自己的 Chromium (Google Chrome for Testing)，與 ungoogled-chromium profile 不相容 | 不要用 `launch_persistent_context`；改用 `connect_over_cdp()` 連接已運行的 ungoogled-chromium |
| `launch_persistent_context` 進程立即退出 | Playwright 的 Chromium 版本與 profile 的加密鑰匙不相容 | 只能用 `connect_over_cdp()` 方案，詳見 skill `chromium-cdp-browser-hijack` |

## 關鍵陷阱：千萬不要用 launch_persistent_context

`playwright.chromium.launch_persistent_context(user_data_dir=...)` 會啟動 Playwright 自帶的 Chromium (Google Chrome for Testing)，它使用自己的加密鑰匙，**無法解密** ungoogled-chromium profile 裡的 cookies。Chromium 會直接崩潰。

**唯一正確做法：** 啟動 ungoogled-chromium 用 `--remote-debugging-port` 模式，然後 `connect_over_cdp()` 接管。

## Threads 發文：Lexical Editor 的 contenteditable 陷阱

操作 Threads 發文時，composer modal 打開後會有多個相同的
`div[contenteditable][aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]` 元素：

```
[0] h=105, top=214 → 舊文章內容（錯誤）
[1] h=21, top=256  → 空的 editor（正確）
[2-4] h=21, top=256 → 空 editor 副本
```

**錯誤做法：** 直接用 `querySelectorAll("[contenteditable]")[0]` → 會選到錯誤元素（包含整個 feed 文字）。

**正確流程：**
1. 用 `Playwright.click('[aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]').first.click(force=True)` 先點擊（Playwright 會自動選中正確的那個）
2. 然後 CDP 的 `document.activeElement` 才能取到正確的 editor
3. 發布按鈕用 `Playwright.mouse.click(x, y)` 比 CDP `.click()` 更可靠

```python
# Step 1: Playwright 點擊開啟 composer 並 focus 正確的 editor
await threads_page.locator(
    '[aria-label="文字欄位空白。請輸入內容以撰寫新貼文。"]'
).first.click(force=True)
await asyncio.sleep(0.3)

# Step 2: CDP 打字 — 用 document.activeElement（此時已指向正確的 editor）
async with websockets.connect(tab_ws_url) as ws:
    await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
        "params": {"expression":
            "document.activeElement?.setAttribute('data-cdp-focused', 'true')",
            "returnByValue": True}}))
    await _type_via_cdp(ws, message)

# Step 3: 發布按鈕坐標取得後，用 Playwright mouse.click
await threads_page.mouse.click(btn_info["x"], btn_info["y"])
```

## Facebook 發文完整流程（CDP 自動化）

Facebook 的帖子 Composer 是**兩頁對話框**：
- **第一頁（建立帖子）**：文字輸入、相片/影片按鈕、「新增到帖子」、下一頁
- **第二頁（帖子設定）**：排定選項/立即發佈、分享到群組、儲存、發佈

### 關鍵發現

1. **所有按鈕全是 `DIV` + `role=button`**，靠 `aria-label` 區分功能
2. **aria-label JS click 風險**：在 dialog 內用 `.click()` 可能觸發關閉而非執行。用座標點擊更可靠
3. **打字用 `execCommand`**：`document.execCommand("insertText", false, text)` 比 CDP `Input.dispatchKeyEvent` 靠譜
4. **文件上傳**：OS 層級 file chooser，CDP 無法自動化，需用戶手動選圖
5. **座標參考**：Chromium 視窗約 1280px 寬時，dialog 在 x=470 起

### 兩頁 Dialog 常見按鈕（絕對座標）

| 按鈕 | aria-label | tag | 位置 (視窗1280px) |
|------|-----------|-----|------------------|
| 關閉 | 關閉撰寫工具對話框 | DIV | (418, 96) |
| 返回 | 返回 | DIV | (486, 96) |
| 編輯私隱 | 編輯私隱設定。分享對象：所有人。 | DIV | (537, 274) |
| 相片/影片 | 相片／影片 | DIV | (707, 488) |
| 表情符號 | 表情符號 | DIV | (925, 425) |
| 下一頁 | 下一頁 | DIV | (486, 551) |
| **排程/發佈** | — | DIV | (478, 377) |
| **發佈** | 發佈 | DIV | (726, 646) |

### 自動化流程（Python + CDP WebSocket）

```python
import asyncio, json, websockets

WS = 'ws://localhost:9333/devtools/page/<TAB_ID>'

async def cdp(ws, mid, method, params):
    await ws.send(json.dumps({'id': mid, 'method': method, 'params': params}))
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=15))

async def click_aria(ws, aria_label):
    """可靠點擊：aria-label 定位"""
    await cdp(ws, 1, 'Runtime.evaluate', {
        'expression': f'document.querySelector(\'[aria-label="{aria_label}"]\').click()',
        'returnByValue': True
    })

async def click_coord(ws, x, y):
    """座標點擊：用於 dialog 內按鈕（aria click 有副作用時）"""
    await cdp(ws, 10, 'Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})
    await asyncio.sleep(0.3)
    await cdp(ws, 11, 'Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})
    await asyncio.sleep(0.05)
    await cdp(ws, 12, 'Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})

async def type_text(ws, text):
    """可靠打字：用 execCommand insertText"""
    await cdp(ws, 1, 'Runtime.evaluate', {
        'expression': '''(() => {
            var e = document.querySelector("[role=dialog]").querySelector("[contenteditable]");
            if (e) { e.focus(); document.execCommand("insertText", false, arguments[0]); return "ok"; }
            return "not_found";
        })()''',
        'params': {'expression': '', 'args': [text], 'returnByValue': True}
    })
    # 上面不行就用這個
    await cdp(ws, 2, 'Runtime.evaluate', {
        'expression': f'''(function(){{
            var e = document.querySelector("[role=dialog]").querySelector("[contenteditable]");
            if (!e) return "no_editor";
            e.focus();
            document.execCommand("insertText", false, "{text}");
            return "done";
        }})()''',
        'returnByValue': True
    })

async def fb_post(ws, message):
    # Step 1: Facebook 選單
    await click_aria(ws, 'Facebook 選單')
    await asyncio.sleep(2)
    
    # Step 2: 帖子（座標點擊，aria 有重疊元素問題）
    await click_coord(ws, 1272, 168)
    await asyncio.sleep(2)
    
    # Step 3: 打字
    await type_text(ws, message)
    await asyncio.sleep(1)
    
    # Step 4: 下一頁
    await click_aria(ws, '下一頁')
    await asyncio.sleep(3)
    
    # Step 5: 發佈
    await click_aria(ws, '發佈')
    await asyncio.sleep(5)
    
    # 驗證
    r = await cdp(ws, 99, 'Runtime.evaluate', {
        'expression': 'document.body.innerText.includes("動態消息帖子")',
        'returnByValue': True
    })
    return r.get('result', {}).get('result', {}).get('value', False)
```

### CDP 操作安全規則

- **嚴禁**：1秒內發送超過 2 個 CDP 指令
- **嚴禁**：批量轟炸 CDP（會導致 Chrome 分頁崩潰或開啟垃圾頁面）
- **間隔**：每個操作之間至少等待 0.3-0.5 秒
- **優先**：aria-label 定位 → 失敗則用座標
- **打字**：始終用 `execCommand insertText`，不用 key event

### 驗證邏輯注意

發文後的 body innerText 會包含整個 feed 的文字，不能用 `message[:30] in body` 來判斷成功與否。改用 CDP 檢查 `document.querySelector('[data-lexical-editor]')?.innerText`。

## 與 just_facebook_mcp 的整合

just_facebook_mcp 需要 browser session。流程：
1. Chromium 保持運行（帶有效 FB session）
2. MCP server 連接 CDP port
3. MCP 通過 CDP 操縱已登入的 browser 狀態

**重要提醒：** just_facebook_mcp 的 `post_threads` 底層機制就是 CDP + Playwright，和直接用 social-mcp 的 `post_threads` 沒有本質區別。它只能發 Threads（不是 Facebook），也沒有 Graph API 集成。

## 為什麼不用 Chrome for Testing

Chrome for Testing 與普通 Google Chrome 共享同一個代碼簽名 (com.google.Chrome)，macOS Keychain 的 Chrome Safe Storage Keychain group 對兩者都有效，但 profile 目錄級別的 Singleton lock 會衝突。一個進程持有 lock，另一個就無法啟動。
