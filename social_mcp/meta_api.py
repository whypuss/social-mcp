"""
Meta API — 直接 call Facebook/Instagram/Threads Graph API。

認證方式：直接用 Chrome cookies（c_user, xs, fr 等）來建立一個 session，
然後 call Graph API。Facebook 允許用 cookie-based session 來操作粉絲頁。

注意：這需要你的 Facebook 帳號已啟用「允許使用舊版 Cookie 的應用」。
設定位置：Facebook → 設定 → 安全與登入 → 允許使用舊版 Cookie 的應用 → 開啟
"""

import asyncio
import json
import logging
import os
import re
import time
import httpx
from dataclasses import dataclass, field
from typing import Any, Optional

from .chrome_session import get_meta_cookies, cookies_to_simple_dict, ChromeCookie

log = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v22.0"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.facebook.com/",
}


@dataclass
class MetaPlatform:
    name: str  # "facebook" | "instagram" | "threads"
    domain_cookies: list[str]
    api_base: str


PLATFORMS = {
    "facebook": MetaPlatform("facebook", [".facebook.com"], GRAPH_API_BASE),
    "instagram": MetaPlatform("instagram", [".instagram.com"], "https://graph.facebook.com/v22.0"),
    "threads": MetaPlatform("threads", [".threads.net", ".instagram.com"], "https://graph.facebook.com/v22.0"),
}


@dataclass
class PostResult:
    success: bool
    post_id: Optional[str] = None
    error: Optional[str] = None
    url: Optional[str] = None


@dataclass
class InboxMessage:
    sender: str
    sender_id: str
    text: str
    timestamp: str
    message_id: str
    thread_id: Optional[str] = None


@dataclass
class InboxResult:
    success: bool
    messages: list[InboxMessage] = field(default_factory=list)
    error: Optional[str] = None


class MetaAPI:
    """
    Meta Graph API client，直接用 Chrome cookies 認證。
    """

    def __init__(self, cookies: list[ChromeCookie] = None):
        self.cookies = cookies or []
        self.cookie_dict = cookies_to_simple_dict(cookies) if cookies else {}
        self.cookie_str = "; ".join(f"{c.name}={c.value}" for c in cookies) if cookies else ""
        self.headers = dict(HEADERS)
        self.headers["Cookie"] = self.cookie_str

        # 從 c_user cookie 取得 user ID
        self.user_id = self.cookie_dict.get("c_user", "")

    def _get_client(self) -> httpx.AsyncClient:
        """每次請求都用新的 client，headers 包含最新 cookies。"""
        return httpx.AsyncClient(
            headers=self.headers,
            follow_redirects=True,
            timeout=30.0,
        )

    async def _graph_get(self, endpoint: str, params: dict = None) -> dict:
        """GET Graph API（需要 access_token 的場景）"""
        async with self._get_client() as client:
            resp = await client.get(f"{GRAPH_API_BASE}/{endpoint}", params=params)
            resp.raise_for_status()
            return resp.json()

    async def _graph_post(self, endpoint: str, data: dict = None) -> dict:
        """POST Graph API"""
        async with self._get_client() as client:
            resp = await client.post(f"{GRAPH_API_BASE}/{endpoint}", data=data)
            resp.raise_for_status()
            return resp.json()

    # ──── Facebook ────

    async def get_page_id(self) -> Optional[str]:
        """
        從 c_user 找到綁定的第一個粉絲頁 ID。
        透過 Facebook 內部 API 直接拿，不需要 Page Access Token。
        """
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    "https://www.facebook.com/api/browser/graphql/",
                    params={
                        "variables": json.dumps({"fetch_type": "LIST"}),
                        "doc_id": "5917646661339658",
                    },
                    headers={
                        **self.headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-FB-Friendly-Name": "PageCometAdminNavRootQuery",
                    },
                )
                data = resp.json()
                pages = data.get("data", {}).get("admin_navigation", [])
                if pages:
                    return pages[0].get("page_id")
        except Exception as e:
            log.error(f"[MetaAPI] get_page_id 失敗: {e}")
        return None

    async def post_to_facebook(self, message: str, page_id: str = None) -> PostResult:
        """發 Facebook 文字帖"""
        if not page_id:
            page_id = os.getenv("FACEBOOK_PAGE_ID")
        if not page_id:
            return PostResult(success=False, error="需要 FACEBOOK_PAGE_ID")

        try:
            # 用 Cookie 去拿一個短期 Page Token
            token = await self._get_page_token_from_cookie(page_id)
            if not token:
                return PostResult(success=False, error="無法取得 Page Access Token")

            async with self._get_client() as client:
                resp = await client.post(
                    f"{GRAPH_API_BASE}/{page_id}/feed",
                    data={"message": message, "access_token": token},
                )
            result = resp.json()
            if "id" in result:
                post_id = result["id"]
                return PostResult(
                    success=True,
                    post_id=post_id,
                    url=f"https://www.facebook.com/{post_id}",
                )
            else:
                return PostResult(success=False, error=str(result))
        except Exception as e:
            return PostResult(success=False, error=str(e))

    async def _get_page_token_from_cookie(self, page_id: str) -> Optional[str]:
        """
        用 Facebook 舊版 API 直接從 cookie session 換短期 Page Token。
        不需要 Server-Side OAuth flow。
        """
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    "https://www.facebook.com/v22.0/dialog/oauth",
                    params={
                        "access_token": "self",
                        "next": f"https://www.facebook.com/{page_id}",
                        "display": "popup",
                        "response_type": "token",
                    },
                )
                # 舊版方式已經不可用，改用後面的直接頁面方式
        except Exception:
            pass

        # 備用：直接從 Page Admin 頁面拿 token
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    f"https://www.facebook.com/{page_id}/settings",
                    headers=self.headers,
                )
                text = resp.text
                # 找 __eql 或 access_token
                m = re.search(r'access_token="([^"]+)"', text)
                if m:
                    return m.group(1)
                m = re.search(r'"access_token":"([^"]+)"', text)
                if m:
                    return m.group(1)
        except Exception:
            pass

        # 舊版交換
        try:
            async with self._get_client() as client:
                resp = await client.post(
                    f"{GRAPH_API_BASE}/oauth/access_token",
                    data={
                        "grant_type": "fb_exchange_token",
                        "client_id": os.getenv("FB_APP_ID", "1249215749037029"),
                        "client_secret": os.getenv("FB_APP_SECRET", ""),
                        "fb_exchange_token": self.cookie_dict.get("xs", ""),
                    },
                )
                result = resp.json()
                return result.get("access_token")
        except Exception:
            pass

        return None

    async def get_messenger_inbox(self, limit: int = 10) -> InboxResult:
        """
        讀取 Messenger 收件箱。
        透過 Facebook 行動版 API，不需要 token。
        """
        try:
            async with self._get_client() as client:
                resp = await client.get(
                    "https://www.facebook.com/api/graphql/",
                    params={
                        "variables": json.dumps({
                            "inboxType": "FB_THREADS",
                            "limit": limit,
                        }),
                        "doc_id": "35665612601839492",
                    },
                    headers={
                        **self.headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-FB-Friendly-Name": "InboxInboxQuery",
                    },
                )
                data = resp.json()
                messages = self._parse_messenger_response(data)
                return InboxResult(success=True, messages=messages)
        except Exception as e:
            return InboxResult(success=False, error=str(e))

    async def send_messenger_message(self, thread_id: str, text: str) -> PostResult:
        """發送 Messenger 私信"""
        try:
            async with self._get_client() as client:
                resp = await client.post(
                    "https://www.facebook.com/api/graphql/",
                    data={
                        "variables": json.dumps({
                            "message": text,
                            "threadId": thread_id,
                        }),
                        "doc_id": "21089166626515972",
                    },
                    headers={
                        **self.headers,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                result = resp.json()
                if result.get("data", {}).get("message_send"):
                    return PostResult(success=True, post_id=thread_id)
                return PostResult(success=False, error=str(result))
        except Exception as e:
            return PostResult(success=False, error=str(e))

    def _parse_messenger_response(self, data: dict) -> list[InboxMessage]:
        messages = []
        try:
            threads = data.get("data", {}).get("viewer", {}).get("message_threads", {}).get("nodes", [])
            for thread in threads:
                for msg in thread.get("messages", {}).get("nodes", []):
                    messages.append(InboxMessage(
                        sender=msg.get("snippet_sender", "未知"),
                        sender_id=msg.get("sender_id", ""),
                        text=msg.get("text", ""),
                        timestamp=msg.get("created_at", ""),
                        message_id=msg.get("id", ""),
                        thread_id=thread.get("thread_key", ""),
                    ))
        except Exception as e:
            log.warning(f"[MetaAPI] 解析 Messenger 回應失敗: {e}")
        return messages

    # ──── Instagram ────

    async def get_ig_user_id(self) -> Optional[str]:
        """取得綁定的 Instagram Business 帳號 ID"""
        try:
            # 從 Facebook Page 找綁定的 IG
            page_id = os.getenv("FACEBOOK_PAGE_ID")
            if not page_id:
                page_id = await self.get_page_id()
            if not page_id:
                return None

            token = await self._get_page_token_from_cookie(page_id)
            if not token:
                return None

            async with self._get_client() as client:
                resp = await client.get(
                    f"{GRAPH_API_BASE}/{page_id}",
                    params={"fields": "instagram_business_account{id,username}", "access_token": token},
                )
                ig = resp.json().get("instagram_business_account", {})
                return ig.get("id")
        except Exception as e:
            log.error(f"[MetaAPI] get_ig_user_id 失敗: {e}")
        return None

    async def post_to_instagram(self, caption: str, image_url: str = None, local_image_path: str = None) -> PostResult:
        """發 IG 圖文帖（需要 Instagram Business 帳號）"""
        try:
            ig_user_id = os.getenv("IG_USER_ID") or await self.get_ig_user_id()
            if not ig_user_id:
                return PostResult(success=False, error="需要 IG_USER_ID 或先綁定 Facebook Page")

            page_id = os.getenv("FACEBOOK_PAGE_ID") or await self.get_page_id()
            token = await self._get_page_token_from_cookie(page_id)
            if not token:
                return PostResult(success=False, error="無法取得 access token")

            async with self._get_client() as client:
                # 建立 container
                container_data = {"caption": caption, "access_token": token}
                if image_url:
                    container_data["image_url"] = image_url
                elif local_image_path:
                    # 先上傳圖片
                    with open(local_image_path, "rb") as f:
                        img_data = f.read()
                    img_resp = await client.post(
                        f"{GRAPH_API_BASE}/{ig_user_id}/photos",
                        data={"access_token": token, "caption": caption},
                        files={"source": (os.path.basename(local_image_path), img_data, "image/jpeg")},
                    )
                    result = img_resp.json()
                    if "id" in result:
                        return PostResult(success=True, post_id=str(result["id"]), url=f"https://www.instagram.com/p/{result.get('code', '')}")
                    return PostResult(success=False, error=str(result))
                    return PostResult(success=False, error="local_image_path 尚需完整實現")

                container_resp = await client.post(
                    f"{GRAPH_API_BASE}/{ig_user_id}/media",
                    data=container_data,
                )
                container = container_resp.json()
                container_id = container.get("id")
                if not container_id:
                    return PostResult(success=False, error=str(container))

                # 發布
                publish_resp = await client.post(
                    f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
                    data={"creation_id": container_id, "access_token": token},
                )
                result = publish_resp.json()
                if "id" in result:
                    return PostResult(success=True, post_id=str(result["id"]))
                return PostResult(success=False, error=str(result))
        except Exception as e:
            return PostResult(success=False, error=str(e))

    # ──── Threads ────

    async def post_to_threads(self, text: str, image_url: str = None) -> PostResult:
        """發 Threads 文字帖（需 Instagram Business 帳號）"""
        try:
            ig_user_id = os.getenv("IG_USER_ID") or await self.get_ig_user_id()
            if not ig_user_id:
                return PostResult(success=False, error="需要 IG_USER_ID")

            page_id = os.getenv("FACEBOOK_PAGE_ID") or await self.get_page_id()
            token = await self._get_page_token_from_cookie(page_id)
            if not token:
                return PostResult(success=False, error="無法取得 access token")

            async with self._get_client() as client:
                # 建立 threads media container
                post_data = {"caption": text, "access_token": token}
                if image_url:
                    post_data["image_url"] = image_url

                container_resp = await client.post(
                    f"{GRAPH_API_BASE}/{ig_user_id}/threads",
                    data=post_data,
                )
                container = container_resp.json()
                container_id = container.get("id")
                if not container_id:
                    return PostResult(success=False, error=str(container))

                # 發布
                publish_resp = await client.post(
                    f"{GRAPH_API_BASE}/{ig_user_id}/threads_publish",
                    data={"creation_id": container_id, "access_token": token},
                )
                result = publish_resp.json()
                if "id" in result:
                    return PostResult(success=True, post_id=str(result["id"]))
                return PostResult(success=False, error=str(result))
        except Exception as e:
            return PostResult(success=False, error=str(e))

    # ──── 通用 ────

    async def health_check(self) -> dict:
        """檢查 cookie 是否有效"""
        has_user = bool(self.user_id)
        has_xs = "xs" in self.cookie_dict
        return {
            "user_id": self.user_id,
            "has_user_cookie": has_user,
            "has_session_cookie": has_xs,
            "cookie_count": len(self.cookies),
            "platforms": list(PLATFORMS.keys()),
        }


# Singleton
_api: Optional[MetaAPI] = None


async def get_api() -> MetaAPI:
    global _api
    if _api is None:
        cookies = await get_meta_cookies()
        _api = MetaAPI(cookies)
        log.info(f"[MetaAPI] 初始化完成，user_id={_api.user_id}, cookies={len(cookies)}")
    return _api
