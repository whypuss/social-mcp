# social-mcp

> 用 Chrome Cookies 直接操作 Meta 全家桶：Facebook、Instagram、Threads。
> 完全本地運行，不需要第三方 API Token，不需要 OAuth 跳轉。

## 功能

| 工具 | 說明 |
|------|------|
| `health_check` | 檢查 Chrome cookies 是否有效 |
| `get_messenger_inbox` | 讀取 Messenger 收件箱最新對話 |
| `send_messenger` | 發送 Messenger 私信 |
| `post_facebook` | 發 Facebook 粉絲團文字帖 |
| `post_instagram` | 發 Instagram 圖文帖（需 IG Business） |
| `post_threads` | 發 Threads 文字帖（需 IG Business） |
| `get_facebook_page_info` | 取得粉絲頁資訊（名稱、粉絲數） |

## 原理

```
你的 Chrome（已登入 Facebook）
    ↓ CDP (Chrome DevTools Protocol)
social-mcp 讀取 cookies
    ↓
直接 call Facebook Graph API（用 cookie session）
    ↓
完成發帖 / 讀取收件箱 / 發 IG / 發 Threads
```

**核心創新**：不需要 Facebook Developer App，不需要 `PAGE_ACCESS_TOKEN`，直接用你 Chrome 裡的真實登入狀態。

## 前置需求

- Python 3.11+
- Chrome 或 Chromium（已登入 Facebook）
- macOS / Linux
- **建議**：已啟用 AIpuss-browser daemon（social-mcp 會自動偵測並復用 CDP 連線）

## 安裝

```bash
# clone 並進入目錄
git clone https://github.com/whypuss/social-mcp.git
cd social-mcp

# 用 uv 安裝（推薦）
uv sync

# 或用 pip
pip install -e .
```

## 設定

### 1. 允許 Chrome 使用舊版 Cookie（必須）

Facebook 預設封鎖「使用舊版 Cookie 的應用」，需要手動開啟：

1. 開啟 Facebook網頁，點右上角頭像 → `設定與隱私` → `設定`
2. 左側選單 → `安全與登入`
3. 向下滾，找到「**允許使用舊版 Cookie 的應用**」
4. 切換為「**開啟**」

> 若沒有這個選項，表示你的帳號已預設允許，繼續下一步。

### 2. 設定環境變數（可選）

```bash
# .env 檔（放在 social-mcp/ 目錄下）
cp .env.example .env
nano .env
```

```env
# Facebook 粉絲頁 ID（可從粉絲頁網址取得）
# 例如：https://www.facebook.com/MyPage → FACEBOOK_PAGE_ID=MyPage
FACEBOOK_PAGE_ID=你的頁面ID

# Instagram Business 帳號 ID（從 Meta Business Suite 取得）
IG_USER_ID=你的IG帳號數字ID
```

### 3. 確認 Chrome 已登入

確保你 Chrome 的 Profile 3（名稱 `MY`）已登入 Facebook 和 Instagram。
若使用 AIpuss-browser daemon，它預設會讀取這個 profile。

## 使用方式

### 啟動 MCP Server

```bash
# 基本啟動
uv run social-mcp

# 除錯模式（看完整日誌）
uv run social-mcp --debug
```

### 連接到 Claude Desktop

在 `~/Library/Application Support/Claude/claude_desktop_config.json` 加入：

```json
{
  "mcpServers": {
    "social": {
      "command": "uv",
      "args": ["run", "--project", "/路徑/到/social-mcp", "social-mcp"]
    }
  }
}
```

重啟 Claude Desktop，即可在對話中使用上述所有工具。

### 連接到 Hermes Agent

在 `~/.hermes/config.yaml` 的 `mcp` 區塊加入，或直接用：

```bash
hermes tools enable social
```

## 工具使用範例

```
你：幫我發一篇 Facebook 帖：「測試成功！」
Agent → post_facebook(message="測試成功！")
       → ✅ 成功！URL: https://www.facebook.com/123456789

你：讀取最新的 Messenger 訊息
Agent → get_messenger_inbox(limit=5)
       → ## Messenger 收件箱（5筆）
         1. [2026-04-24 10:00] 王小明：明天開會嗎？
         2. [2026-04-24 09:45] 李小美：收到了，謝謝！

你：回覆王大明（ID: 100001234567890）說「好的，明天見」
Agent → send_messenger(thread_id="100001234567890", message="好的，明天見")
       → ✅ 訊息已發送
```

## 關於 AIpuss-browser 整合

social-mcp 會自動偵測 AIpuss-browser daemon：

1. 讀取 `~/.agent-browser/default.stream` 取得 CDP WebSocket URL
2. 透過 WebSocket 連接 AIpuss 的 Chrome session
3. 直接從 CDP 讀取 cookies（繞過 document.cookie 在 headless 模式下的限制）

**好處**：
- AIpuss 的 Chrome 已經包含你的登入狀態，不需要另外開瀏覽器
- 完全在本地運行，cookies 不會經過任何第三方伺服器
- 若 AIpuss daemon 未運行，會自動 fallback 啟動獨立的 Chrome

## 安全性說明

- **Cookies 永遠留在本地**：`social-mcp` 只在你自己機器上運行，cookies 不會傳送到任何第三方
- **不會儲存任何敏感資料**：cookies 只存在記憶體中，程式結束後消失
- **建議**：使用專用的 Chrome Profile 而非主要 profile

## 限制與已知問題

| 問題 | 說明 |
|------|------|
| Facebook 偵測 headless | 某些 Facebook 頁面在 headless 模式無法完整載入，cookies 仍可正常讀取 |
| 需要 IG Business 帳號 | IG / Threads 發文功能需要將個人帳號升級為 Business 帳號 |
| Token 有效期 | Chrome cookie session 可能過期，長時間使用後需要重新在瀏覽器登入 |
| macOS 金鑰串流存取 | Chrome cookies 默認加密，若無法自動解密，請確認你已登入 macOS 帳戶 |

## 開發

```bash
# 安裝開發依賴
uv sync --extra dev

# 程式碼檢查
uv run ruff check social_mcp/

# 單元測試
uv run pytest
```

## 授權

MIT License

---

**注意**：本工具僅供個人自動化用途。大量或自動化操作 Facebook/IG 可能違反 Meta 服務條款，請自行評估風險。
