"""科技 & AI & 金融资讯聚合器 — 从多个免费来源抓取全球动态"""
import re
import os
import time as _time
import html as _html_lib
import tempfile
import asyncio
import datetime
import xml.etree.ElementTree as ET
import httpx
from loguru import logger

_RECENCY_SECONDS = 7 * 24 * 3600  # 只取最近 7 天的内容


# ============================================================
# 工具函数
# ============================================================

_CONTENT_KW = [
    # ── AI / ML ──
    " ai ", "llm", "gpt", "machine learning", "deep learning",
    "neural", "transformer", "openai", "chatgpt", "claude",
    "gemini", "mistral", "llama", "agent", "copilot",
    "stable diffusion", "sora", "rag", "fine-tun",
    "pytorch", "tensorflow", "hugging face", "multimodal",
    "reasoning", "benchmark", "inference", "foundation model",
    "langchain", "vector db", "embedding", "text-to-image",
    "text-to-video", "speech-to-text", "whisper", "midjourney",
    "anthropic", "cohere", "together ai", "replicate",
    "cursor", "devin", "vibe coding", "bolt.new",
    # ── 泛科技 ──
    " nvidia", " amd", "intel", "arm ", "chip", "semiconductor",
    "kubernetes", "docker", "serverless", "cloudflare", "vercel",
    "browser", "typescript", "rust ", "swift ", "linux",
    "open source", "api ", "sdk", "saas", "security", "zero-day",
    "app store", "ios 18", "android 15", "macos", "vision pro",
    "tesla", "waymo", "cruise", "autonomous", "robotaxi",
    # ── 金融 / 创投 / Crypto ──
    "crypto", "bitcoin", "ethereum", "defi", "web3", "blockchain",
    "funding", "series a", "series b", "series c", "valuation",
    "ipo", "venture", "startup", "fintech", "acquisition",
    "merger", "stake", "token", "stablecoin", "nft",
    "sec ", "regulation", "doj", "antitrust", " ftc",
    "market cap", "revenue", "layoff", "hiring",
]


def _is_relevant(title: str, desc: str = "") -> bool:
    text = f"{title} {desc}".lower()
    return any(kw in text for kw in _CONTENT_KW)


def _strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html_lib.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _safe_get(obj: dict, *keys, default=""):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            obj = obj[k]
        else:
            return default
    return str(obj) if not isinstance(obj, (dict, list)) else default


# ============================================================
# 主入口
# ============================================================

async def fetch_all() -> list[dict]:
    """并发拉取全部来源，返回合并列表。
    TWITTER_TEST = True 时只抓 Twitter（用于调试推文抓取是否正常）。
    """
    TWITTER_TEST = False  # 改为 False 以启用全部资讯源（HackerNews/GitHub/RSS/Reddit/ArXiv/Twitter）
    if TWITTER_TEST:
        sources = [fetch_twitter]
    else:
        sources = [
            fetch_hackernews,
            fetch_github_trending,
            fetch_techcrunch,
            fetch_theverge,
            fetch_thedecoder,
            fetch_ycblog,
            fetch_reddit_ml,
            fetch_arxiv,
            fetch_twitter,
        ]
    results = []
    for src in sources:
        try:
            items = await src()
            results.extend(items)
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.warning(f"[techblog] {src.__name__} 失败: {e}")
    logger.info(f"[techblog] 总共抓取 {len(results)} 条资讯")
    return results


# ============================================================
# HackerNews
# ============================================================

async def fetch_hn_comments(item_id: int, max_comments: int = 4) -> list[str]:
    """Algolia HN API 拿顶部评论"""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"https://hn.algolia.com/api/v1/items/{item_id}")
            if resp.status_code != 200:
                return []
            data = resp.json()
            comments = []
            for child in (data.get("children") or []):
                text = child.get("text") or ""
                if not text or len(text) < 50:
                    continue
                text = _strip_html(text)
                comments.append(text[:500])
                if len(comments) >= max_comments:
                    break
            return comments
    except Exception as e:
        logger.warning(f"[techblog] HN评论获取失败 {item_id}: {e}")
        return []


async def fetch_hackernews() -> list[dict]:
    """HackerNews 热帖中相关内容的，补充评论（仅限 7 天内）"""
    now = _time.time()
    filtered = []
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = resp.json()[:100]  # 多取一些，因为要过滤过时的
        for nid in ids:
            resp = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{nid}.json")
            item = resp.json()
            if not item or not item.get("title"):
                continue
            # 跳过 7 天前的内容
            item_time = item.get("time", 0)
            if item_time and now - item_time > _RECENCY_SECONDS:
                continue
            title = item.get("title", "")
            if _is_relevant(title):
                filtered.append({
                    "id": nid,
                    "title": title,
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={nid}"),
                    "source": "HackerNews",
                    "score": item.get("score", 0),
                    "freshness": item_time,
                    "desc": _strip_html(item.get("text") or "")[:300],
                    "comments": [],
                })
                if len(filtered) >= 8:
                    break

    async def enrich(item: dict) -> dict:
        item["comments"] = await fetch_hn_comments(item["id"])
        return item

    enriched = await asyncio.gather(*[enrich(i) for i in filtered[:6]])
    result = list(enriched) + filtered[6:]
    logger.info(f"[techblog] HackerNews: {len(result)} 条（7天内）")
    return result


# ============================================================
# GitHub Trending
# ============================================================

async def fetch_github_trending() -> list[dict]:
    """GitHub 近一周 AI 相关高 star 仓库"""
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    items = []
    queries = ["ai", "llm", "agent", "gpt", "rag", "machine-learning"]
    async with httpx.AsyncClient(timeout=15) as client:
        for q in queries:
            url = (
                f"https://api.github.com/search/repositories"
                f"?q={q}+created:>{week_ago}&sort=stars&per_page=5"
            )
            resp = await client.get(url, headers={"Accept": "application/vnd.github.v3+json"})
            if resp.status_code != 200:
                continue
            for repo in resp.json().get("items", [])[:3]:
                items.append({
                    "title": (repo.get("description") or repo.get("name", ""))[:120],
                    "url": repo.get("html_url", ""),
                    "source": "GitHub",
                    "score": repo.get("stargazers_count", 0),
                    "freshness": _time.time(),  # 已通过 API 过滤 created:>7d，标记当前时间
                    "repo": f"{repo.get('full_name', '')} ⭐{repo.get('stargazers_count', 0)}",
                    "lang": repo.get("language") or "",
                    "desc": "",
                    "comments": [],
                })
            await asyncio.sleep(0.3)
            if len(items) >= 10:
                break
    logger.info(f"[techblog] GitHub: {len(items)} 条")
    return items


# ============================================================
# RSS 源：TechCrunch / The Verge
# ============================================================

async def _fetch_rss(name: str, feed_url: str, max_items: int) -> list[dict]:
    """通用 RSS 抓取器"""
    items = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code != 200:
                logger.warning(f"[techblog] {name} RSS 返回 {resp.status_code}")
                return items
            root = ET.fromstring(resp.text)
    except Exception as e:
        logger.warning(f"[techblog] {name} RSS 解析失败: {e}")
        return items

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    for entry in root.iter("item"):
        title = _strip_html(entry.findtext("title", ""))
        link = entry.findtext("link", "")
        desc_raw = entry.findtext("description", "")
        # 优先用 content:encoded（全文），其次用 description（摘要）
        content_encoded = entry.findtext("content:encoded", "", ns)
        desc = _strip_html(content_encoded or desc_raw)
        if not title:
            continue
        if _is_relevant(title, desc):
            # 尝试解析 pubDate，失败则用当前时间
            pub_ts = _time.time()
            pub_str = entry.findtext("pubDate", "")
            if pub_str:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_ts = parsedate_to_datetime(pub_str).timestamp()
                except Exception:
                    pass
            items.append({
                "title": title,
                "url": link,
                "source": name,
                "score": 0,
                "freshness": pub_ts,
                "desc": desc[:500],
                "comments": [],
            })
            if len(items) >= max_items:
                break

    logger.info(f"[techblog] {name}: {len(items)} 条")
    return items


async def fetch_techcrunch() -> list[dict]:
    return await _fetch_rss("TechCrunch", "https://techcrunch.com/feed/", 6)


async def fetch_theverge() -> list[dict]:
    return await _fetch_rss("TheVerge", "https://www.theverge.com/rss/index.xml", 5)


async def fetch_thedecoder() -> list[dict]:
    return await _fetch_rss("TheDecoder", "https://the-decoder.com/feed/", 5)


async def fetch_ycblog() -> list[dict]:
    return await _fetch_rss("YCBlog", "https://www.ycombinator.com/blog/feed/", 5)


# ============================================================
# Reddit r/MachineLearning
# ============================================================

async def fetch_reddit_ml() -> list[dict]:
    """Reddit r/MachineLearning 热门帖"""
    items = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.reddit.com/r/MachineLearning/hot.json?limit=20",
                headers={"User-Agent": "Mozilla/5.0 (compatible; TechBlogBot/1.0)"},
            )
            if resp.status_code != 200:
                logger.warning(f"[techblog] Reddit 返回 {resp.status_code}")
                return items
            data = resp.json()
    except Exception as e:
        logger.warning(f"[techblog] Reddit 请求失败: {e}")
        return items

    now_ts = _time.time()
    for child in _safe_get(data, "data", "children", default=[]):
        post = child.get("data", {})
        title = _strip_html(post.get("title", ""))
        if not title:
            continue
        # 跳过 7 天前的内容
        created = post.get("created_utc", 0)
        if created and now_ts - created > _RECENCY_SECONDS:
            continue
        # 跳过每日简单问题帖（[D] 标签 + 无深度的）
        if title.startswith("[D]") or title.startswith("[Discussion]"):
            if len(title) < 80:
                continue
        permalink = "https://www.reddit.com" + post.get("permalink", "")
        selftext = _strip_html(post.get("selftext", ""))
        ups = post.get("ups", 0)

        items.append({
            "title": title,
            "url": post.get("url", permalink),
            "source": "Reddit r/ML",
            "score": ups,
            "freshness": created,
            "desc": selftext[:400],
            "comments": [],  # 评论在另一个接口，暂时不拉
        })
        if len(items) >= 6:
            break

    logger.info(f"[techblog] Reddit r/ML: {len(items)} 条")
    return items


# ============================================================
# ArXiv 最新 AI 论文
# ============================================================

async def fetch_arxiv() -> list[dict]:
    """ArXiv cs.AI 最新论文"""
    items = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "http://export.arxiv.org/api/query"
                "?search_query=cat:cs.AI&sortBy=submittedDate&start=0&max_results=10",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                logger.warning(f"[techblog] ArXiv 返回 {resp.status_code}")
                return items
            root = ET.fromstring(resp.text)
    except Exception as e:
        logger.warning(f"[techblog] ArXiv 请求失败: {e}")
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("atom:entry", ns):
        title = _strip_html(entry.findtext("atom:title", "", ns))
        summary = _strip_html(entry.findtext("atom:summary", "", ns))
        arxiv_id = ""
        for link in entry.findall("atom:link", ns):
            if link.get("rel") == "alternate":
                arxiv_id = link.get("href", "")
                break
        if not title:
            continue
        # 解析发布时间
        pub_ts = _time.time()
        pub_str = entry.findtext("atom:published", "", ns)
        if pub_str:
            try:
                pub_ts = datetime.datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
        items.append({
            "title": title,
            "url": arxiv_id,
            "source": "ArXiv",
            "score": 0,
            "freshness": pub_ts,
            "desc": summary[:500],
            "comments": [],
        })
        if len(items) >= 5:
            break

    logger.info(f"[techblog] ArXiv: {len(items)} 条")
    return items


# ============================================================
# Twitter/X（Playwright 登录后抓取）
# ============================================================

async def fetch_twitter() -> list[dict]:
    """从关注的 AI/科技推号抓取最近推文"""
    try:
        from app.services.techblog.twitter import fetch_tweets, load_cookies
        items = await fetch_tweets()
        if not items:
            cookies = load_cookies()
            if not cookies:
                logger.warning("[techblog] ⚠️ Twitter Cookie 文件不存在，请先执行: python scripts/nurture.py twitter-login")
            else:
                logger.warning("[techblog] ⚠️ Twitter Cookie 可能已过期，抓取结果为空，请执行: python scripts/nurture.py twitter-login")
        logger.info(f"[techblog] Twitter: {len(items)} 条")
        return items
    except Exception as e:
        logger.warning(f"[techblog] Twitter 抓取失败: {e}")
        return []


# ============================================================
# 来源截图
# ============================================================

async def screenshot_source_page(item: dict) -> str | None:
    """用 Playwright 截取原始来源页面，返回临时 PNG 路径"""
    from playwright.async_api import async_playwright

    source = item.get("source", "")
    item_id = item.get("id")
    article_url = item.get("url", "")

    # 截图 URL 选择逻辑：
    # - HN 链接型帖子：优先截外部原文（有视觉设计），纯文本帖才回 HN 讨论页
    # - HN 讨论页 URL 格式: news.ycombinator.com/item?id=xxx
    # - 其他来源：直接截原文链接
    if source.startswith("X @"):
        target_url = article_url  # 推文原链接，带互动信息
    elif source == "HackerNews" and article_url and "news.ycombinator.com" not in article_url:
        target_url = article_url  # 外部原文
    elif source == "HackerNews" and item_id:
        target_url = f"https://news.ycombinator.com/item?id={item_id}"  # 纯文本帖回 HN
    elif article_url:
        target_url = article_url
    else:
        return None

    title = f"{source}: {item.get('title', '')[:40]}"

    # Twitter 截图需要 Cookie
    twitter_cookies = None
    if source.startswith("X @"):
        try:
            from app.services.techblog.twitter import load_cookies
            twitter_cookies = load_cookies()
        except Exception:
            pass

    try:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="techblog_")
        os.close(fd)

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            is_twitter = source.startswith("X @")
            # 手机视口截推文主体（不滚动避免触发 App 弹窗）
            viewport = {"width": 430, "height": 932} if is_twitter else {"width": 1200, "height": 900}
            context = await browser.new_context(
                viewport=viewport,
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
                    if is_twitter else
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()
            if twitter_cookies:
                await context.add_cookies(twitter_cookies)
            wait_mode = "domcontentloaded" if is_twitter else "networkidle"
            try:
                await page.goto(target_url, wait_until=wait_mode, timeout=30000)
            except Exception:
                pass
            await asyncio.sleep(4 if is_twitter else 2)

            if is_twitter:
                # 尝试点击弹窗右上角关闭按钮（aria-label="Close" 或 X 图标）
                closed = await page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll(
                            '[aria-label="Close"], [data-testid="appBarClose"], ' +
                            'button[aria-label*="close" i], [role="button"][aria-label*="Close" i]'
                        );
                        for (const btn of btns) {
                            if (btn.offsetParent !== null) {
                                btn.click();
                                return true;
                            }
                        }
                        // 备选：找弹窗里的任意按钮/链接元素，第一个通常是关闭
                        const banner = document.querySelector(
                            '[data-testid="appCtaBanner"], [class*="banner"], [class*="Banner"]'
                        );
                        if (banner && banner.offsetParent) {
                            const closeBtn = banner.querySelector('div[role="button"], a[role="link"], button');
                            if (closeBtn) { closeBtn.click(); return true; }
                        }
                        return false;
                    }
                """)
                if not closed:
                    # JS 找不到关闭按钮 → 尝试按 Escape 键
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
                else:
                    logger.info(f"[techblog] 已关闭 Twitter App 弹窗")
                    await asyncio.sleep(1)

            if not is_twitter:
                await page.evaluate("window.scrollBy(0, 300)")
                await asyncio.sleep(1)
            await page.screenshot(path=path, full_page=False)
            await browser.close()

        logger.info(f"[techblog] 截图完成: {title}")
        return path

    except Exception as e:
        logger.warning(f"[techblog] 截图失败 {title}: {e}")
        return None
