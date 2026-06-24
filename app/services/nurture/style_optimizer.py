"""动态风格权重 — 根据前一天互动数据自动调整各风格权重"""
import datetime
import json
from loguru import logger
from sqlalchemy import select, func

from app.database import async_session
from app.models.nurture import NurtureRecord


# 默认权重（冷启动）
DEFAULT_WEIGHTS = {
    "sharp": 0.25,
    "humorous": 0.30,
    "knowledge": 0.30,
    "warm": 0.15,
}

# 最小权重，防止某风格被完全淘汰
MIN_WEIGHT = 0.05


async def calculate_dynamic_weights(days: int = 1) -> dict[str, float]:
    """
    根据最近 N 天的互动数据计算各风格权重。
    评分公式: 总互动数 / 发布次数，然后归一化。
    互动数 = 转发*3 + 评论*2 + 点赞*1（加权，评论比点赞重要）
    """
    async with async_session() as session:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)

        result = await session.execute(
            select(NurtureRecord).where(
                NurtureRecord.status == "published",
                NurtureRecord.content_style != "",
                NurtureRecord.created_at >= since,
                NurtureRecord.views > 0,
            )
        )
        posts = result.scalars().all()

    if len(posts) < 5:
        logger.info(f"有效帖子不足({len(posts)}条)，使用默认权重")
        return DEFAULT_WEIGHTS

    # 按风格汇总
    style_stats: dict[str, list[float]] = {}
    for p in posts:
        s = p.content_style
        if s not in style_stats:
            style_stats[s] = []
        # 互动分 = 转发*3 + 评论*2 + 点赞*1，再除以阅读量（互动率）
        engagement = p.reposts * 3 + p.comments * 2 + p.likes * 1
        if p.views > 0:
            rate = engagement / p.views * 10000  # 万分比
        else:
            rate = 0
        style_stats[s].append(rate)

    # 计算每风格平均互动率
    style_scores = {}
    for s, rates in style_stats.items():
        avg = sum(rates) / len(rates)
        style_scores[s] = avg

    # 归一化为权重
    total = sum(style_scores.values()) or 1
    weights = {}
    for s in ["sharp", "humorous", "knowledge", "warm"]:
        score = style_scores.get(s, 0)
        weights[s] = max(MIN_WEIGHT, score / total)

    # 确保总和为 1
    wtotal = sum(weights.values())
    weights = {k: round(v / wtotal, 3) for k, v in weights.items()}

    logger.info(f"动态权重: {weights}")
    return weights


async def apply_weights_to_schedule():
    """将动态权重写入当前活跃任务的 extra_data"""
    weights = await calculate_dynamic_weights()

    from app.models.schedule import Schedule
    from sqlalchemy import update

    async with async_session() as session:
        result = await session.execute(
            select(Schedule).where(
                Schedule.task_type == "nurture_publish",
                Schedule.is_active == True,
            )
        )
        schedules = result.scalars().all()

        for sch in schedules:
            extra = json.loads(sch.extra_data) if sch.extra_data else {}
            extra["dynamic_weights"] = weights
            sch.extra_data = json.dumps(extra, ensure_ascii=False)

        await session.commit()
        logger.info(f"权重已写入 {len(schedules)} 个任务")

    # 打印对比
    print("\n📊 动态权重（基于互动率）:")
    style_names = {"sharp": "犀利观点", "humorous": "幽默有趣", "knowledge": "干货知识", "warm": "温暖治愈"}
    for s in ["sharp", "humorous", "knowledge", "warm"]:
        bar = "█" * int(weights[s] * 50)
        print(f"  {style_names[s]:8s} {weights[s]:.0%} {bar}")

    return weights
