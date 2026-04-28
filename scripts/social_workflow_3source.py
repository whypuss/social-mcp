"""
social_workflow_3source.py — 三來源輪流自動發文

每 3 小時一個循環：
  :00 → 來源 1（Google Trends HK）
  :45 → 來源 2（微博熱搜）
  :90(:30) → 來源 3（Google Trends US）

流程：
1. 根據 source 引數抓對應來源
2. 防重複過濾（posted_topics.json）
3. Google Images 找圖
4. Gemini 生成 ~100字正文 + 5個關鍵詞（繁體中文）
5. 發布到 FB → IG → Threads
6. 記錄已發布 topic

用法：
  uv run python scripts/social_workflow_3source.py 1   # Google Trends HK
  uv run python scripts/social_workflow_3source.py 2   # 微博熱搜
  uv run python scripts/social_workflow_3source.py 3   # Google Trends US

Cron（hermes-skills）：
  0,45,90 * * * *  → 每 45 分鐘觸發一次
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path
import requests
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)

CDP_PORT = 9222  # 平常的 Chrome（有 Google 登入，不會 captcha）
POSTED_TOPICS_FILE = Path.home() / ".hermes/cron/output/posted_topics_3source.json"
MAX_POSTED = 30  # 30 個足够 36 小時（每 45 分鐘一個）

# Gemini selector
GEMINI_INPUT = 'div[aria-label="請輸入 Gemini 提示詞"]'
SOURCE_NAMES = {1: "Google Trends HK", 2: "微博熱搜", 3: "Google Trends US"}


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
# 防重複： posted_topics 管理
# ============================================================================

def load_posted_topics() -> list:
    if not POSTED_TOPICS_FILE.exists():
        return []
    try:
        with open(POSTED_TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_posted_topics(topics: list):
    POSTED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = topics[-MAX_POSTED:]
    with open(POSTED_TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def add_posted_topic(topic: str):
    topics = load_posted_topics()
    topics = [t for t in topics if t != topic]
    topics.append(topic)
    save_posted_topics(topics)


# ============================================================================
# 來源 1：Google Trends HK
# ============================================================================

async def fetch_gtrends_hk(ctx, skip_topics: list) -> list:
    abstract_kw = [
        "1994", "1995", "1996", "1997", "1998", "1999",
        "2000", "2001", "2002", "2003", "2004",
        "series", "episode", "ep1", "ep2", "trailer",
        "awards", "fans", "fammeet", "replay", "m3u8"
    ]

    gt_page = None
    for pg in ctx.pages:
        if "trends.google" in pg.url.lower():
            gt_page = pg
            break
    if not gt_page:
        gt_page = await ctx.new_page()

    await gt_page.bring_to_front()
    await gt_page.goto(
        "https://trends.google.com.tw/trending?geo=HK&pli=1",
        wait_until="domcontentloaded", timeout=30000
    )
    await asyncio.sleep(5)

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

    skip_set = set(skip_topics)
    cleaned = []
    seen = set()
    for t in topics_raw:
        t_clean = re.sub(r'\s+', '', t).strip()
        if not t_clean or t_clean in seen:
            continue
        seen.add(t_clean)
        lower = t_clean.lower()
        if t_clean in skip_set:
            continue
        if any(kw in lower for kw in abstract_kw if len(kw) > 3):
            continue
        cleaned.append(t_clean)

    return cleaned[:12]


# ============================================================================
# 來源 2：微博熱搜
# ============================================================================

async def fetch_weibo(ctx, skip_topics: list) -> list:
    wb_page = None
    for pg in ctx.pages:
        if "weibo.com" in pg.url.lower():
            wb_page = pg
            break
    if not wb_page:
        wb_page = await ctx.new_page()

    await wb_page.bring_to_front()
    await wb_page.goto(
        "https://s.weibo.com/top/summary?cate=realtimehot",
        wait_until="domcontentloaded", timeout=30000
    )
    await asyncio.sleep(4)

    # 滾動載入
    for _ in range(4):
        await wb_page.evaluate("window.scrollBy(0, 400)")
        await asyncio.sleep(0.5)

    topics_raw = await wb_page.evaluate("""() => {
        const items = document.querySelectorAll('td.td-02');
        const topics = [];
        for (const item of items) {
            const a = item.querySelector('a');
            if (!a) continue;
            const text = (a.innerText || '').trim();
            // 跳過數字排名，只取標題
            const cleaned = text.replace(/^\\d+/, '').trim();
            // 只取有數字排序的話題（置頂/熱門/推薦等無數字的都跳過）
            if (!/^\d/.test(text)) continue;
            if (cleaned.includes('置顶') || cleaned.includes('置頂') ||
                cleaned.includes('热') || cleaned.includes('熱') ||
                cleaned.includes('荐') || cleaned.includes('薦')) continue;
            if (cleaned.length >= 2 && cleaned.length <= 30) {
                topics.push(cleaned);
            }
        }
        return [...new Set(topics)];
    }""")

    skip_set = set(skip_topics)
    cleaned = []
    seen = set()
    for t in topics_raw:
        t_clean = re.sub(r'\s+', '', t).strip()
        if not t_clean or t_clean in seen:
            continue
        seen.add(t_clean)
        if t_clean in skip_set:
            continue
        cleaned.append(t_clean)

    return cleaned[:12]


# ============================================================================
# 來源 3：Google Trends US
# ============================================================================

async def fetch_gtrends_us(ctx, skip_topics: list) -> list:
    abstract_kw = [
        "1994", "1995", "1996", "1997", "1998", "1999",
        "2000", "2001", "2002", "2003", "2004",
        "series", "episode", "trailer", "awards"
    ]

    gt_page = None
    for pg in ctx.pages:
        if "trends.google" in pg.url.lower():
            gt_page = pg
            break
    if not gt_page:
        gt_page = await ctx.new_page()

    await gt_page.bring_to_front()
    await gt_page.goto(
        "https://trends.google.com.tw/trending?geo=US&pli=1",
        wait_until="domcontentloaded", timeout=30000
    )
    await asyncio.sleep(5)

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

    skip_set = set(skip_topics)
    cleaned = []
    seen = set()
    for t in topics_raw:
        t_clean = re.sub(r'\s+', '', t).strip()
        if not t_clean or t_clean in seen:
            continue
        seen.add(t_clean)
        lower = t_clean.lower()
        if t_clean in skip_set:
            continue
        if any(kw in lower for kw in abstract_kw if len(kw) > 3):
            continue
        cleaned.append(t_clean)

    return cleaned[:12]


# ============================================================================
# Google Images
# ============================================================================

async def search_google_image(ctx, topic: str) -> str:
    """用 Bing Images 搜尋 topic，回傳圖片路徑（Google 已被 captcha 封鎖，改用 Bing）"""
    search_q = urllib.parse.quote(topic[:50])

    b_page = None
    for pg in ctx.pages:
        if "bing.com" in pg.url.lower() and "/images/" in pg.url.lower():
            b_page = pg
            break
    if not b_page:
        b_page = await ctx.new_page()

    await b_page.bring_to_front()
    await b_page.goto(
        f"https://www.bing.com/images/search?q={search_q}&first=1&cw=1280&ch=720",
        wait_until="domcontentloaded", timeout=30000
    )
    await asyncio.sleep(3)

    # Bing 的圖片 URL 在 link 的 mediaurl 參數裡
    media_urls = await b_page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a[href*="mediaurl"]'));
        const urls = [];
        for (const link of links) {
            const href = link.href;
            try {
                const params = new URLSearchParams(href.split('?')[1] || '');
                const mediaUrl = params.get('mediaurl');
                if (mediaUrl && mediaUrl.startsWith('http')) {
                    // 解碼 URL
                    const decoded = decodeURIComponent(mediaUrl);
                    urls.push(decoded);
                }
            } catch(e) {}
            if (urls.length >= 8) break;
        }
        return urls;
    }""")

    log.info(f"[Images] Bing 找到 {len(media_urls)} 個 URL: {media_urls[:2]}")

    for img_url in media_urls:
        try:
            # 用 requests（自帶 redirect、速度快）
            r = requests.get(img_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
            if r.status_code == 200:
                content_type = r.headers.get("content-type", "")
                ext = "jpg"
                if "webp" in content_type.lower():
                    ext = "webp"
                elif "png" in content_type.lower():
                    ext = "png"
                img_bytes = r.content
                if len(img_bytes) > 5000:
                    out_path = f"/tmp/social3_{int(time.time())}_{random.randint(100,999)}.{ext}"
                    with open(out_path, "wb") as f:
                        f.write(img_bytes)
                    log.info(f"[Images] 下載成功 {len(img_bytes)} bytes: {out_path}")
                    return out_path
        except Exception as e:
            log.warning(f"[Images] 下載失敗 {img_url[:60]}: {e}")
            continue

    log.warning(f"[Images] 找不到圖片: '{topic}'")
    return None


# ============================================================================
# Gemini caption 生成
# ============================================================================

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
    await asyncio.sleep(1.0)  # 等 fill 完全生效，DOM 穩定
    await inp.type(prompt, delay=60)  # 60ms/字，確保每個字都輸入到位
    await asyncio.sleep(1.5)  # 等 React state 更新，確保文字完全進入 input
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


def _to_traditional(text: str) -> str:
    """簡 → 繁 常見替換（覆蓋社交媒體常用詞）"""
    pairs = [
        "趋势", "熱門話題", "熱門", "熱搜", "挑战", "挑戰",
        "视频", "視頻", "电影", "電影", "发布", "發布",
        "话题", "話題", "时间", "時間", "关注", "關注",
        "推荐", "推薦", "最新", "最新", "今日", "今日",
        "明星", "明星", "歌手", "歌手", "演员", "演員",
        "比赛", "比賽", "球队", "球隊", "选手", "選手",
        "直播", "直播", "投票", "投票", "排名", "排名",
        "合作", "合作", "活动", "活動", "演唱会", "演唱會",
        "新歌", "新歌", "上映", "上映", "首播", "首播",
        "大火", "大火", "爆火", "爆火", "刷屏", "刷屏",
        "来袭", "來襲", "来袭", "來襲", "上线", "上線",
        "上线", "上線", "公开", "公開", "出道", "出道",
    ]
    result = text
    # 兩兩處理（詞組優先）
    i = 0
    while i < len(pairs) - 1:
        result = result.replace(pairs[i], pairs[i + 1])
        i += 2
    return result


async def generate_caption(topic: str, source: int, ctx) -> dict:
    """
    Gemini 生成約 100 字正文 + 5 個關鍵詞（繁體中文）
    回傳 {fb_text, ig_text, threads_text, keywords}
    """
    gemini_page = await _find_gemini_page(ctx)
    await gemini_page.bring_to_front()

    source_label = SOURCE_NAMES.get(source, f"來源{source}")

    prompt = f"""你是一個香港社交媒體內容創作專家。

請為以下話題創作一篇 Facebook / Instagram / Threads 帖子。

話題：「{topic}」（來源：{source_label}）

請嚴格按照以下格式輸出，不要加任何前置說明：

【正文】（約 100 字，繁體中文，廣東話口語，客觀資訊類風格，例如「据悉」「有消息指」「近日」之類，不要用「我」「我們」「我睇到」「我去咗」等第一人稱，純資訊分享，不要加 emoji）

【關鍵詞】（5個，用 # 開頭，繁體中文，例如：#香港 #話題 #電影 #推薦 #熱門）

直接輸出，不要加「以下是」等文字。"""

    log.info(f"[Gemini] 生成 caption for '{topic}' ({source_label})...")
    response = await call_gemini(gemini_page, prompt)
    log.info(f"[Gemini] 回應 {len(response)} chars: {response[:80]}...")

    # 清理 Gemini 前綴（直接刪掉無效正則，直接去掉【正文】開頭）
    clean = response.strip()
    # 移除可能有的 "【正文】" 開頭標記
    if clean.startswith("【正文】"):
        clean = clean[len("【正文】"):].strip()

    # 提取正文和關鍵詞
    body_text = ""
    keywords_text = ""

    if "【正文】" in clean and "【關鍵詞】" in clean:
        parts = clean.split("【關鍵詞】")
        body_text = parts[0].replace("【正文】", "").strip()
        keywords_text = parts[1].strip() if len(parts) > 1 else ""
        # 如果正文為空但關鍵詞有內容，用標題當正文（Gemini 正文未生成）
        if not body_text and keywords_text:
            body_text = f"針對「{topic}」的熱門討論引發關注。"
    else:
        # fallback：整段當正文
        body_text = clean

    # 確保繁體
    body_text = _to_traditional(body_text)
    keywords_text = _to_traditional(keywords_text)

    # 組合完整 caption（關鍵詞全部加上）
    def make_caption(body, keywords, max_len):
        full = f"{body}\n\n{keywords}" if keywords else body
        return full[:max_len]

    return {
        "body": body_text,
        "keywords": keywords_text,
        "fb": make_caption(body_text, keywords_text, 280),
        "ig": make_caption(body_text, keywords_text, 200),
        "threads": make_caption(body_text, keywords_text, 500),
    }


# ============================================================================
# 頁面管理（保持 ≤ 6 個）
# ============================================================================

async def close_extra_pages(ctx, max_pages=6):
    current_count = len(ctx.pages)
    if current_count <= max_pages:
        log.info(f"[Pages] {current_count} pages，無需關閉")
        return

    to_close = []
    for pg in list(ctx.pages):
        u = pg.url
        if "tbm=isch" in u or "github.com" in u.lower():
            to_close.append(pg)

    total = current_count - max_pages
    for pg in to_close[:total]:
        await pg.close()
        log.info(f"[Pages] 關閉：{pg.url[:50]}")

    log.info(f"[Pages] 剩餘 {len(ctx.pages)} 個")


# ============================================================================
# 主流程
# ============================================================================

async def run_workflow(source: int):
    print("=" * 60)
    print(f"Social Workflow 3-Source — 來源 {source}: {SOURCE_NAMES.get(source, '未知')}")
    print("=" * 60)

    posted = load_posted_topics()
    print(f"\n[Init] 已發布 topic ({len(posted)}): {posted[-5:]}")

    async with async_playwright() as p:
        port, _ = _get_cdp_browser()
        if not port:
            print("❌ 無法連接 CDP Chromium（port 9333 或 9222）")
            return {"error": "CDP not available"}

        browser = await p.chromium.connect_over_cdp(
            f"http://localhost:{port}", timeout=20000
        )
        ctx = browser.contexts[0]

        # ── Step 1: 抓來源 ─────────────────────────────────────
        source_name = SOURCE_NAMES.get(source, f"來源{source}")
        print(f"\n[Step 1] 抓 {source_name}...")

        if source == 1:
            topics = await fetch_gtrends_hk(ctx, skip_topics=posted)
        elif source == 2:
            topics = await fetch_weibo(ctx, skip_topics=posted)
        elif source == 3:
            topics = await fetch_gtrends_us(ctx, skip_topics=posted)
        else:
            print(f"❌ 未知來源：{source}")
            return {"error": f"Unknown source {source}"}

        if not topics:
            print(f"❌ 無法取得 {source_name} 話題（或全部已發布過）")
            await browser.close()
            return {"error": "No topics available"}

        print(f"  取得 {len(topics)} 個話題：{topics[:5]}...")

        # ── Step 2: 找圖片 ─────────────────────────────────────
        chosen_topic = None
        image_path = None

        for topic in topics:
            print(f"\n[Step 2] 嘗試話題: '{topic}'")
            image_path = await search_google_image(ctx, topic)
            if image_path:
                chosen_topic = topic
                print(f"  ✅ 圖片找到: {image_path}")
                break
            else:
                print(f"  ❌ 找不到圖片，跳過")

        if not chosen_topic or not image_path:
            print("❌ 所有話題都找不到圖片")
            await browser.close()
            return {"error": "No image found"}

        # ── Step 3: Gemini 生成 caption ──────────────────────
        print(f"\n[Step 3] Gemini 生成 caption...")
        caption_data = await generate_caption(chosen_topic, source, ctx)

        print(f"  正文: {caption_data['body'][:60]}...")
        print(f"  關鍵詞: {caption_data['keywords'][:60]}...")
        print(f"  FB: {caption_data['fb'][:60]}...")

        # ── Step 4: 整理頁面 ───────────────────────────────────
        print(f"\n[Step 4] 整理頁面（保持 ≤ 6 個）...")
        await close_extra_pages(ctx, max_pages=6)

        # ── Step 5: 發布 ──────────────────────────────────────
        print(f"\n[Step 5] 發布到 FB → Threads...")
        from social_mcp.post_facebook import post_facebook
        from social_mcp.post_threads import post_threads

        # 確保 Threads tab 已打開（post_threads 需要現成的 tab 否則會失敗）
        threads_tab = None
        for pg in ctx.pages:
            if "threads.net" in pg.url and "settings" not in pg.url:
                threads_tab = pg
                break
        if not threads_tab:
            print("  [Threads] 開新標籤...")
            threads_tab = await ctx.new_page()
            await threads_tab.goto("https://www.threads.net/", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)

        results = {}

        platforms = [
            ("facebook", caption_data["fb"], post_facebook),
            ("threads", caption_data["threads"], post_threads),
        ]

        for platform_name, text, post_fn in platforms:
            try:
                result = await post_fn(text, image_path)
                print(f"  [{platform_name}] {result}")
                results[platform_name] = result
                # 每個平台間隔一下（擬人）
                await asyncio.sleep(random.uniform(2, 4))
            except Exception as e:
                msg = f"❌ {e}"
                print(f"  [{platform_name}] {msg}")
                results[platform_name] = msg

        # ── Step 6: 記錄已發布 ─────────────────────────────────
        print(f"\n[Step 6] 更新已發布記錄...")
        add_posted_topic(chosen_topic)
        print(f"  ✅ '{chosen_topic}' 已記錄")

        print(f"\n{'=' * 60}")
        print(f"來源 {source}（{source_name}）完成")
        print(f"{'=' * 60}")
        for plat, res in results.items():
            print(f"  {plat}: {res}")

        try:
            await browser.close()
        except Exception as e:
            # CDP mode: browser is external, close is optional
            log.info(f"[Browser] close skipped (CDP mode): {e}")
        return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="3-Source Social Workflow")
    parser.add_argument("source", type=int, choices=[1, 2, 3],
                        help="來源：1=Google Trends HK, 2=微博熱搜, 3=Google Trends US")
    args = parser.parse_args()

    result = asyncio.run(run_workflow(args.source))
    sys.exit(0 if "error" not in result else 1)
