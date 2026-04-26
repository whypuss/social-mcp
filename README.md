# ai-cdp-browser

> 用 CDP Browser Hijacking 操作主流社交平台——不需要 API Token，不需要開發者帳號。

透過你自己瀏覽器的登入 session，AI Agent 用 CDP 接管來發文、爬蟲、對話。

## 2026-04-26 實測成果

| 平台 | 功能 | 狀態 |
|------|------|------|
| **Facebook** | 發布文字/圖片帖、讀私訊、讀通知 | ✅ 實測成功 |
| **Instagram** | 發布圖片帖（caption 直達 sharing 頁） | ✅ 實測成功 |
| **Threads** | 發布文字/圖片帖 | ✅ 實測成功 |
| **Google Trends** | HK 熱門話題爬蟲 | ✅ 實測成功 |
| **Gemini** | AI 對話、內容生成 | ✅ 實測成功 |

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
git clone https://github.com/whypuss/ai-cdp-browser.git
cd ai-cdp-browser
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

### 3. 啟動 CDP Server

```bash
"/Applications/Chromium.app/Contents/MacOS/Chromium" \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/Library/Application Support/Chromium/FacebookMCP" \
  --profile-directory="Default" &
```

### 4. 自動偵測

`browser_hijack.py` 會自動檢測 `9333` 或 `9222`，哪個有活躍 session 就用哪個。

## 使用範例

### 單平台發文測試

```bash
# Facebook 圖文帖
uv run python -m social_mcp.post_facebook "caption" /tmp/image.jpg

# Instagram 圖文帖
uv run python -m social_mcp.post_ig "caption text" /tmp/image.jpg

# Threads 圖文帖
uv run python -m social_mcp.post_threads "caption" /tmp/image.jpg
```

### 自動 workflow（每 2 小時自動發文）

```bash
# Google Trends HK → Gemini 生成原創 caption → FB + IG + Threads 同時發布
uv run python scripts/social_workflow.py
```

防重複邏輯：`posted_topics.json` 記錄最近 12 個已發布 topic，每個 topic 只發一次。

### Gemini AI 對話

用 CDP 接管 Gemini 網頁，直接對話：

```python
# 在 Gemini 頁面輸入 prompt，自動分析 GitHub 項目
prompt = "請分析 https://github.com/whypuss/ai-cdp-browser"
# → Gemini 回傳完整分析報告
```

## 架構

```
ai-cdp-browser/
├── social_mcp/
│   ├── browser_hijack.py    # CDP 接管核心（多端口自動檢測）
│   ├── mcp_server.py       # MCP Server（Hermes Agent / Claude Desktop）
│   ├── post_facebook.py     # Facebook 發文（支援圖片）
│   ├── post_ig.py          # Instagram 發文（支援圖片，2026-04 實測 DOM 版）
│   ├── post_threads.py      # Threads 發文（支援圖片）
│   └── browser_hijack.py
│
└── scripts/
    └── social_workflow.py   # 統一 workflow：Google Trends → Gemini → 三平臺自動發文
```

## 全新電腦復現

只需三樣東西：

| 項目 | 備份/復現 |
|------|-----------|
| `Chromium.app` | `brew install --cask chromium` |
| `FacebookMCP` profile | `~/Library/Application Support/Chromium/FacebookMCP/`（拷貝） |
| ai-cdp-browser 環境 | `git clone + uv sync` |

拷貝 `FacebookMCP` profile 後，所有平臺（Facebook / IG / Threads / Google）全部復現，**不需要重新登入**。

## 常見問題

**Q: session 會過期嗎？**
A: Facebook 有時會要求重新驗證。操作失敗時，在 CDP Chromium 視窗重新登入一次即可。

**Q: 支援粉絲專頁嗎？**
A: 目前支援個人帳號。粉絲專頁需切換身份，DOM selector 需要調整。

**Q: IG 發文卡在「下一步」？**
A: `post_ig.py` 已修復。IG 新 UI 有時跳過 filter 頁直接到 sharing 頁，腳本已支援自動判斷。

**Q: Gemini 生成圖片可以嗎？**
A: 免費版不行，需要升級 Google AI Plus。文字對話和內容分析都正常。

## 安全性

- Cookies 永遠留在本地，不會傳到第三方
- 不使用任何官方 API，不需要 access token
- 獨立 profile，不影響日常瀏覽 session

**警告**：大量自動化操作可能違反各平臺服務條款，請自行評估風險。

## 授權

MIT License
