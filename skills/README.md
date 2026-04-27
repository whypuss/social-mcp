# hermes-skills

Hermes Agent 的瀏覽器自動化技能庫。控制 Chromium (AIpuss-browser) + Playwright CDP 發布 Facebook、Instagram、Threads 圖文帖子。

## 技能列表

### 1. instagram-workflow
**用途**：Instagram 圖文發文（Playwright + CDP 混合模式，v6）
**核心方法**：
- 新貼文 SVG 按鈕：CDP JS click（Playwright click 被覆蓋層 intercept）
- React 按鈕（從電腦選擇、下一步、分享、完成）：Playwright `locator.click()`
- 圖片：`page.on("filechooser")` 事件監聽器（繞過 OS 窗口）
- Caption：keyboard.type()
**腳本**：`social_mcp/post_ig.py`
```bash
uv run python -m social_mcp.post_ig "caption" /path/to/image.jpg
```

---

### 2. facebook-workflow
**用途**：Facebook 圖文發文（Playwright + CDP 混合模式，v6）
**核心方法**：
- Composer：CDP JS click「在想什麼」div
- 圖片：**base64 DataTransfer 注入**（React 不吃 input.files，需轉 Blob → File → DataTransfer）
- Caption：keyboard.type()
- 下一頁/發佈：CDP JS click
- 成功信號：dialog 關閉
**腳本**：`social_mcp/post_facebook.py`
```bash
uv run python -m social_mcp.post_facebook "caption" /path/to/image.jpg
```

---

### 3. threads-composer-debug
**用途**：Threads 發文（Playwright 純 Selector 模式，~12 秒）
**核心方法**：
- 純 Playwright locator，無 CDP flooding（避免 Meta 帳號被封）
- 所有 locator 加 `.last`（5 個重疊 dialog）
- 所有 `.click()` 用 `force=True`（overlay 遮擋）
- 打字：keyboard.type()（40-80ms/字元）
- 圖片：`input[type=file].set_input_files()`（Threads 的 React 接受這個）
**腳本**：`social_mcp/post_threads.py`
```bash
uv run python -m social_mcp.post_threads "message"
```

---

### 4. facebook-mcp-browser-setup
**用途**：Chromium (ungoogled-chromium) 設定，供 Facebook MCP 使用
**核心方法**：
- 使用 ungoogled-chromium（非 Google Chrome），避免 Singleton lock 衝突
- `--remote-debugging-port=9333` + 獨立 profile `/tmp/chromium-fb`
- 只用 `connect_over_cdp()`，禁止 `launch_persistent_context()`
**啟動命令**：
```bash
/Applications/Chromium.app/Contents/MacOS/Chromium \
  --remote-debugging-port=9333 \
  --user-data-dir=/tmp/chromium-fb \
  --no-first-run \
  --no-default-browser-check \
  --window-size=1280,720
```

## 平台差異對照

| | Instagram | Facebook | Threads |
|---|---|---|---|
| 圖片方式 | filechooser 事件 | base64 DataTransfer 注入 | set_input_files |
| 按鈕點擊 | CDP JS（新貼文）/ Playwright（React 按鈕） | CDP JS | Playwright force=True |
| 打字方式 | keyboard.type | keyboard.type | keyboard.type |
| 成功信號 | "已分享" + dialog 消失 | dialog 關閉 | dialog 消失 + reload 驗證 |
| 主要陷阱 | overlay 遮擋、React 不吃 JS click | React 不吃 input.files | 5 個重疊 dialog |

## 瀏覽器前置條件

- Chromium 運行中（port 9333）
- 已登入對應平台（FB/IG/Threads）
- profile 位於 `/tmp/chromium-fb`
