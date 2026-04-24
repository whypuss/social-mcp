# Meta Workflow

> 用 CDP Browser Hijacking 直接操作 Facebook 個人帳號——不需要 API Token、不需要開發者帳號。

用你自己的 Chromium 瀏覽器 session，AI Agent 透過 CDP 接管來發文、讀私訊、讀通知。

## 2026-04-24 實測：成功在 Facebook 個人牆發文

![post result](https://img.shields.io/badge/Status-Working-brightgreen)
![platform](https://img.shields.io/badge/Platform-Facebook%20Personal%20Account-blue)

## 功能

| 工具 | 說明 | 狀態 |
|------|------|------|
| `open_login_window` | 啟動可見瀏覽器，讓你親自登入 Facebook（只需一次） | ✅ |
| `post_facebook` | 在個人動態牆發布文字帖 | ✅ 實測成功 |
| `read_messenger` | 讀取 Messenger 私訊對話列表 | ✅ |
| `read_notifications` | 讀取 Facebook 通知 | ✅ |
| `post_threads` | 在 Threads 個人檔案發布文字+圖片帖 | ✅ 實測成功 |

## 原理

```
你親自登入一次（Chromium - FacebookMCP profile）
         ↓
Hermes Agent 啟動 Chromium --remote-debugging-port=9333
         ↓
CDP WebSocket 接管 browser
         ↓
Playwright 直接操作 DOM
         ↓
完成發文 / 讀取私訊 / 讀取通知
```

**為什麼不走 Graph API？**
- Graph API 只能操作粉絲專頁，不能操作個人帳號私訊/通知
- 需要 Facebook 開發者審批（`pages_messaging` 權限需要人工審查）
- 我們的方案：**個人帳號能做什麼，AI 就能做什麼**

**為什麼不用 Selenium/Playwright 直接 launch？**
- macOS Chrome 的 cookies 使用 `login.keychain` 加密，外部程序無法直接讀取
- 解法：透過 CDP 接管一個已經解密 cookies 的 browser session
- 關鍵洞察由 [@Livia-Zaharia/just_facebook_mcp](https://github.com/Livia-Zaharia/just_facebook_mcp) 觸發，但該 repo 使用 headless Chrome 仍有 cookie 讀取限制

## 前置需求

- Python 3.11+
- [ungoogled-chromium](https://ungoogled-privacy.github.io/)（brew install --cask chromium）
- macOS（已測試）
- Linux/WSL 理論上支援（需調整 profile 路徑）

## 安裝

```bash
git clone https://github.com/whypuss/social-mcp.git
cd social-mcp
uv sync
```

## 設定

### 1. 建立獨立 Chromium profile

建議使用獨立 profile，避免影響你日常的 Chrome session：

```bash
# 建立 FacebookMCP profile 目錄
mkdir -p ~/Library/Application\ Support/Chromium/FacebookMCP
```

### 2. 在 Chromium 登入 Facebook

```bash
open -a Chromium --args --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP"
```

在瀏覽器中正常登入 Facebook。**只需做一次。**

### 3. 啟動 CDP Server

讓 Chromium 在背景以 remote debugging 模式運行：

```bash
# 方法 A：手動啟動
"/Applications/Chromium.app/Contents/MacOS/Chromium" \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP" \
  --profile-directory="Default" &

# 方法 B：用 launchd 自動啟動（macOS）
# 見 scripts/ 裡的 launchd plist 範例
```

## 使用方式

### 直接測試發文

```bash
uv run python -m social_mcp.post_facebook "測試訊息 from AI Agent 🚀"
```

### 啟動 MCP Server（Hermes Agent / Claude Desktop）

```bash
uv run social-mcp --debug
```

### Hermes Agent 設定

在 `~/.hermes/config.yaml` 加入：

```yaml
mcp_servers:
  personal-social:
    command: "/path/to/.venv/bin/python"
    args: ["/path/to/social-mcp/social_mcp/mcp_server.py"]
```

重啟 Hermes Gateway 後生效。

### Claude Desktop 設定

在 `~/Library/Application Support/Claude/claude_desktop_config.json` 加入：

```json
{
  "mcpServers": {
    "social": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/social-mcp", "social-mcp"]
    }
  }
}
```

## 工具使用範例

```
你：幫我發一篇 Facebook 帖：「AI Agent 自動發文測試」
Agent → post_facebook(message="AI Agent 自動發文測試")
       → ✅ Post published successfully!

你：讀取我的 Messenger 私訊
Agent → read_messenger()
       → ### Messenger 私訊摘要
         | 對話 |
         | :--- |
         | 張三 | ...

你：看看 Facebook 有沒有通知
Agent → read_notifications()
       → ### Facebook 通知摘要
         | 通知 |
         | :--- |
         | Kate Ngu 讚好你的回應 | ...
```

## CDP 端口與 profile 設定

| 設定 | 值 |
|------|-----|
| Chromium 路徑 | `/Applications/Chromium.app/Contents/MacOS/Chromium` |
| CDP 端口 | `9333` |
| Profile 目錄 | `~/Library/Application Support/Chromium/FacebookMCP` |

若你的 Chromium 路徑不同，在 `social_mcp/browser_hijack.py` 裡修改 `CHROMIUM_PATH`。

## 常見問題

**Q: 為什麼要用 ungoogled-chromium（Chromium）而不是 Google Chrome？**
A: Chromium 是完全開源的，沒有 Google 更新推送等問題。任何 Chromium 都支援 `--remote-debugging-port`。

**Q: 為什麼不直接用 Playwright launch headless Chrome？**
A: macOS Chrome 的 cookies 會用 login keychain 加密。Playwright 新啟動的 headless Chrome 處於「未解密」狀態，Facebook 會偵測到異常 session。

**Q: session 會過期嗎？**
A: Facebook 有時會要求重新驗證裝置。若操作失敗，重新在 CDP Chromium 視窗登入一次即可。

**Q: 支援粉絲專頁嗎？**
A: 目前主要支援個人帳號。粉絲專頁操作（以粉絲團身份發文）需要切換到粉絲團身份，Facebook 頁面元件稍有不同，可能需要調整 DOM selector。

**Q: 支援 Instagram / Threads 嗎？**
A: ✅ Threads 支援已實測成功！見下方「Threads 發文」章節。Instagram 尚未測試。

## Threads 發文

Threads 使用和 Facebook 相同的 CDP Browser Hijacking 架構。Chromium 登入 Facebook 後，Threads 同步登入，無需另外設定。

### 直接測試文字帖

```bash
uv run python scripts/post_threads "測試訊息 from AI Agent 🚀"
```

### 帶圖片發文

```bash
uv run python scripts/post_threads "Windows 11 SSD 速度問題" --image /tmp/ssd.jpg
```

### Python API

```python
from social_mcp.post_threads import post_threads

# 純文字
result = await post_threads("測試文字帖")

# 圖文並茂
result = await post_threads(
    message="Windows 11 升級後 SSD 速度跌至 1/4？",
    image_path="/tmp/ssd.jpg"
)
print(result)  # ✅ Posted to Threads in 9.2s
```

### Threads 發文原理（與 Facebook 的差異）

| 步驟 | Facebook | Threads |
|------|----------|---------|
| 開啟 composer | `aria-label=" 建立帖子 "` | `aria-label=" 文字欄位空白。請輸入內容以撰寫新貼文。 "` |
| 編輯器 | 標準 `contenteditable` | Lexical `data-lexical-editor` |
| 發布按鈕 | 直接在頁面 DOM | 在 `role="dialog"` 內（含「發佈」文字） |
| 圖片上傳 | 點擊按鈕 → OS 文件選擇 | 找 dialog 內 `input[type="file"]` → `set_input_files()` |
| 發布後行為 | Dialog 關閉 | Dialog 關閉，**留在 profile 頁面不跳轉** |

**驗證方式**：發布後 reload profile 頁面，從 DOM 中確認內容出現。

## 安全性說明

- **Cookies 永遠留在本地**：所有操作都在你自己機器的 Chromium 內，cookies 不會離開你的電腦
- **不使用任何第三方 API**：不走 Meta Graph API，不需要 access token
- **獨立 profile**：建議使用專用 profile，避免影響日常瀏覽 session
- **警告**：大量自動化操作可能違反 Meta 服務條款，請自行評估風險。本工具僅供個人用途。

## 開發

```bash
# 安裝
uv sync

# 程式碼檢查
uv run ruff check social_mcp/

# 單元測試
uv run pytest
```

## 架構

```
social_mcp/
├── __init__.py          # 套件起點
├── browser_hijack.py    # CDP 瀏覽器接管核心
├── mcp_server.py        # MCP Server（stdio 模式）
├── post_facebook.py     # Facebook 發文腳本
└── post_threads.py      # Threads 發文腳本（支援圖文）
```

## 授權

MIT License
