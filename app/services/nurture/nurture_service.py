"""养号流水线编排 — 扫描话题 → 创作文案 → 生成配图 → 发布 → 记录"""
import asyncio
import datetime
import json
import os
import random

from sqlalchemy import select, func
from loguru import logger

from app.templates_env import to_local, today_start_utc

from app.database import async_session
from app.models.nurture import NurtureTopic, NurtureRecord
from app.models.account import Account
from app.models.schedule import Schedule
from app.services.nurture.topic_scanner import TopicScanner, HotTopic
from app.services.nurture.content_writer import NurtureWriter
from app.services.nurture.image_generator import NurtureImageGenerator


async def _load_schedule(schedule_id: int) -> Schedule | None:
    async with async_session() as session:
        result = await session.execute(select(Schedule).where(Schedule.id == schedule_id))
        return result.scalar_one_or_none()


async def _get_account(account_id: int) -> Account | None:
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        return result.scalar_one_or_none()


async def _count_today_posts(account_id: int) -> int:
    """统计今日已发布养号微博数（北京时间 0:00-24:00）"""
    async with async_session() as session:
        since = today_start_utc()
        result = await session.execute(
            select(func.count(NurtureRecord.id)).where(
                NurtureRecord.account_id == account_id,
                NurtureRecord.status == "published",
                NurtureRecord.created_at >= since,
            )
        )
        return result.scalar() or 0


async def _get_past_topics(account_id: int, days: int = 7) -> set[str]:
    """获取最近几天已参与过的话题名"""
    async with async_session() as session:
        since = today_start_utc() - datetime.timedelta(days=days)
        result = await session.execute(
            select(NurtureRecord.topic_name).where(
                NurtureRecord.account_id == account_id,
                NurtureRecord.created_at >= since,
            )
        )
        return {row[0] for row in result.all()}


async def _save_topics(topics: list[HotTopic]):
    async with async_session() as session:
        for t in topics:
            record = NurtureTopic(
                topic_name=t.name,
                topic_query=t.query,
                heat_score=t.heat,
                category=t.category,
                rank=t.rank,
                is_suitable=True,
                raw_data=json.dumps({"heat": t.heat, "desc": t.desc}, ensure_ascii=False),
            )
            session.add(record)
        await session.commit()


async def _save_record(
    topic_name: str, content: str, image_path: str, image_prompt: str,
    account_id: int, external_url: str, external_id: str,
    status: str, error_message: str = "", content_style: str = "",
):
    async with async_session() as session:
        record = NurtureRecord(
            topic_name=topic_name,
            content=content,
            image_local=image_path,
            image_prompt=image_prompt,
            account_id=account_id,
            external_url=external_url,
            external_id=external_id,
            status=status,
            error_message=error_message,
            content_style=content_style,
            published_at=datetime.datetime.utcnow() if status == "published" else None,
        )
        session.add(record)
        await session.commit()


async def execute_nurture_scan(schedule_id: int | None = None):
    """仅扫描热搜话题，不入库发布记录"""
    logger.info("[养号] 开始扫描热搜话题...")
    scanner = TopicScanner()
    topics = await scanner.scan()
    if topics:
        await _save_topics(topics)
        logger.info(f"[养号] 扫描完成，入库 {len(topics)} 个话题，最佳: {topics[0].name}")
    else:
        logger.warning("[养号] 未扫描到合适话题")


async def execute_nurture_publish(schedule_id: int):
    """完整养号发布流程：扫描 → 过滤 → 创作 → 配图 → 发布"""
    config = await _load_schedule(schedule_id)
    if not config:
        logger.error(f"[养号] 任务 {schedule_id} 不存在")
        return

    account_id = config.account_id
    if not account_id:
        logger.error(f"[养号] 任务 {schedule_id} 未配置账号")
        return

    # 确保无论如何都更新 last_run_at，避免调度器因提前退出而反复重试
    async def _touch():
        from sqlalchemy import update
        async with async_session() as session:
            await session.execute(
                update(Schedule)
                .where(Schedule.id == schedule_id)
                .values(last_run_at=datetime.datetime.utcnow())
            )
            await session.commit()

    # 读取任务配置
    extra = json.loads(config.extra_data) if config.extra_data else {}
    interval_minutes = extra.get("interval_minutes", 30)
    max_posts_per_day = extra.get("max_posts_per_day", 5)
    content_style = extra.get("content_style", "knowledge")
    # 支持"轮换"模式：每次随机从知识干货和温暖治愈中选一个
    use_remix = False
    if content_style == "rotate":
        dynamic = extra.get("dynamic_weights", None)
        pool = ["knowledge", "warm"]
        if dynamic:
            styles, weights = zip(*dynamic.items())
            content_style = random.choices(styles, weights=weights)[0]
        else:
            content_style = random.choices(
                pool, weights=[0.6, 0.4],
            )[0]
        # 15% 概率用 remix 改写，提升内容质量
        use_remix = random.random() < 0.15
        logger.info(f"轮换风格 → {content_style}" + (" (remix)" if use_remix else ""))
    filter_keywords = set(extra.get("filter_keywords", "").split(",")) if extra.get("filter_keywords") else set()
    preferred_categories = set(extra.get("preferred_categories", "").split(",")) if extra.get("preferred_categories") else set()
    enable_image = extra.get("enable_image", True)

    # 0. 分时段策略：仅 7:00-23:00 发布
    now_hour = to_local(datetime.datetime.utcnow()).hour
    if now_hour < 7 or now_hour >= 23:
        logger.info(f"[养号] {now_hour}:00 非发布时段，跳过")
        await _touch()
        return

    # 0.5 随机间隔：休眠 0-15 分钟，制造 45-60 分钟的不规则间隔
    delay = random.randint(0, 15 * 60)
    logger.info(f"[养号] 随机延迟 {delay} 秒...")
    await asyncio.sleep(delay)

    # 1. 检查今日发布配额
    today_count = await _count_today_posts(account_id)
    if today_count >= max_posts_per_day:
        logger.info(f"[养号] 今日已发布 {today_count} 条，达到上限 {max_posts_per_day}，跳过")
        await _touch()
        return

    # 2. 获取账号
    account = await _get_account(account_id)
    if not account:
        logger.error(f"[养号] 账号 {account_id} 不存在")
        await _touch()
        return

    # 3. 解析 cookies
    cookies = {}
    if account.cookies:
        try:
            cookie_list = json.loads(account.cookies)
            cookies = {c.get("name", ""): c.get("value", "") for c in cookie_list}
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"[养号] 账号 cookies 解析失败")

    # 4. 扫描热搜
    logger.info("[养号] 扫描热搜话题...")
    scanner = TopicScanner(
        cookies=cookies,
        extra_filter_keywords=filter_keywords,
        preferred_categories=preferred_categories,
    )
    topics = await scanner.scan()
    if not topics:
        logger.warning("[养号] 未找到合适话题，跳过发布")
        await _touch()
        return

    # 入库扫描结果
    await _save_topics(topics)

    # 5. 选择话题（排除近期已参与的）
    past = await _get_past_topics(account_id)
    best = scanner.pick_best(topics, exclude_names=past)
    if not best:
        logger.info("[养号] 所有热门话题近期已参与过")
        await _touch()
        return

    logger.info(f"[养号] 选中话题: {best.name} (热度:{best.heat})")

    # 体育赛事话题强制 remix（避免 AI 瞎猜比赛结果）
    SPORTS_REMIX_KW = ["欧冠", "决赛", "卫冕", "夺冠", "晋级", "淘汰", "比分",
                       "法网", "温网", "澳网", "美网", "大满贯", "NBA", "CBA",
                       "世界杯", "欧洲杯", "中超", "英超", "西甲", "意甲", "德甲"]
    if any(kw in best.name for kw in SPORTS_REMIX_KW):
        use_remix = True
        logger.info(f"体育赛事话题，强制 remix: {best.name}")

    # 6. 创作文案
    writer = NurtureWriter(style=content_style)
    content = await writer.generate(best.name, best.desc, use_remix=use_remix)
    logger.info(f"[养号] 文案生成完成 ({len(content)}字)")

    # 7. 生成配图
    image_path = ""
    image_prompt = ""
    if enable_image:
        img_gen = NurtureImageGenerator()
        image_path = await img_gen.generate(best.name, content[:200])
        if image_path:
            logger.info(f"[养号] 配图: {image_path}")

    # 8. 发布
    from app.services.publisher.weibo_web import WebWeiboPublisher
    publisher = WebWeiboPublisher(cookies_json=account.cookies, headless=True)
    images = [image_path] if image_path and os.path.exists(image_path) else []
    result = await publisher.publish(content, images)

    # 9. 记录
    await _save_record(
        topic_name=best.name,
        content=content,
        image_path=image_path,
        image_prompt=image_prompt,
        account_id=account_id,
        external_url=result.external_url,
        external_id=result.external_id,
        status="published" if result.success else "failed",
        error_message=result.error_message,
        content_style=content_style,
    )

    # 10. 更新时间戳
    await _touch()

    if result.success:
        logger.info(f"[养号] 发布成功: {best.name} → {result.external_url}")
    else:
        logger.error(f"[养号] 发布失败: {result.error_message}")


async def run_nurture_manual(
    account_id: int,
    style: str = "sharp",
    enable_image: bool = True,
    simulate: bool = False,
) -> dict:
    """手动触发一次养号发布（通过 API 或 CLI 调用），返回结果摘要"""
    account = await _get_account(account_id)
    if not account:
        return {"success": False, "error": f"账号 {account_id} 不存在"}

    cookies = {}
    if account.cookies:
        try:
            cookie_list = json.loads(account.cookies)
            cookies = {c.get("name", ""): c.get("value", "") for c in cookie_list}
        except (json.JSONDecodeError, TypeError):
            pass

    # 扫描
    scanner = TopicScanner(cookies=cookies)
    topics = await scanner.scan()
    if not topics:
        return {"success": False, "error": "未找到合适的热搜话题"}

    past = await _get_past_topics(account_id)
    best = scanner.pick_best(topics, exclude_names=past)
    if not best:
        return {"success": False, "error": "所有话题近期已参与过"}

    # 创作
    writer = NurtureWriter(style=style)
    content = await writer.generate(best.name)

    # 配图
    image_path = ""
    if enable_image:
        img_gen = NurtureImageGenerator()
        image_path = await img_gen.generate(best.name, content[:200])

    if simulate:
        await _save_record(
            topic_name=best.name, content=content, image_path=image_path,
            image_prompt="", account_id=account_id, external_url="(模拟)",
            external_id="", status="draft", content_style=style,
        )
        return {
            "success": True,
            "simulate": True,
            "topic": best.name,
            "content": content,
            "image": image_path,
        }

    # 发布
    from app.services.publisher.weibo_web import WebWeiboPublisher
    publisher = WebWeiboPublisher(cookies_json=account.cookies, headless=True)
    images = [image_path] if image_path and os.path.exists(image_path) else []
    result = await publisher.publish(content, images)

    await _save_record(
        topic_name=best.name, content=content, image_path=image_path,
        image_prompt="", account_id=account_id,
        external_url=result.external_url, external_id=result.external_id,
        status="published" if result.success else "failed",
        error_message=result.error_message, content_style=style,
    )

    return {
        "success": result.success,
        "topic": best.name,
        "content": content,
        "image": image_path,
        "url": result.external_url,
        "error": result.error_message,
    }
