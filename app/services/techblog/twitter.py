"""Twitter/X 资讯抓取 — Playwright 登录 + 抓指定账号推文"""
import json
import os
import re
import asyncio
from loguru import logger

# Cookie 持久化文件
_COOKIE_FILE = os.path.join(os.path.dirname(__file__), "twitter_cookies.json")

# 关注的 Twitter 账号（可按需增删）
AI_ACCOUNTS = [
    # ── 科技巨头 / 企业家 ──
    "elonmusk", "jeffbezos", "satyanadella", "tim_cook",
    "sundarpichai", "realDonaldTrump",
    # ── 科技公司 ──
    "SpaceX", "Tesla", "neuralink", "blueorigin",
    "OpenAI", "AnthropicAI", "GoogleDeepMind", "GoogleAI",
    "MistralAI", "huggingface", "GroqInc", "Cerebras",
    "perplexity_ai", "cursor_ai", "v0", "cognition_labs",
    "LangChainAI", "weights_biases", "pytorch", "TensorFlow",
    "MSFTResearch", "DeepMind", "xai", "aisdk_", "StabilityAI",
    # ── AI 媒体 / 资讯 ──
    "TechCrunch_AI", "The_Decoder_AI", "kaborojevic",
    "_akhaliq", "AlphaSignalAI", "ai_for_success",
    "svpino", "TheRundownAI", "aisupremacy",
    "Techmeme", "Wired",
    # ── AI 个人 / 研究者 ──
    "DrJimFan", "ylecun", "AndrewYNg", "koltregaskes",
    "goodside", "Teknium", "EMostaque", "aidan_mclau",
    "amasad", "saranormous", "eshear", "gdb",
    # ── 创业 / VC ──
    "ycombinator", "paulg", "sama", "naval",
    "garrytan", "a16z", "sequoia", "Accel",
    "levelsio", "gregisenberg",
]


# ============================================================
# 登录
# ============================================================

async def login_and_save_cookies() -> dict | None:
    """打开浏览器让用户手动登录 Twitter，保存 Cookie 到文件"""
    from playwright.async_api import async_playwright

    logger.info("[twitter] 启动系统 Chrome，请手动登录 Twitter...")
    async with async_playwright() as p:
        # 用系统真实 Chrome（不是 Playwright 自带 Chromium），避免安全检测
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",  # 使用系统安装的 Chrome
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
        """)
        page = await context.new_page()

        await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
        logger.info("[twitter] 请在浏览器窗口中登录你的 Twitter 账号（3 分钟超时）")

        # 等待用户登录完成 — 检测首页元素或 URL 变化
        try:
            await page.wait_for_function(
                """() => {
                    const url = window.location.href;
                    return url.includes('x.com/home') || url.includes('x.com/notifications');
                }""",
                timeout=180000,
            )
            await asyncio.sleep(3)
            logger.info("[twitter] 登录检测成功")
        except Exception:
            logger.warning("[twitter] 登录等待超时")

        cookies = await context.cookies()
        await browser.close()

    # 持久化到文件
    with open(_COOKIE_FILE, "w") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

    logger.info(f"[twitter] Cookie 已保存 ({len(cookies)} 条)")
    return {"cookies": json.dumps(cookies), "cookie_count": len(cookies), "ok": True}


def load_cookies() -> list[dict] | None:
    """从文件加载 Cookie"""
    if not os.path.exists(_COOKIE_FILE):
        return None
    try:
        with open(_COOKIE_FILE) as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 抓取推文
# ============================================================

async def fetch_tweets(accounts: list[str] | None = None) -> list[dict]:
    """从指定账号抓取最近推文，并发控制 3 个同时打开"""
    if accounts is None:
        accounts = AI_ACCOUNTS

    cookies = load_cookies()
    if not cookies:
        logger.warning("[twitter] 无 Cookie，请先登录")
        return []

    items = []
    sem = asyncio.Semaphore(6)  # 限制并发数

    async def _fetch_one(acct: str):
        async with sem:
            return await _fetch_account_tweets(acct, cookies)

    results = await asyncio.gather(*[_fetch_one(a) for a in accounts])
    for r in results:
        items.extend(r)

    logger.info(f"[twitter] 共抓取 {len(items)} 条推文")
    return items


async def _fetch_account_tweets(username: str, cookies: list[dict]) -> list[dict]:
    """抓取单个账号的最近推文"""
    from playwright.async_api import async_playwright

    items = []
    try:
        async with async_playwright() as p:
            # 用系统 Chrome（与登录一致），headless 模式，避免 X.com 检测
            browser = await p.chromium.launch(
                headless=True,
                channel="chrome",
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            await context.add_cookies(cookies)
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                delete navigator.__proto__.webdriver;
            """)
            page = await context.new_page()

            url = f"https://x.com/{username}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(5)  # 等推文 JS 渲染

            # 调试：页面状态
            page_url = page.url
            page_title = await page.title()
            article_count = await page.evaluate("() => document.querySelectorAll('article[data-testid=\"tweet\"]').length")

            # 检查是否被要求登录
            if "login" in page_url.lower() or "i/flow" in page_url:
                logger.warning(f"[twitter] {username}: Cookie失效 → {page_url[:80]}")
                await browser.close()
                return items

            if article_count == 0:
                logger.warning(f"[twitter] {username}: 0 tweets | title={page_title[:60]} | url={page_url[:80]}")
            else:
                logger.info(f"[twitter] {username}: {article_count} tweets found")

            # 提取推文
            tweets = await page.evaluate("""
                () => {
                    const articles = document.querySelectorAll('article[data-testid="tweet"]');
                    const results = [];
                    for (const a of articles) {
                        const textEl = a.querySelector('[data-testid="tweetText"]');
                        const timeEl = a.querySelector('time');
                        const linkEl = a.querySelector('a[href*="/status/"]');
                        if (textEl) {
                            results.push({
                                text: textEl.innerText.trim(),
                                time: timeEl ? timeEl.getAttribute('datetime') : '',
                                link: linkEl ? linkEl.href : '',
                            });
                        }
                        if (results.length >= 3) break;
                    }
                    return results;
                }
            """)

            await browser.close()

            for tweet in tweets:
                text = tweet.get("text", "")
                if not text or len(text) < 20:
                    continue
                # 去掉末尾的"显示更多"等 UI 文本
                text = re.sub(r"\b\d+[KMB]?\s*查看.*$", "", text).strip()
                items.append({
                    "title": text[:160],
                    "url": tweet.get("link") or f"https://x.com/{username}",
                    "source": f"X @{username}",
                    "score": 0,
                    "freshness": 0,  # 稍后解析时间
                    "desc": text[:500],
                    "comments": [],
                })
                if len(items) >= 3:
                    break

    except Exception as e:
        logger.warning(f"[twitter] {username} 抓取失败: {e}")

    return items
