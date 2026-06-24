"""阅读量采集 — 通过微博移动端 API 翻页逐条获取阅读数"""
import asyncio
import json
import re
from loguru import logger
from sqlalchemy import select, update

from app.database import async_session
from app.models.nurture import NurtureRecord
from app.models.account import Account


async def _call_api(page, url: str) -> dict:
    """在已登录的 Playwright 页面中调用微博 API"""
    return await page.evaluate(f"""
        async () => {{
            const r = await fetch('{url}', {{credentials: 'include'}});
            return await r.json();
        }}
    """)


async def _scrape_views(page, uid: str, records: list[NurtureRecord]) -> int:
    """
    翻页拉取微博时间线，按内容匹配本地记录后逐条查阅读量。
    返回更新条数。
    """
    # Step 1: 翻页拉取足够多的时间线帖子
    timeline_posts = []
    since_id = None
    for page_num in range(5):
        api_url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=107603{uid}"
        if since_id:
            api_url += f"&since_id={since_id}"

        data = await _call_api(page, api_url)
        cards = data.get("data", {}).get("cards", [])

        page_count = 0
        for card in cards:
            mblog = card.get("mblog")
            if mblog:
                raw = (mblog.get("text", "") or "")
                clean = re.sub(r'<[^>]+>', '', raw)
                clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
                clean = clean.replace("&lt;", "<").replace("&gt;", ">")
                timeline_posts.append({"id": str(mblog.get("id", "")), "text": clean})
                page_count += 1

        cardlist_info = data.get("data", {}).get("cardlistInfo", {})
        since_id = cardlist_info.get("since_id")
        if not since_id or page_count == 0:
            break

    logger.info(f"时间线共 {len(timeline_posts)} 条（{page_num + 1} 页）")

    if not timeline_posts:
        return 0

    # Step 2: 按内容匹配本地记录
    updated = 0
    for record in records:
        if record.views > 0:
            continue

        content_key = re.sub(r'<[^>]+>', '', record.content[:40])
        content_key = content_key.replace("#", "").replace("\n", "").strip()
        if len(content_key) < 10:
            continue

        matched_post = None
        for tp in timeline_posts:
            # timeline 文本也去掉 # 话题标签后再比较
            clean_text = tp["text"].replace("#", "")
            if content_key in clean_text:
                matched_post = tp
                break

        if not matched_post:
            continue

        # Step 3: 查该帖子阅读量
        detail = await _call_api(
            page, f"https://m.weibo.cn/statuses/show?id={matched_post['id']}"
        )
        detail_data = detail.get("data", {})
        reads = int(detail_data.get("reads", 0) or 0)

        if reads > 0:
            reposts = int(detail_data.get("reposts_count", 0) or 0)
            comments = int(detail_data.get("comments_count", 0) or 0)
            likes = int(detail_data.get("attitudes_count", 0) or 0)
            async with async_session() as session:
                await session.execute(
                    update(NurtureRecord)
                    .where(NurtureRecord.id == record.id)
                    .values(views=reads, external_id=matched_post["id"],
                            reposts=reposts, comments=comments, likes=likes)
                )
                await session.commit()
            updated += 1
            logger.info(f"#{record.id} {record.topic_name[:20]} → 阅读量 {reads}")

    return updated


async def update_views(account_id: int | None = None) -> int:
    """从微博抓取阅读量并更新数据库"""
    from playwright.async_api import async_playwright

    async with async_session() as session:
        if account_id:
            result = await session.execute(
                select(Account).where(Account.id == account_id)
            )
        else:
            result = await session.execute(
                select(Account).where(Account.is_active == True).limit(1)
            )
        account = result.scalar_one_or_none()

        if not account or not account.cookies:
            logger.warning("无可用的微博账号 Cookie")
            return 0

        uid = account.uid
        cookies_json = account.cookies

        result = await session.execute(
            select(NurtureRecord).where(
                NurtureRecord.status == "published",
                NurtureRecord.views == 0,
            ).order_by(NurtureRecord.created_at.desc()).limit(50)
        )
        records = result.scalars().all()

    if not records:
        logger.info("没有需要更新阅读量的记录")
        return 0

    if not uid:
        uid = await _get_uid(cookies_json)
        if uid:
            async with async_session() as s:
                await s.execute(update(Account).where(Account.id == account.id).values(uid=uid))
                await s.commit()

    if not uid:
        logger.warning("无法获取用户 UID")
        return 0

    try:
        cookies = json.loads(cookies_json) if cookies_json else []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                locale="zh-CN",
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            await page.goto("https://m.weibo.cn/compose/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)

            if "passport" in page.url:
                logger.warning("微博 Cookie 已失效，请重新扫码登录")
                await browser.close()
                return 0

            count = await _scrape_views(page, uid, records)
            await browser.close()
            return count

    except Exception as e:
        logger.warning(f"阅读量采集失败: {e}")
        return 0


async def _get_uid(cookies_json: str) -> str | None:
    """通过微博 API 获取 UID"""
    try:
        from playwright.async_api import async_playwright

        cookies = json.loads(cookies_json) if cookies_json else []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                locale="zh-CN",
            )
            await context.add_cookies(cookies)
            page = await context.new_page()
            await page.goto("https://m.weibo.cn/compose/", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(1)

            data = await _call_api(page, "https://m.weibo.cn/api/config")
            uid = str(data.get("data", {}).get("uid", ""))
            await browser.close()

            if uid:
                logger.info(f"获取到 UID: {uid}")
                return uid

    except Exception as e:
        logger.warning(f"获取 UID 失败: {e}")

    return None


async def update_all_account_views() -> int:
    """更新所有已激活账号的阅读量"""
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.is_active == True))
        accounts = result.scalars().all()

    total = 0
    for account in accounts:
        count = await update_views(account_id=account.id)
        total += count

    return total
