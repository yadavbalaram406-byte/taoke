"""飞书 AI 资讯 → 微博发布服务
流程：抓飞书群今日消息 → AI 排版成两条微博 → 依次发布
"""
import asyncio
import datetime
import json
import random
from loguru import logger

import httpx

from app.config import settings
from app.services.feishu.fetcher import fetch_today_messages


# ============================================================
# AI 排版
# ============================================================

async def _call_llm(prompt: str) -> str | None:
    """DeepSeek 优先，失败降级 Claude"""
    if settings.DEEPSEEK_API_KEY:
        result = await _call_deepseek(prompt)
        if result:
            return result
    if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
        logger.info("[feishu] DeepSeek 失败，降级到 Claude")
        return await _call_claude(prompt)
    return None


async def _call_claude(prompt: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.ANTHROPIC_MODEL,
                    "max_tokens": 2000,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
            if "content" not in data:
                logger.error(f"[feishu] Claude 响应无 content 字段: {data}")
                return None
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.exception(f"[feishu] Claude 失败: {e}")
        return None


async def _call_deepseek(prompt: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "max_tokens": 2000,
                    "temperature": 0.7,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
            if "choices" not in data:
                logger.error(f"[feishu] DeepSeek 响应无 choices 字段: {data}")
                return None
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception(f"[feishu] DeepSeek 失败: {e}")
        return None


async def generate_weibo_posts(messages: list[dict]) -> list[str] | None:
    """
    把飞书群消息列表交给 AI，生成两条微博文案。
    返回 [post1, post2]，失败返回 None。
    """
    today = datetime.date.today().strftime("%Y-%m-%d")
    combined = "\n\n---\n\n".join(
        f"[{m['time']}]\n{m['text']}" for m in messages
    )

    prompt = f"""你是一位专注 AI 行业的科技博主，每天在微博发布 AI 前沿资讯。

以下是今日（{today}）AI 资讯群里的原始内容：

{combined}

请根据以上内容，写两条独立的微博文案，要求：

第一条：AI 行业综述
- 聚焦 OpenAI、Anthropic、GitHub Copilot 等主流厂商的今日动态
- 提炼 3-4 个关键事件，每条配简短分析
- 结尾一句话总结今日大趋势
- 字数 400-600 字，加 3 个相关话题标签

第二条：深度专题
- 聚焦今日最有价值的一个技术/产品话题（如 Cerebras、某个模型、某个技术趋势）
- 有具体数据、事件和反共识观点
- 字数 400-600 字，加 3 个相关话题标签

输出格式（严格按此格式，不要多余说明）：
===POST1===
（第一条内容）
===POST2===
（第二条内容）
===END===
"""

    raw = await _call_llm(prompt)
    if not raw:
        logger.error("[feishu] AI 生成失败")
        return None

    # 解析两条
    try:
        post1 = raw.split("===POST1===")[1].split("===POST2===")[0].strip()
        post2 = raw.split("===POST2===")[1].split("===END===")[0].strip()
        if not post1 or not post2:
            raise ValueError("解析结果为空")
        logger.info(f"[feishu] 生成成功：post1={len(post1)}字, post2={len(post2)}字")
        return [post1, post2]
    except Exception as e:
        logger.error(f"[feishu] 解析 AI 输出失败: {e}\n原始输出: {raw[:300]}")
        return None


# ============================================================
# 主入口
# ============================================================

async def _already_published_today(account_id: int) -> bool:
    """检查今天是否已经发布过飞书AI资讯"""
    from app.database import async_session
    from app.models.nurture import NurtureRecord
    from app.templates_env import today_start_utc
    from sqlalchemy import select, func

    async with async_session() as s:
        since = today_start_utc()
        result = await s.execute(
            select(func.count(NurtureRecord.id)).where(
                NurtureRecord.account_id == account_id,
                NurtureRecord.topic_name == "飞书AI资讯",
                NurtureRecord.status == "published",
                NurtureRecord.created_at > since,
            )
        )
        return (result.scalar() or 0) > 0


async def execute_feishu_publish(account_id: int = 2) -> dict:
    """
    完整流程：抓飞书群消息 → AI 排版 → 发两条微博
    供 scheduler 调用。
    当天已发布过则跳过（支持整点补发触发而不会重复发）。
    """
    from app.database import async_session
    from app.models.account import Account
    from app.models.nurture import NurtureRecord
    from app.services.publisher.weibo_web import WebWeiboPublisher
    from sqlalchemy import select

    # 当天已发布过则跳过，避免补发触发重复发布
    if await _already_published_today(account_id):
        logger.info("[feishu] 今日已发布过，跳过本次触发")
        return {"success": True, "skipped": True, "error": "今日已发布，跳过"}

    chat_id = settings.FEISHU_AI_CHAT_ID
    if not chat_id:
        return {"success": False, "error": "未配置 FEISHU_AI_CHAT_ID"}

    # 1. 抓今日飞书群消息
    messages = fetch_today_messages(chat_id)
    if not messages:
        return {"success": False, "error": "今日飞书群暂无消息"}

    # 2. AI 排版成两条微博
    posts = await generate_weibo_posts(messages)
    if not posts:
        return {"success": False, "error": "AI 排版失败"}

    # 3. 获取账号 Cookie
    async with async_session() as s:
        row = await s.execute(select(Account).where(Account.id == account_id))
        acc = row.scalar_one_or_none()
        if not acc or not acc.cookies:
            return {"success": False, "error": f"账号 {account_id} 不存在或未登录"}
        cookies_json = acc.cookies

    publisher = WebWeiboPublisher(cookies_json=cookies_json, headless=True)

    results = []
    for i, content in enumerate(posts, 1):
        logger.info(f"[feishu] 发布第 {i} 条微博 ({len(content)}字)...")
        result = await publisher.publish(content)
        results.append(result)

        # 记录到 NurtureRecord（复用现有表，topic_name 区分）
        async with async_session() as s:
            record = NurtureRecord(
                topic_name="飞书AI资讯",
                content=content,
                image_local="",
                image_prompt="",
                account_id=account_id,
                external_url=result.external_url or "",
                external_id=result.external_id or "",
                views=0,
                status="published" if result.success else "failed",
                error_message=result.error_message or "",
                content_style="knowledge",
                published_at=datetime.datetime.utcnow() if result.success else None,
            )
            s.add(record)
            await s.commit()

        if result.success:
            logger.info(f"[feishu] 第 {i} 条发布成功")
        else:
            logger.error(f"[feishu] 第 {i} 条发布失败: {result.error_message}")

        # 两条之间间隔 30-60 秒，模拟真人操作
        if i < len(posts):
            wait = random.randint(30, 60)
            logger.info(f"[feishu] 等待 {wait}s 后发下一条...")
            await asyncio.sleep(wait)

    success_count = sum(1 for r in results if r.success)
    return {
        "success": success_count > 0,
        "published": success_count,
        "total": len(posts),
        "error": "" if success_count > 0 else results[-1].error_message,
    }
