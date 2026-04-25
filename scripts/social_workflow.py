"""
social_workflow.py — Google Trends HK → 圖文 → FB / IG / Threads

防重複邏輯：
- posted_topics.json 記錄最近 12 個已發布 topic
- 每次取 topic 時跳過已出現過的，確保每個 topic 只發一次
- 12 個足够覆蓋 24 小時（每 2 小時跑一次）

流程（單一 CDP 連接）：
1. Google Trends HK 熱門趨勢 → 過濾 topic（跳過已發布的）
2. Google Images 搜尋每個 topic → base64 圖片 → 存檔
3. 隨機角度原創 caption
4. 關閉多餘頁面（保持 ≤ 6 個）
5. 發布到 FB / IG / Threads
6. 更新 posted_topics.json

Cron: 每 2 小時執行一次
用法: uv run python scripts/social_workflow.py
"""
import asyncio
import base64
import json
import os
import random
import re
import sys
import time
import urllib.parse
import logging
from pathlib import Path
from playwright.async_api import async_playwright

# Setup paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

CDP_PORT = 9333
POSTED_TOPICS_FILE = Path.home() / ".hermes/cron/output/posted_topics.json"
MAX_POSTED = 12  # 記錄最近 12 個，超過的自動移除最舊的


# ============================================================================
# CDP helpers
# ============================================================================

def _get_cdp_browser(port=9333):
    import urllib.request
    for p in [port, 9222]:
        try:
            req = urllib.request.Request(
                f"http://localhost:{p}/json",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                import json
                tabs = json.loads(r.read())
                return p, tabs
        except Exception:
            pass
    return None, []


# ============================================================================
# 防重複： posted_topics 管理
# ============================================================================

def load_posted_topics() -> list:
    """讀取最近已發布的 topic 清單"""
    if not POSTED_TOPICS_FILE.exists():
        return []
    try:
        with open(POSTED_TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_posted_topics(topics: list):
    """寫入已發布 topic 清單（只保留最近 MAX_POSTED 個）"""
    POSTED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 MAX_POSTED 個
    trimmed = topics[-MAX_POSTED:]
    with open(POSTED_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def add_posted_topic(topic: str):
    """新增一個 topic 到已發布清單"""
    topics = load_posted_topics()
    # 移除重複（如果有的話）
    topics = [t for t in topics if t != topic]
    topics.append(topic)
    save_posted_topics(topics)


# ============================================================================
# Step 1: Google Trends HK 熱門趨勢
# ============================================================================

async def get_gtrends_hk(browser, ctx, skip_topics: list) -> list:
    """取得 Google Trends HK 熱門話題（跳過已發布的 + 太抽象的關鍵詞）"""
    abstract_keywords = ["1994", "1995", "1996", "1997", "1998", "1999",
                         "2000", "2001", "2002", "2003", "2004",
                         "series", "episode", "ep1", "ep2", "trailer",
                         "awards", "fans", "fammeet"]

    gt_page = None
    for pg in ctx.pages:
        if "trends.google" in pg.url.lower():
            gt_page = pg
            break

    if not gt_page:
        gt_page = await ctx.new_page()

    await gt_page.goto(
        "https://trends.google.com.tw/trending?geo=HK&pli=1",
        wait_until="domcontentloaded",
        timeout=30000
    )
    await asyncio.sleep(5)

    # Scroll to load more topics
    for _ in range(5):
        await gt_page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(0.5)

    topics_raw = await gt_page.evaluate("""() => {
        const rows = document.querySelectorAll('tr.enOdEe-wZVHld-xMbwt');
        const topics = [];
        for (const row of rows) {
            const text = row.innerText || '';
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
            if (lines.length > 0) {
                const topic = lines[0];
                if (topic.length >= 2 && topic.length <= 35) {
                    topics.push(topic);
                }
            }
        }
        return [...new Set(topics)];
    }""")

    # Clean up: remove spaces, filter abstract keywords
    cleaned = []
    seen = set()
    skip_set = set(skip_topics)  # 已發布過的
    for t in topics_raw:
        t_clean = re.sub(r'\s+', '', t).strip()
        if not t_clean or t_clean in seen:
            continue
        seen.add(t_clean)
        lower = t_clean.lower()
        # 跳過已發布
        if t_clean in skip_set:
            continue
        # 跳過抽象關鍵詞
        if any(kw in lower for kw in abstract_keywords if len(kw) > 3):
            continue
        cleaned.append(t_clean)

    return cleaned[:12]


# ============================================================================
# Step 2: Google Images — 拿 base64 圖片
# ============================================================================

async def search_google_image(browser, ctx, topic: str) -> str:
    """用 Google Images 搜尋 topic，回傳圖片路徑"""
    search_q = urllib.parse.quote(topic[:50])

    g_page = None
    for pg in ctx.pages:
        if "google.com" in pg.url.lower() and "tbm=isch" not in pg.url.lower():
            g_page = pg
            break

    if not g_page:
        g_page = await ctx.new_page()

    await g_page.bring_to_front()
    await g_page.goto(
        f"https://www.google.com/search?q={search_q}&tbm=isch&hl=zh-TW",
        wait_until="domcontentloaded",
        timeout=30000
    )
    await asyncio.sleep(3)

    imgs = await g_page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('img'));
        return all
            .filter(img => {
                const w = img.naturalWidth || img.width;
                const h = img.naturalHeight || img.height;
                return w > 100 && h > 80
                    && !img.src.includes('gstatic.com')
                    && !img.src.includes('google.com')
                    && !img.src.includes('favicon');
            })
            .slice(0, 8)
            .map(img => ({ src: img.src, w: img.naturalWidth || img.width, h: img.naturalHeight || img.height }));
    }""")

    for img in imgs:
        src = img["src"]
        if src.startswith("data:image/") and "," in src:
            try:
                b64_data = src.split(",", 1)[1]
                img_bytes = base64.b64decode(b64_data)
                if len(img_bytes) > 5000:
                    out_path = f"/tmp/social_workflow_{int(time.time())}.jpg"
                    with open(out_path, "wb") as f:
                        f.write(img_bytes)
                    log.info(f"[Google Images] 成功存檔 {len(img_bytes)} bytes: {out_path}")
                    return out_path
            except Exception as e:
                log.warning(f"[Google Images] 解碼失敗: {e}")
                continue

    log.warning(f"[Google Images] 找不到足夠大的圖片 for '{topic}'")
    return None


# ============================================================================
# Step 3: 原創風格 caption（4 種隨機角度）
# ============================================================================

def to_traditional(text: str) -> str:
    replacements = [
        ("趋势", "趨勢"), ("热门", "熱門"), ("话题", "話題"),
        ("挑战", "挑戰"), ("视频", "視頻"), ("电影", "電影"),
        ("热门话题", "熱門話題"), ("正在流行", "正在流行"),
        ("足球", "足球"), ("比赛", "比賽"), ("直播", "直播"),
    ]
    result = text
    for old, new in replacements:
        result = result.replace(old, new)
    return result


# ============================================================================
# Gemini caption 生成
# ============================================================================

GEMINI_INPUT = 'div[aria-label="請輸入 Gemini 提示詞"]'


async def _find_gemini_page(ctx):
    """Find or create Gemini page"""
    for pg in ctx.pages:
        if "gemini.google.com" in pg.url:
            return pg
    g_page = await ctx.new_page()
    await g_page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)
    return g_page


async def call_gemini(page, prompt: str, timeout=90) -> str:
    """Send prompt to Gemini, return response text (max 500 chars)"""
    inp = page.locator(GEMINI_INPUT)
    await inp.click()
    await inp.fill("")
    await asyncio.sleep(0.5)
    await inp.type(prompt, delay=40)
    await asyncio.sleep(1)
    await page.keyboard.press("Enter")

    await asyncio.sleep(6)
    start = time.time()

    for _ in range(15):
        await asyncio.sleep(4)

        response = await page.evaluate("""() => {
            const all = Array.from(document.querySelectorAll('.model-response-text'));
            if (all.length === 0) return { status: 'no-response', text: '' };
            const last = all[all.length - 1];
            const text = (last.innerText || '').trim();
            const isProcessing = last.classList.contains('processing-state-visible');
            return { status: isProcessing ? 'processing' : 'done', text };
        }""")

        elapsed = int(time.time() - start)

        # If substantial text (>80 chars) and at least 25s passed, take it
        if len(response['text']) > 80 and elapsed > 25:
            return response['text'][:500]

        if response['status'] == 'done' and len(response['text']) > 5:
            return response['text'][:500]

    # Final fallback
    response = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('.model-response-text'));
        if (all.length === 0) return '';
        return (all[all.length - 1].innerText || '').trim();
    }""")
    return response[:500] if response else "[Gemini timeout]"


async def generate_caption(topic: str, ctx) -> dict:
    """用 Gemini 生成原創 caption（150字广东话）"""
    gemini_page = await _find_gemini_page(ctx)
    await gemini_page.bring_to_front()

    prompt = (
        f"你是一個香港社交媒體內容創作專家。\n"
        f"請為以下話題創作一篇約150字的原創Facebook帖子內容：\n\n"
        f"話題：「{topic}」\n\n"
        f"要求：\n"
        f"1. 以香港廣東話口語撰寫\n"
        f"2. 內容像真人在分享個人觀察或感受，自然地表達\n"
        f"3. 不要加emoji\n"
        f"4. 不要列出關鍵字或hashtag\n"
        f"5. 150字以內\n"
        f"6. 直接輸出內容，不要加標題或「以下是」等前置說明"
    )

    log.info(f"[Gemini] 生成 caption for '{topic}'...")
    response = await call_gemini(gemini_page, prompt)
    log.info(f"[Gemini] 回應 {len(response)} chars: {response[:60]}...")

    # Gemini 回應有時包含 "Gemini 說了" 之類的前綴，剝離它
    clean = re.sub(r"^Gemini[^\\n]*\\n*", "", response).strip()

    return {
        "facebook": {"text": clean[:300]},
        "instagram": {"text": clean[:280]},
        "threads": {"text": clean[:150]},
    }


def prepare_posts(topic: str, image_path: str, ctx=None) -> dict:
    """用 Gemini 生成 caption，image_path 在調用方注入"""
    # Gemini caption generation is async; caller should use generate_caption()
    # This is kept for backwards compat - if ctx not provided, return placeholder
    if ctx is None:
        return {
            "facebook": {"text": f"關於「{topic}」的香港熱門話題", "image": image_path},
            "instagram": {"text": f"「{topic}」香港熱門話題", "image": image_path},
            "threads": {"text": f"「{topic}」", "image": image_path},
        }
    raise NotImplementedError("Use generate_caption() directly")


# ============================================================================
# Step 4: 關閉多餘頁面
# ============================================================================

async def close_extra_pages(ctx, max_pages=6):
    current_count = len(ctx.pages)
    if current_count <= max_pages:
        log.info(f"[PageMgr] {current_count} pages, no need to close")
        return

    pages_to_close = []
    priority_close = []

    for pg in list(ctx.pages):
        u = pg.url
        if "tbm=isch" in u:
            priority_close.append(pg)
            continue
        if "github.com" in u.lower():
            priority_close.append(pg)
            continue

    total_to_close = current_count - max_pages
    for pg in priority_close[:total_to_close]:
        await pg.close()
        pages_to_close.append(pg.url[:50])

    log.info(f"[PageMgr] Closed {len(pages_to_close)} pages: {pages_to_close}")
    log.info(f"[PageMgr] Remaining: {len(ctx.pages)} pages")


# ============================================================================
# Main workflow（單一 CDP 連接）
# ============================================================================

async def run_workflow():
    print("=" * 50)
    print("Social Workflow (Google Trends HK)")
    print("=" * 50)

    # 讀取已發布 topic（防重複）
    posted = load_posted_topics()
    print(f"\n[Init] 已發布 topic ({len(posted)}): {posted[-5:]}")

    async with async_playwright() as p:
        port, _ = _get_cdp_browser()
        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port or 9333}", timeout=20000
        )
        ctx = browser.contexts[0]

        # ── Step 1: Google Trends HK（跳過已發布的）─────────
        print("\n[Step 1] 抓 Google Trends HK...")
        topics = await get_gtrends_hk(browser, ctx, skip_topics=posted)
        if not topics:
            print("❌ 無法取得熱門話題（或全部已發布過）")
            return {"error": "No topics available"}
        print(f"  取得 {len(topics)} 個話題:")
        for i, t in enumerate(topics):
            print(f"  {i+1}. {t[:60]}")

        # ── Step 2: 找圖片 ───────────────────────
        chosen_topic = None
        image_path = None

        for topic in topics:
            print(f"\n[Step 2] 嘗試話題: '{topic}'")
            image_path = await search_google_image(browser, ctx, topic)
            if image_path:
                chosen_topic = topic
                print(f"  ✅ 圖片找到: {image_path}")
                break
            else:
                print(f"  ❌ 找不到相關圖片，跳過")

        if not chosen_topic or not image_path:
            print("❌ 所有話題都找不到圖片，結束")
            await browser.close()
            return {"error": "No image found"}

        # ── Step 3: Gemini 生成原創 caption ─────────
        print("\n[Step 3] Gemini 生成原創 caption...")
        raw_posts = await generate_caption(chosen_topic, ctx)
        for platform in raw_posts:
            raw_posts[platform]["image"] = image_path
        posts = raw_posts
        print(f"  FB: {posts['facebook']['text'][:60]}...")
        print(f"  IG: {posts['instagram']['text'][:60]}...")
        print(f"  Threads: {posts['threads']['text'][:60]}...")

        # ── Step 4: 關閉多餘頁面 ────────────────
        print("\n[Step 4] 整理頁面...")
        await close_extra_pages(ctx, max_pages=6)

        # ── Step 5: 發布 ──────────────────────
        print("\n[Step 5] 發布到各平台...")
        from social_mcp.post_facebook import post_facebook
        from social_mcp.post_ig import post_ig
        from social_mcp.post_threads import post_threads

        results = {}

        for platform, post_data in posts.items():
            try:
                text = post_data["text"]
                img = post_data["image"]

                if platform == "facebook":
                    result = await post_facebook(text, img)
                elif platform == "instagram":
                    result = await post_ig(text, img)
                elif platform == "threads":
                    result = await post_threads(text, img)

                print(f"  [{platform}] {result}")
                results[platform] = result
            except Exception as e:
                msg = f"❌ {e}"
                print(f"  [{platform}] {msg}")
                results[platform] = msg

        # ── Step 6: 標記 topic 為已發布 ─────────
        print("\n[Step 6] 更新已發布記錄...")
        add_posted_topic(chosen_topic)
        print(f"  ✅ '{chosen_topic}' 已加入已發布清單")

        print("\n" + "=" * 50)
        print("Workflow 完成")
        print("=" * 50)
        for platform, result in results.items():
            print(f"  {platform}: {result}")

        await browser.close()
        return results


if __name__ == "__main__":
    asyncio.run(run_workflow())
