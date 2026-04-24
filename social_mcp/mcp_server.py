"""
social-mcp — MCP Server

用法：
  uv run social-mcp                    # 直接啟動
  uv run social-mcp --debug            # 除錯模式

在 Claude Desktop 的 claude_desktop_config.json 加入：
{
  "mcpServers": {
    "social": {
      "command": "uv",
      "args": ["run", "--project", "/路徑/到/social-mcp", "social-mcp"]
    }
  }
}
"""

import argparse
import asyncio
import logging
import os
import sys
import json
from datetime import datetime

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .chrome_session import get_meta_cookies, ChromeCookie
from .meta_api import MetaAPI, PLATFORMS

log = logging.getLogger(__name__)

# ──── Logging ────

def setup_logging(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


# ──── MCP Tools ────

TOOLS = [
    Tool(
        name="health_check",
        description="檢查 Chrome cookies 是否有效，回傳登入狀態。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_messenger_inbox",
        description="讀取 Messenger 收件箱最新對話。",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "最多回傳幾條對話（預設 10）",
                    "default": 10,
                },
            },
        },
    ),
    Tool(
        name="send_messenger",
        description="發送 Messenger 私信給指定用戶。",
        inputSchema={
            "type": "object",
            "required": ["thread_id", "message"],
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "Messenger 對話 ID（從 inbox 取得）",
                },
                "message": {
                    "type": "string",
                    "description": "要發送的訊息內容",
                },
            },
        },
    ),
    Tool(
        name="post_facebook",
        description="發 Facebook 粉絲團文字帖。",
        inputSchema={
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": {
                    "type": "string",
                    "description": "貼文內容",
                },
            },
        },
    ),
    Tool(
        name="post_instagram",
        description="發 Instagram 圖文帖（需 Instagram Business 帳號）。",
        inputSchema={
            "type": "object",
            "required": ["caption"],
            "properties": {
                "caption": {
                    "type": "string",
                    "description": "圖片說明文字",
                },
                "image_url": {
                    "type": "string",
                    "description": "圖片 URL（可選）",
                },
            },
        },
    ),
    Tool(
        name="post_threads",
        description="發 Threads 文字帖（需 Instagram Business 帳號）。",
        inputSchema={
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Threads 內文",
                },
            },
        },
    ),
    Tool(
        name="get_facebook_page_info",
        description="取得 Facebook 粉絲頁基本資訊（名稱、粉絲數、ID）。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


# ──── MCP Handlers ────

async def handle_tool_call(name: str, arguments: dict, api: MetaAPI) -> list[TextContent]:
    """根據工具名稱 dispatch 到對應的 API 方法。"""

    if name == "health_check":
        result = await api.health_check()
        return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

    elif name == "get_messenger_inbox":
        limit = arguments.get("limit", 10)
        result = await api.get_messenger_inbox(limit=limit)
        if result.success:
            lines = [f"## Messenger 收件箱（共 {len(result.messages)} 筆）\n"]
            for i, msg in enumerate(result.messages, 1):
                ts = msg.timestamp[:19] if msg.timestamp else ""
                lines.append(f"{i}. [{ts}] {msg.sender}：{msg.text[:80]}")
            return [TextContent(type="text", text="\n".join(lines))]
        return [TextContent(type="text", text=f"❌ 取得失敗：{result.error}")]

    elif name == "send_messenger":
        thread_id = arguments["thread_id"]
        message = arguments["message"]
        result = await api.send_messenger_message(thread_id, message)
        if result.success:
            return [TextContent(type="text", text=f"✅ 訊息已發送至 {thread_id}：{message}")]
        return [TextContent(type="text", text=f"❌ 發送失敗：{result.error}")]

    elif name == "post_facebook":
        message = arguments["message"]
        page_id = os.getenv("FACEBOOK_PAGE_ID")
        result = await api.post_to_facebook(message, page_id=page_id)
        if result.success:
            return [TextContent(type="text", text=f"✅ 成功發帖！\nURL: {result.url}")]
        return [TextContent(type="text", text=f"❌ 發帖失敗：{result.error}")]

    elif name == "post_instagram":
        caption = arguments["caption"]
        image_url = arguments.get("image_url")
        result = await api.post_to_instagram(caption=caption, image_url=image_url)
        if result.success:
            return [TextContent(type="text", text=f"✅ IG 發文成功！ID: {result.post_id}")]
        return [TextContent(type="text", text=f"❌ IG 發文失敗：{result.error}")]

    elif name == "post_threads":
        text = arguments["text"]
        result = await api.post_to_threads(text=text)
        if result.success:
            return [TextContent(type="text", text=f"✅ Threads 發文成功！ID: {result.post_id}")]
        return [TextContent(type="text", text=f"❌ Threads 發文失敗：{result.error}")]

    elif name == "get_facebook_page_info":
        page_id = os.getenv("FACEBOOK_PAGE_ID")
        if not page_id:
            page_id = await api.get_page_id()
        if not page_id:
            return [TextContent(type="text", text="❌ 無法取得 Page ID")]
        try:
            token = await api._get_page_token_from_cookie(page_id)
            if not token:
                return [TextContent(type="text", text="❌ 無法取得 Page Access Token")]
            info = await api._graph_get(page_id, params={
                "fields": "name,fan_count,id",
                "access_token": token,
            })
            return [TextContent(type="text", text=json.dumps(info, indent=2, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=f"❌ 錯誤：{e}")]

    return [TextContent(type="text", text=f"❌ 未知工具: {name}")]


# ──── Main ────

async def main():
    parser = argparse.ArgumentParser(description="social-mcp — Meta Social MCP Server")
    parser.add_argument("--debug", action="store_true", help="除錯模式")
    parser.add_argument("--port", type=int, default=None, help="CDP 連接埠（預設自動偵測）")
    args = parser.parse_args()

    setup_logging(args.debug)
    log.info("[social-mcp] 啟動中...")

    # 預先初始化 cookies
    cookies = await get_meta_cookies()
    api = MetaAPI(cookies)
    log.info(f"[social-mcp] 取得 {len(cookies)} 個 Meta cookies，user_id={api.user_id}")

    # 建立 MCP Server
    server = Server("social-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            return await handle_tool_call(name, arguments, api)
        except Exception as e:
            log.exception(f"[social-mcp] Tool {name} 錯誤")
            return [TextContent(type="text", text=f"❌ 例外錯誤: {e}")]

    # 啟動 stdio server（MCP 標準輸入輸出模式）
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
