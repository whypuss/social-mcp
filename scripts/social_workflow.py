"""
social_workflow.py — 多來源熱門話題 → 圖文 → FB / Threads

支援來源:
  gtrends_hk  Google Trends 香港 (https://trends.google.com.tw/trending?geo=HK&pli=1)
  weibo       微博熱搜 (https://s.weibo.com/top/summary?cate=realtimehot)
  gtrends_us  Google Trends 美國 (https://trends.google.com.tw/trending?geo=US)

防重複邏輯:
- 每個來源有獨立的 posted_topics_{source}.json
- 每個 topic 只發一次

流程（單一 CDP 連接）:
1. 根據 source 抓取熱門話題（跳過已發布的）
2. Google Images 搜尋 topic → base64 圖片 → 存檔
3. Gemini 生成約 100 字原創內容 + 5 個關鍵詞（繁體中文）
4. 整理頁面（保持 ≤ 6 個）
5. 發布到 Facebook 和 Threads
6. 更新 posted_topics_{source}.json

用法: uv run python scripts/social_workflow.py gtrends_hk
      uv run python scripts/social_workflow.py weibo
      uv run python scripts/social_workflow.py gtrends_us
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

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

CDP_PORT = 9333
OUTPUT_DIR = Path.home() / ".hermes/cron/output"
MAX_POSTED = 12

SOURCES = {
    "gtrends_hk": {
        "name": "Google Trends 香港",
        "url": "https://trends.google.com.tw/trending?geo=HK&pli=1",
        "row_selector": "tr.enOdEe-wZVHld-xMbwt",
        "posted_file": OUTPUT_DIR / "posted_topics_gtrends_hk.json",
    },
    "weibo": {
        "name": "微博熱搜",
        "url": "https://s.weibo.com/top/summary?cate=realtimehot",
        "posted_file": OUTPUT_DIR / "posted_topics_weibo.json",
    },
    "gtrends_us": {
        "name": "Google Trends 美國",
        "url": "https://trends.google.com.tw/trending?geo=US&pli=1",
        "row_selector": "tr.enOdEe-wZVHld-xMbwt",
        "posted_file": OUTPUT_DIR / "posted_topics_gtrends_us.json",
    },
}


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
                tabs = json.loads(r.read())
                return p, tabs
        except Exception:
            pass
    return None, []


# ============================================================================
# 防重複：per-source posted_topics 管理
# ============================================================================

def load_posted_topics(source: str) -> list:
    f = SOURCES[source]["posted_file"]
    if not f.exists():
        return []
    try:
        with open(f, "r", encoding="utf-8") as fp:
            data = json.load(fp)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_posted_topics(source: str, topics: list):
    f = SOURCES[source]["posted_file"]
    f.parent.mkdir(parents=True, exist_ok=True)
    trimmed = topics[-MAX_POSTED:]
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(trimmed, fp, ensure_ascii=False, indent=2)


def add_posted_topic(source: str, topic: str):
    topics = load_posted_topics(source)
    topics = [t for t in topics if t != topic]
    topics.append(topic)
    save_posted_topics(source, topics)


# ============================================================================
# Step 1: 熱門話題抓取（各來源）
# ============================================================================

ABSTRACT_KEYWORDS = [
    "1994", "1995", "1996", "1997", "1998", "1999",
    "2000", "2001", "2002", "2003", "2004",
    "series", "episode", "ep1", "ep2", "trailer",
    "awards", "fans", "fammeet",
]


async def _ensure_trends_page(ctx, url: str):
    """找已開的 trends 頁，或開新頁"""
    for pg in ctx.pages:
        if "trends.google" in pg.url.lower():
            return pg
    pg = await ctx.new_page()
    await pg.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    return pg


async def _get_gtrends_topics(browser, ctx, source: str) -> list:
    """抓 Google Trends HK 或 US 的話題"""
    cfg = SOURCES[source]
    skip_topics = load_posted_topics(source)

    gt_page = await _ensure_trends_page(ctx, cfg["url"])
    await gt_page.bring_to_front()
    await asyncio.sleep(2)

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

    return _clean_topics(topics_raw, skip_topics)


async def _get_weibo_topics(browser, ctx, source: str) -> list:
    """抓微博熱搜話題"""
    skip_topics = load_posted_topics(source)

    wb_page = None
    for pg in ctx.pages:
        if "weibo.com" in pg.url.lower() and "s.weibo.com" in pg.url.lower():
            wb_page = pg
            break

    if not wb_page:
        wb_page = await ctx.new_page()

    await wb_page.goto(
        "https://s.weibo.com/top/summary?cate=realtimehot",
        wait_until="domcontentloaded",
        timeout=30000
    )
    await asyncio.sleep(4)

    topics_raw = await wb_page.evaluate("""() => {
        // 微博熱搜榜，選擇 <td> 第一個 <a> 的文字
        const tds = document.querySelectorAll('td');
        const topics = [];
        for (const td of tds) {
            const a = td.querySelector('a');
            if (!a) continue;
            const t = (a.innerText || '').trim();
            // 跳過熱搜排名數字（1, 2, 3...）和熱搜/榜之類的導航
            if (/^\\d+$/.test(t)) continue;
            if (t.length >= 2 && t.length <= 30) {
                topics.push(t);
            }
        }
        return [...new Set(topics)];
    }""")

    return _clean_topics(topics_raw, skip_topics)


def _clean_topics(topics_raw: list, skip_topics: list) -> list:
    """過濾：去空格、去重、去 abstract、去已發布"""
    cleaned = []
    seen = set()
    skip_set = set(skip_topics)
    for t in topics_raw:
        t_clean = re.sub(r'\s+', '', t).strip()
        if not t_clean or t_clean in seen:
            continue
        seen.add(t_clean)
        lower = t_clean.lower()
        if t_clean in skip_set:
            continue
        if any(kw in lower for kw in ABSTRACT_KEYWORDS if len(kw) > 3):
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
# Gemini caption 生成
# ============================================================================

GEMINI_INPUT = 'div[aria-label="請輸入 Gemini 提示詞"]'


async def _find_gemini_page(ctx):
    for pg in ctx.pages:
        if "gemini.google.com" in pg.url:
            return pg
    g_page = await ctx.new_page()
    await g_page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(4)
    return g_page


async def call_gemini(page, prompt: str, timeout=90) -> str:
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

        if len(response['text']) > 80 and elapsed > 25:
            return response['text'][:500]

        if response['status'] == 'done' and len(response['text']) > 5:
            return response['text'][:500]

    response = await page.evaluate("""() => {
        const all = Array.from(document.querySelectorAll('.model-response-text'));
        if (all.length === 0) return '';
        return (all[all.length - 1].innerText || '').trim();
    }""")
    return response[:500] if response else "[Gemini timeout]"


async def generate_caption(topic: str, source: str, ctx) -> dict:
    """
    用 Gemini 生成原創內容（繁體中文）：
    - 約 100 字正文
    - 5 個關鍵詞
    - 格式：正文\n\n關鍵詞：#xxx #xxx #xxx #xxx #xxx
    """
    gemini_page = await _find_gemini_page(ctx)
    await gemini_page.bring_to_front()

    source_name = SOURCES[source]["name"]

    prompt = (
        f"你是一個香港社交媒體內容創作專家。\n"
        f"請為以下話題創作一篇 Facebook / Threads 帖子：\n\n"
        f"話題：「{topic}」（來源：{source_name}）\n\n"
        f"要求：\n"
        f"1. 繁體中文（台灣/香港正體字）\n"
        f"2. 約 100 字正文，口語化，像真人在分享\n"
        f"3. 正文結尾自選位置加 5 個 hashtag（#關鍵詞），用繁體\n"
        f"4. 不要加 emoji\n"
        f"5. 嚴格在 120 字以內（正文+關鍵詞合計）\n"
        f"6. 直接輸出，不要前置說明\n"
        f"7. 格式：正文內容（可含 hashtags）\n"
        f"   關鍵詞：#關鍵詞1 #關鍵詞2 #關鍵詞3 #關鍵詞4 #關鍵詞5"
    )

    log.info(f"[Gemini] 生成 caption for '{topic}' ({source_name})...")
    response = await call_gemini(gemini_page, prompt)
    log.info(f"[Gemini] 回應 {len(response)} chars: {response[:60]}...")

    # 清理 Gemini 輸出前綴
    clean = re.sub(r"^Gemini[^\n]*\n*", "", response).strip()

    # 從回應中拆分正文和關鍵詞
    body_text = clean
    keywords_text = ""

    # 如果包含「關鍵詞：」或「关键词：」，拆分
    kw_match = re.search(r'[關关]鍵詞[：:]\s*(.+)', clean, re.UNICODE)
    if kw_match:
        body_text = clean[:kw_match.start()].strip()
        keywords_text = kw_match.group(1).strip()
        # 確保關鍵詞格式正確
        if not keywords_text.startswith('#'):
            hashtags = re.findall(r'#[\w\u4e00-\u9fff]+', keywords_text)
            keywords_text = " ".join(hashtags) if hashtags else keywords_text

    # 合併為完整 caption（正文在前，關鍵詞在最後）
    if keywords_text:
        full_caption = f"{body_text}\n\n{keywords_text}"
    else:
        full_caption = body_text

    # Threads 截斷至 140 字，FB 留 500
    return {
        "facebook": {"text": full_caption[:500]},
        "threads": {"text": full_caption[:140]},
    }


# ============================================================================
# Step 3: 頁面整理
# ============================================================================

async def close_extra_pages(ctx, max_pages=6):
    current_count = len(ctx.pages)
    if current_count <= max_pages:
        log.info(f"[PageMgr] {current_count} pages, no need to close")
        return

    priority_close = []
    for pg in list(ctx.pages):
        u = pg.url
        if "tbm=isch" in u or "github.com" in u.lower():
            priority_close.append(pg)

    total_to_close = current_count - max_pages
    for pg in priority_close[:total_to_close]:
        await pg.close()
        log.info(f"[PageMgr] Closed: {pg.url[:50]}")


# ============================================================================
# Main workflow
# ============================================================================

async def run_workflow(source: str):
    cfg = SOURCES.get(source)
    if not cfg:
        return {"error": f"未知來源: {source}"}

    print("=" * 60)
    print(f"Social Workflow ({cfg['name']})")
    print("=" * 60)

    posted = load_posted_topics(source)
    print(f"\n[Init] 已發布 topic ({len(posted)}): {posted[-5:]}")

    async with async_playwright() as p:
        port, _ = _get_cdp_browser()
        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port or 9333}", timeout=20000
        )
        ctx = browser.contexts[0]

        # ── Step 1: 抓話題 ─────────────────────────────────
        print(f"\n[Step 1] 抓取 {cfg['name']} 話題...")
        if source == "weibo":
            topics = await _get_weibo_topics(browser, ctx, source)
        else:
            topics = await _get_gtrends_topics(browser, ctx, source)

        if not topics:
            print("❌ 無法取得話題（或全部已發布過）")
            await browser.close()
            return {"error": "No topics available"}

        print(f"  取得 {len(topics)} 個話題:")
        for i, t in enumerate(topics):
            print(f"  {i+1}. {t[:60]}")

        # ── Step 2: 找圖片 ─────────────────────────────────
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
                print(f"  ❌ 找不到圖片，跳過")

        if not chosen_topic or not image_path:
            print("❌ 所有話題都找不到圖片，結束")
            await browser.close()
            return {"error": "No image found"}

        # ── Step 3: Gemini 生成內容 ──────────────────────
        print("\n[Step 3] Gemini 生成原創內容...")
        posts = await generate_caption(chosen_topic, source, ctx)
        for platform in posts:
            posts[platform]["image"] = image_path
        print(f"  FB:   {posts['facebook']['text'][:60]}...")
        print(f"  Threads: {posts['threads']['text'][:60]}...")

        # ── Step 4: 整理頁面 ──────────────────────────────
        print("\n[Step 4] 整理頁面...")
        await close_extra_pages(ctx, max_pages=6)

        # ── Step 5: 發布 ────────────────────────────────
        print("\n[Step 5] 發布到 FB 和 Threads...")
        from social_mcp.post_facebook import post_facebook
        from social_mcp.post_threads import post_threads

        results = {}

        # 先發 Threads
        try:
            result = await post_threads(
                posts["threads"]["text"],
                posts["threads"]["image"]
            )
            print(f"  [Threads] {result}")
            results["threads"] = result
        except Exception as e:
            msg = f"❌ {e}"
            print(f"  [Threads] {msg}")
            results["threads"] = msg

        # 再發 Facebook
        try:
            result = await post_facebook(
                posts["facebook"]["text"],
                posts["facebook"]["image"]
            )
            print(f"  [Facebook] {result}")
            results["facebook"] = result
        except Exception as e:
            msg = f"❌ {e}"
            print(f"  [Facebook] {msg}")
            results["facebook"] = msg

        # ── Step 6: 標記 topic 為已發布 ──────────────────
        print(f"\n[Step 6] 更新已發布記錄...")
        add_posted_topic(source, chosen_topic)
        print(f"  ✅ '{chosen_topic}' 已加入 {source} 已發布清單")

        print("\n" + "=" * 60)
        print("Workflow 完成")
        print("=" * 60)
        for platform, result in results.items():
            print(f"  {platform}: {result}")

        await browser.close()
        return results


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "gtrends_hk"
    asyncio.run(run_workflow(source))
