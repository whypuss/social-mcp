# social-mcp

> 用 CDP Browser Hijacking 操作主流社交平台——不需要 API Token，不需要開發者帳號。

透過你自己瀏覽器的登入 session，AI Agent 用 CDP 接管來發文、爬蟲、對話。

## 2026-04-25 實測成果

| 平台 | 功能 | 狀態 |
|------|------|------|
| **X.com** | 搜索熱門話題、爬蟲 | ✅ 實測成功 |
| **Gemini** | AI 對話（分析項目、生成內容） | ✅ 實測成功 |
| **Facebook** | 發布文字/圖片帖、讀私訊、讀通知 | ✅ 實測成功 |
| **Threads** | 發布文字/圖片帖 | ✅ 實測成功 |
| **Instagram** | 發布圖片帖 | ✅ 實測成功 |

![Status](https://img.shields.io/badge/Status-Working-brightgreen)
![Platform](https://img.shields.io/badge/Platform-Multi--Platform-blue)

## 原理

```
你親自登入一次（Chromium — FacebookMCP profile）
         ↓
Chromium 啟動 --remote-debugging-port=9333
         ↓
Playwright CDP 接管瀏覽器
         ↓
直接操作 DOM（發文 / 爬蟲 / 對話）
```

**為什麼不走官方 API？**
- Facebook Graph API 只能操作粉絲專頁，**個人帳號的私訊/通知讀不到**
- X API 要付費才能發文
- 我們的方案：**你的帳號能做什麼，AI 就能做什麼**

**為什麼不直接 launch Playwright？**
- macOS Chrome cookies 用 `login.keychain` 加密，外部程序無法解密
- 透過 CDP 接管一個已解密 session，繞過這個限制

## 安裝

```bash
git clone https://github.com/whypuss/social-mcp.git
cd social-mcp
uv sync
```

## 設定

### 1. 建立獨立 Chromium profile

```bash
mkdir -p ~/Library/Application\ Support/Chromium/FacebookMCP
```

### 2. 登入各平台

```bash
open -a Chromium --args --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP"
```

在瀏覽器中分別登入：
- **Facebook**（只需一次）
- **Instagram**（只需一次）
- **Threads**（Facebook 登入後同步）
- **Google**（Gemini 用，選擇性）
- X.com 不需要登入

### 3. 啟動 CDP Server

```bash
"/Applications/Chromium.app/Contents/MacOS/Chromium" \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP" \
  --profile-directory="Default" &
```

### 4. 支援多個 CDP 端口

`browser_hijack.py` 會自動檢測 `9333` 或 `9222`，哪個有活躍 session 就用哪個。

## 使用範例

### 直接發文測試

```bash
# Facebook 文字帖
uv run python -m social_mcp.post_facebook "測試訊息 🚀"

# Facebook 圖文帖
uv run python -m social_mcp.post_facebook "測試訊息" /tmp/image.jpg

# Threads 圖文帖
uv run python scripts/post_threads "測試訊息 🚀" --image /tmp/image.jpg

# Instagram 圖文帖
uv run python scripts/post_instagram "caption text" /tmp/image.jpg
```

### AI 對話（Gemini）

用 CDP 接管 Gemini 網頁，直接對話：

```python
# 在 Gemini 頁面輸入 prompt，自動分析 GitHub 項目
prompt = "請分析 https://github.com/whypuss/social-mcp"
# → Gemini 回傳完整分析報告
```

### X.com 爬蟲

```python
# 爬取熱門話題（20ms 拿到數據）
await page.goto("https://x.com/explore/tabs/trending")
trending = await page.evaluate("""
    () => {
        const cells = document.querySelectorAll("[data-testid='cellInnerDiv']");
        return Array.from(cells).map(c => c.innerText).filter(t => t.length > 20);
    }
""")
# → ["#TermMaxPuzzleChallenge", "#SECAwards", ...]
```

## 架構

```
social_mcp/
├── browser_hijack.py    # CDP 接管核心（多端口自動檢測）
├── mcp_server.py       # MCP Server（Hermes Agent / Claude Desktop）
├── post_facebook.py    # Facebook 發文（支援圖片）
├── post_threads.py     # Threads 發文（支援圖片）
├── post_instagram.py   # Instagram 發文（支援圖片）
└── scripts/
    └── post_instagram.py  # Instagram 獨立腳本
```

## 全新電腦復現

只需三樣東西：

| 項目 | 備份/復現 |
|------|-----------|
| `Chromium.app` | `brew install --cask chromium` |
| `FacebookMCP` profile | `~/Library/Application Support/Chromium/FacebookMCP/`（拷貝） |
| social-mcp 環境 | `git clone + uv sync` |

拷貝 `FacebookMCP` profile 後，所有平台（Facebook / IG / Threads / Google）全部復現，**不需要重新登入**。

## 常見問題

**Q: session 會過期嗎？**
A: Facebook 有時會要求重新驗證。操作失敗時，在 CDP Chromium 視窗重新登入一次即可。

**Q: 支援粉絲專頁嗎？**
A: 目前支援個人帳號。粉絲專頁需切換身份，DOM selector 需要調整。

**Q: X.com 需要登入嗎？**
A: 不需要。X.com 的內容可以匿名瀏覽。

**Q: Gemini 生成圖片可以嗎？**
A: 免費版不行，需要升級 Google AI Plus。文字對話和項目分析都正常。

## 安全性

- Cookies 永遠留在本地，不會傳到第三方
- 不使用任何官方 API，不需要 access token
- 獨立 profile，不影響日常瀏覽 session

**警告**：大量自動化操作可能違反各平台服務條款，請自行評估風險。

## 授權

MIT License
