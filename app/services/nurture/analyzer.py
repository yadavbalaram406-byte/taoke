"""养号效果分析 — 先刷新阅读量，再甄别无效数据，生成优化建议"""
import datetime
import json
import httpx
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.nurture import NurtureRecord, NurtureIncident
from app.config import settings


async def generate_report(days: int = 1) -> str:
    """生成每日效果分析报告。先刷阅读量，再过滤删除/未采集的帖子。"""
    # Step 0: 先刷新阅读量
    from app.services.nurture.view_scraper import update_views
    await update_views(account_id=1)

    # Step 1: 获取指定天数内的已发布记录
    async with async_session() as session:
        since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        result = await session.execute(
            select(NurtureRecord).where(
                NurtureRecord.status == "published",
                NurtureRecord.created_at >= since,
            ).order_by(NurtureRecord.views.desc())
        )
        posts = result.scalars().all()

    if not posts:
        return "暂无发布数据，无法生成分析报告。"

    # Step 2: 甄别 0 阅读量的帖子
    now = datetime.datetime.utcnow()
    valid_posts = []
    deleted_posts = []
    pending_posts = []

    for p in posts:
        if p.views > 0:
            valid_posts.append(p)
        elif (now - p.created_at).total_seconds() < 1800:  # 30 分钟内
            pending_posts.append(p)
        else:
            # 超过 30 分钟仍为 0 → 很可能已删除
            deleted_posts.append(p)

    # Step 3: 进一步验证 "已删除" 的帖子（查微博时间线确认）
    if deleted_posts:
        deleted_posts = await _verify_deleted(deleted_posts)

    if not valid_posts:
        return "暂无有效阅读数据，可能帖子刚发布或数据尚未采集到。"

    # Step 4: 基于有效数据统计分析
    total = len(valid_posts)
    total_views = sum(p.views for p in valid_posts)
    avg_views = total_views // total
    max_post = max(valid_posts, key=lambda p: p.views)
    min_post = min(valid_posts, key=lambda p: p.views)

    # 按分类统计
    from app.services.nurture.topic_scanner import CATEGORY_KW
    cat_data: dict[str, list[int]] = {}
    for p in valid_posts:
        cat = "综合"
        for cname, keywords in CATEGORY_KW.items():
            for kw in keywords:
                if kw in p.topic_name:
                    cat = cname
                    break
            if cat != "综合":
                break
        if cat not in cat_data:
            cat_data[cat] = []
        cat_data[cat].append(p.views)

    cat_stats = []
    for cat, views in sorted(cat_data.items(), key=lambda x: sum(x[1]) // max(len(x[1]), 1), reverse=True):
        if len(views) >= 1:
            cat_stats.append(f"- **{cat}**：{len(views)} 条，均阅读 {sum(views) // len(views):,}")

    # 构建报告
    report = f"""## 养号效果日报

**统计周期**：最近 {days} 天
**有效帖子**：{total} 条
**总阅读**：{total_views:,}
**均阅读**：{avg_views:,}

### 最佳表现
🥇 {max_post.topic_name} — {max_post.views:,} 阅读
> {max_post.content[:80]}...

### 最差表现（有效数据内）
🔻 {min_post.topic_name} — {min_post.views:,} 阅读

### 分类排行
{chr(10).join(cat_stats) if cat_stats else '(数据不足)'}

### 帖子明细
"""
    for p in valid_posts:
        report += f"\n- [{p.views:>7,}] {p.topic_name}"

    # 排除项
    if deleted_posts:
        report += "\n\n### 已排除（可能已删除）\n"
        for p in deleted_posts:
            report += f"\n- ~~{p.topic_name}~~"

    if pending_posts:
        report += "\n\n### 待采集（刚发布）\n"
        for p in pending_posts:
            age = int((now - p.created_at).total_seconds() // 60)
            report += f"\n- ⏳ {p.topic_name}（{age} 分钟前）"

    # 运营经验日记
    async with async_session() as session:
        result = await session.execute(
            select(NurtureIncident).order_by(NurtureIncident.created_at.desc()).limit(10)
        )
        incidents = result.scalars().all()

    if incidents:
        report += "\n\n---\n\n### 📓 运营经验日记\n\n"
        report += "以下是在运营过程中积累的问题、教训和解决方案，供后续优化参考：\n\n"
        for inc in incidents:
            sev = {"critical": "🔴 严重", "high": "🟠 重要", "medium": "🟡 一般", "low": "🟢 轻微"}.get(inc.severity, "")
            cat = {"bug": "Bug修复", "filter": "过滤规则", "content": "内容策略", "strategy": "运营策略"}.get(inc.category, inc.category)
            report += f"**{inc.created_at.strftime('%m-%d')} | {sev} | {cat}**\n"
            report += f"> {inc.title}\n\n"
            report += f"详情：{inc.detail}\n\n"
            report += f"解决：{inc.solution}\n\n"

    # AI 优化建议
    suggestion = await _ai_suggest(report)
    return report + "\n\n---\n\n" + suggestion


async def _verify_deleted(posts: list[NurtureRecord]) -> list[NurtureRecord]:
    """通过微博时间线 API 验证帖子是否仍然存在。返回确认已删除的列表。"""
    import asyncio
    try:
        from app.database import async_session
        from app.models.account import Account

        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.id == 1))
            account = result.scalar_one_or_none()
            if not account or not account.cookies or not account.uid:
                return posts

        from playwright.async_api import async_playwright

        cookies = json.loads(account.cookies) if account.cookies else []
        uid = account.uid

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

            # 获取时间线数据
            api_url = f"https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=107603{uid}"
            data = await page.evaluate(f"""
                async () => {{
                    const r = await fetch('{api_url}', {{credentials: 'include'}});
                    return await r.json();
                }}
            """)

            await browser.close()

            # 收集时间线中所有帖子的文本
            timeline_texts = []
            for card in data.get("data", {}).get("cards", []):
                mblog = card.get("mblog")
                if mblog:
                    text = (mblog.get("text", "") or "").replace("<span class=\"url-icon\">", "").replace("</span>", "")
                    timeline_texts.append(text)

            # 核验每个帖子
            confirmed_deleted = []
            for p in posts:
                content_key = p.content[:25].replace("#", "").replace("\n", "").strip()
                found = any(content_key in t for t in timeline_texts)
                if found:
                    # 还在时间线里，只是没读到 views → 降级为 pending
                    logger.info(f"帖子 #{p.id}「{p.topic_name}」仍在微博上，跳过")
                else:
                    confirmed_deleted.append(p)

            return confirmed_deleted

    except Exception as e:
        logger.warning(f"验证已删除帖子失败: {e}")
        return posts


async def _ai_suggest(data: str) -> str:
    """用 AI 分析数据并给出优化建议"""
    prompt = f"""你是微博运营专家。分析以下养号发布数据，给出 3-5 条具体的优化建议。
重点关注：哪些话题类型的阅读量更高、什么内容特点容易爆、什么话题要避开。
建议要可执行、具体，每条不超过 50 字。
注意：标记为"已排除"或"待采集"的帖子不参与分析。

{data[:3000]}

直接输出建议，格式：每条一行，用数字序号开头。"""

    try:
        # Claude 优先（走中转站），未配置则回退 DeepSeek
        if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.ANTHROPIC_MODEL,
                        "max_tokens": 500,
                        "temperature": 0.7,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp_data = resp.json()
                return "### 优化建议\n\n" + resp_data["content"][0]["text"].strip()
        if settings.DEEPSEEK_API_KEY:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 500,
                        "temperature": 0.7,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                resp_data = resp.json()
                return "### 优化建议\n\n" + resp_data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"AI 建议生成失败: {e}")

    return _basic_suggestions(data)


def _basic_suggestions(data: str) -> str:
    return """### 优化建议

1. 社会民生类话题（如公务员、教师、医疗）通常阅读量更高，优先参与
2. 纯娱乐八卦话题互动高但阅读量不稳定，每天不超过 2 条
3. 商业/品牌相关话题阅读量普遍偏低，继续过滤
4. 标题带具体数字、转折、疑问句的内容更容易被点击
5. 早 8 点和晚 8 点是流量高峰，优先覆盖这两个时段"""
