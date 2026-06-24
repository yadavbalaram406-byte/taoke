"""科技博主发布服务 — 选一篇资讯 → 深度解读 → 截图原文 → 配图 → 发布"""
import datetime
import os
from loguru import logger

from app.services.techblog.bulletin import generate_bulletin


async def execute_tech_publish(account_id: int = 2) -> dict:
    """完整发布流程：抓取 → 选篇去重 → 深度解读 → 原文截图 → 发布"""
    from app.database import async_session
    from app.models.account import Account
    from app.models.nurture import NurtureRecord
    from app.services.publisher.weibo_web import WebWeiboPublisher
    from app.services.nurture.image_generator import NurtureImageGenerator
    from app.templates_env import today_start_utc
    from sqlalchemy import select

    # 1. 拿今日已发布的资讯 URL，避免重复发同一篇
    async with async_session() as s:
        since = today_start_utc()
        rows = await s.execute(
            select(NurtureRecord.external_url).where(
                NurtureRecord.topic_name == "AI科技日报",
                NurtureRecord.status == "published",
                NurtureRecord.created_at >= since,
            )
        )
        used_urls = {r[0] for r in rows.all() if r[0]}

    # 2. 生成深度解读文案（同时拿到原始条目信息）
    result = await generate_bulletin(used_urls)
    if not result:
        return {"success": False, "error": "无可用资讯或 AI 生成失败"}
    content, article_url, item = result

    # 3. 原文来源截图（首要配图，有实感）
    screenshot_path = ""
    try:
        from app.services.techblog.fetcher import screenshot_source_page
        screenshot_path = await screenshot_source_page(item) or ""
    except Exception as e:
        logger.warning(f"[techblog] 截图失败: {e}")

    # 4. AI 生成配图（作为第二张图，无截图时作为主图）
    ai_image_path = ""
    if not screenshot_path:
        try:
            gen = NurtureImageGenerator()
            ai_image_path = await gen.generate("AI科技日报", content[:200])
        except Exception as e:
            logger.warning(f"[techblog] AI配图失败: {e}")

    # 5. 组装图片列表
    images = []
    if screenshot_path and os.path.exists(screenshot_path):
        images.append(screenshot_path)
    if ai_image_path and os.path.exists(ai_image_path):
        images.append(ai_image_path)

    # 6. 取账号 Cookie
    async with async_session() as s:
        row = await s.execute(select(Account).where(Account.id == account_id))
        acc = row.scalar_one_or_none()
        if not acc or not acc.cookies:
            return {"success": False, "error": "无可用账号"}
        cookies_json = acc.cookies

    # 7. 发布到微博
    publisher = WebWeiboPublisher(cookies_json=cookies_json, headless=True)
    pub_result = await publisher.publish(content, images)

    # 8. 记录（external_url 存原文 URL，供去重用）
    primary_image = screenshot_path or ai_image_path
    async with async_session() as s:
        record = NurtureRecord(
            topic_name="AI科技日报",
            content=content,
            image_local=primary_image,
            image_prompt="",
            account_id=account_id,
            external_url=article_url,
            external_id=pub_result.external_id,
            views=0,
            status="published" if pub_result.success else "failed",
            error_message=pub_result.error_message,
            content_style="knowledge",
            published_at=datetime.datetime.utcnow() if pub_result.success else None,
        )
        s.add(record)
        await s.commit()

    # 清理截图临时文件
    if screenshot_path and os.path.exists(screenshot_path):
        try:
            os.remove(screenshot_path)
        except Exception:
            pass

    return {
        "success": pub_result.success,
        "content": content[:100],
        "article_url": article_url,
        "screenshot": bool(screenshot_path),
        "error": pub_result.error_message,
    }
