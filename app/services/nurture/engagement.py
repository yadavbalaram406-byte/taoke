"""自动互动 — 模拟真人浏览微博、点赞、评论，用于养号防封"""
import asyncio
import json
import random
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.account import Account


# 兴趣领域搜索关键词
INTEREST_KEYWORDS = {
    "体育": ["NBA季后赛", "欧冠决赛", "世界杯预选赛", "中超", "CBA总决赛", "法网", "温网",
             "奥运会", "马拉松", "游泳世锦赛", "羽毛球", "乒乓球", "F1", "拳击"],
    "数码": ["手机评测", "新机发布", "苹果", "华为", "小米", "芯片", "AI", "大模型",
             "笔记本", "耳机", "智能手表", "平板", "显卡", "游戏本"],
    "汽车": ["新车发布", "试驾", "新能源", "特斯拉", "比亚迪", "蔚来", "理想",
             "小米汽车", "自动驾驶", "充电桩", "SUV", "轿跑", "概念车"],
}


async def run_engagement(
    account_id: int = 1,
    likes: int = 4,
    comments: int = 2,
    max_scrolls: int = 10,
    categories: list[str] | None = None,
) -> dict:
    """
    模拟真人刷微博并互动。
    - likes: 最多点赞数
    - comments: 最多评论数
    - max_scrolls: 最多滑动次数
    """
    from playwright.async_api import async_playwright

    async with async_session() as session:
        result = await session.execute(
            select(Account).where(Account.id == account_id)
        )
        acc = result.scalar_one_or_none()
        if not acc or not acc.cookies:
            return {"ok": False, "error": "无可用账号"}

        cookies = json.loads(acc.cookies)

    if categories is None:
        categories = ["体育", "数码", "汽车"]

    # 从兴趣领域随机选关键词作为搜索入口
    all_kw = []
    for cat in categories:
        all_kw.extend(INTEREST_KEYWORDS.get(cat, []))
    random.shuffle(all_kw)

    action_log = {"liked": 0, "commented": 0}

    try:
        from urllib.parse import quote

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                locale="zh-CN",
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            # 随机选一个兴趣关键词搜索
            search_kw = all_kw[0] if all_kw else "NBA"
            search_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(search_kw)}"
            logger.info(f"搜索兴趣内容: {search_kw}")

            await page.goto(search_url, wait_until="networkidle", timeout=20000)
            await asyncio.sleep(random.uniform(2, 4))

            # 模拟浏览搜索结果
            for scroll in range(max_scrolls):
                await page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
                await asyncio.sleep(random.uniform(3, 6))

                if random.random() < 0.3:
                    await asyncio.sleep(random.uniform(4, 10))

                # 随机点赞
                if action_log["liked"] < likes and random.random() < 0.4:
                    liked = await _try_like(page)
                    if liked:
                        action_log["liked"] += 1
                        logger.info(f"点赞 {action_log['liked']}/{likes}")
                        await asyncio.sleep(random.uniform(8, 20))

                # 随机评论
                if action_log["commented"] < comments and random.random() < 0.15:
                    commented = await _try_comment(page)
                    if commented:
                        action_log["commented"] += 1
                        logger.info(f"评论 {action_log['commented']}/{comments}")
                        await asyncio.sleep(random.uniform(15, 30))

            await browser.close()

        logger.info(f"互动完成: 点赞{action_log['liked']} 评论{action_log['commented']}")
        return {"ok": True, **action_log}

    except Exception as e:
        logger.warning(f"互动失败: {e}")
        return {"ok": False, "error": str(e), **action_log}


async def _try_like(page) -> bool:
    """尝试点赞一条可见的微博"""
    try:
        # 找点赞按钮
        btns = await page.evaluate("""
            () => {
                const likes = document.querySelectorAll('a[action-type="fl_like"], [class*="like"]:not([class*="liked"]), footer a[href*="like"]');
                const visible = [];
                for (const el of likes) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top > 0 && rect.top < window.innerHeight) {
                        visible.push(el.getBoundingClientRect().top);
                    }
                }
                return visible;
            }
        """)

        if not btns:
            return False

        # 点一个随机可见的
        target_y = random.choice(btns) - 100
        await page.evaluate(f"window.scrollTo(0, {max(0, target_y)})")
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # 找到并点击
        clicked = await page.evaluate("""
            () => {
                const likes = document.querySelectorAll('[action-type="fl_like"], [class*="like"]:not([class*="liked"])');
                for (const el of likes) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top > 0 && rect.top < window.innerHeight) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)

        if clicked:
            await asyncio.sleep(random.uniform(0.5, 1.5))
        return bool(clicked)

    except Exception:
        return False


async def _try_comment(page) -> bool:
    """尝试评论一条微博（短评）"""
    try:
        # 点击评论按钮
        clicked = await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('a[href*="comment"], [class*="comment"]:not([class*="count"])');
                for (const el of btns) {
                    const rect = el.getBoundingClientRect();
                    if (rect.top > 0 && rect.top < window.innerHeight) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)

        if not clicked:
            return False

        await asyncio.sleep(random.uniform(1, 2))

        # 输入短评
        comments_pool = [
            "确实", "有道理", "说得好", "赞同", "支持",
            "真相了", "是这个理", "说得对", "没错", "点赞",
            "厉害了", "学到了", "干货", "收藏了", "精彩",
            "牛啊", "这波可以", "稳", "好球", "真香",
            "颜值在线", "性能炸裂", "期待", "来了来了",
        ]
        comment = random.choice(comments_pool)

        # 找输入框并输入
        textarea = page.locator("textarea, input[type='text']").first
        await textarea.click()
        await asyncio.sleep(random.uniform(0.3, 0.8))
        await page.keyboard.type(comment, delay=random.randint(50, 150))
        await asyncio.sleep(random.uniform(1, 2))

        # 点击发送
        sent = await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('[class*="send"], [class*="submit"], button:has-text("发送"), a:has-text("发送")');
                for (const el of btns) {
                    if (el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }
        """)

        if sent:
            await asyncio.sleep(random.uniform(1, 3))
        return bool(sent)

    except Exception:
        return False
