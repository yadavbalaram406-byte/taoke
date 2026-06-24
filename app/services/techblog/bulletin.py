"""科技深度解读生成器 — 选一篇资讯 → 翻译 + 解读 → 微博文案 + 智能话题标签"""
import json
import time
import asyncio
import httpx
from loguru import logger

from app.config import settings

# 微博实时热搜 API
_TRENDS_BASE = (
    "https://m.weibo.cn/api/container/getIndex"
    "?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot"
)
_TRENDS_TECH = _TRENDS_BASE + "%26cate%3D10103"   # 科技分榜
_TRENDS_FINANCE = _TRENDS_BASE + "%26cate%3D10104"  # 财经分榜（备用）

# 科技/金融类标签归类（用于匹配热搜 + 兜底）
_TAG_CATEGORIES = {
    "ai": ["#推特AI资讯早知道#", "#大模型前沿#", "#AIGC#"],
    "tech": ["#科技前沿#", "#数码科技#", "#互联网观察#"],
    "crypto": ["#区块链#", "#Web3#", "#加密货币#"],
    "finance": ["#金融科技#", "#创投#", "#商业观察#"],
    "startup": ["#创业#", "#融资#", "#产品思考#"],
}
_GENERIC_TAG = "#推特AI资讯早知道#"  # 兜底标签
# 类别 → 触发关键词
_CAT_KW = {
    "ai": ["ai", "llm", "gpt", "大模型", "openai", "chatgpt", "claude", "agent",
           "deepseek", "gemini", "copilot", "transformer", "rag", "微调", "推理"],
    "crypto": ["crypto", "bitcoin", "ethereum", "defi", "web3", "区块链", "token", "nft"],
    "finance": ["funding", "ipo", "valuation", "acquisition", "revenue", "stock",
                "invest", "融资", "估值", "收购", "上市", "营收"],
    "startup": ["startup", "funding", "series a", "seed", "创业", "launch", "发布"],
    "tech": ["nvidia", "chip", "browser", "api", "open source", "typescript", "rust",
             "apple", "google", "microsoft", "meta", "amazon", "linux", "android",
             "ios", "macos", "tesla", "autonomous", "cloud", "security", "docker"],
}


async def generate_bulletin(used_urls: set | None = None) -> tuple[str, str, dict] | None:
    """
    选一篇今日未发过的资讯，生成深度解读微博。
    返回 (content, article_url, item)，失败返回 None。
    item 保留原始字段（id/source/url 等），供截图用。
    """
    from app.services.techblog.fetcher import fetch_all

    items = await fetch_all()
    if not items:
        logger.warning("[techblog] 无资讯可用")
        return None

    # 过滤今日已发布的 URL
    used = used_urls or set()
    candidates = [i for i in items if i.get("url") not in used]
    if not candidates:
        logger.info("[techblog] 今日所有资讯已发布，重新从头选")
        candidates = items

    # 排序：新鲜度优先（越新越好），同一天内优先内容丰富的
    candidates.sort(key=lambda x: (
        x.get("freshness", 0),           # 越新越靠前
        len(x.get("comments", [])),      # 评论越多越好
        x.get("score", 0),               # 得分越高越好
    ), reverse=True)
    item = candidates[0]

    # 构建原文上下文
    source = item.get("source", "")
    is_twitter = source.startswith("X @")
    twitter_user = source[3:] if is_twitter else ""  # "X @" 后面的用户名
    context_parts = [f"标题：{item['title']}", f"来源：{source}", f"链接：{item['url']}"]
    if is_twitter and twitter_user:
        context_parts.append(f"发布者：Twitter 用户 @{twitter_user}")
    if item.get("repo"):
        context_parts.append(f"项目：{item['repo']}")
    if item.get("lang"):
        context_parts.append(f"编程语言：{item['lang']}")
    if item.get("desc"):
        context_parts.append(f"简介：{item['desc']}")
    if item.get("comments"):
        context_parts.append("\n海外开发者评论（节选）：")
        for idx, c in enumerate(item["comments"][:3], 1):
            context_parts.append(f"{idx}. {c[:400]}")

    context = "\n".join(context_parts)

    prompt = (
        "你是面向中国科技从业者的科技博主，专注翻译和解读海外最新的科技、AI、金融动态。\n\n"
        f"以下是一条来自海外的资讯：\n\n{context}\n\n"
        "请写一篇面向中国读者的深度解读微博，要求：\n"
        '1. 开头点明信息来源（如"OpenAI研究员XX发推表示"、"HackerNews热议"、"GitHub新项目"），并说明发生了什么\n'
        "2. 重点解读 2-3 个有价值的角度：技术/商业亮点、海外社区怎么看、对国内同行意味着什么\n"
        "3. 结尾一句话说你的看法，或对国内读者 / 开发者的启示\n"
        "4. 全文 300-500 字，全程中文，适合微博阅读节奏\n"
        "5. 不要编造原文或评论中没有提到的数据和细节\n\n"
        "直接输出微博文案（纯正文，不要加话题标签，不要加前缀说明）。"
    )

    content = await _call_llm(prompt)
    if not content:
        logger.warning("[techblog] AI 解读生成失败")
        return None

    # 智能话题标签：优先匹配微博实时热搜，其次用分类兜底
    tags = await _pick_hashtags(content, item)
    content = content.rstrip() + "\n\n" + " ".join(tags)

    article_url = item.get("url", "")
    age_hours = "?"
    if item.get("freshness"):
        age_hours = f"{(time.time() - item['freshness']) / 3600:.0f}h 前"
    logger.info(f"[techblog] 生成解读完成，来源: {item['source']} | {item['title'][:50]} | {age_hours} | 标签: {tags}")
    return content, article_url, item


# ============================================================
# 智能话题标签
# ============================================================

async def _pick_hashtags(content: str, item: dict) -> list[str]:
    """为内容挑选话题标签：科技分榜优先 → 总榜兜底 → 分类标签垫底"""
    content_lower = (content + " " + item.get("title", "")).lower()
    chosen = []

    # 1. 拉取科技分榜 + 总榜（科技分榜排前面，优先匹配）
    tech_tags, general_tags = await _fetch_trending_tags()
    all_trending = tech_tags + general_tags

    if all_trending:
        # 第一轮：精确匹配（话题词原样出现在内容中）
        for tag in all_trending:
            name = tag.lstrip("#").rstrip("#").strip()
            if len(name) < 2:
                continue
            if name.lower() in content_lower:
                chosen.append(f"#{name}#")
                if len(chosen) >= 3:
                    break

    # 2. 热搜不够 → 用分类标签补齐
    if len(chosen) < 2:
        extra = _category_tags(content_lower, needed=3 - len(chosen))
        chosen.extend(extra)

    # 3. 去重
    seen = set()
    result = []
    for t in chosen:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:3]


async def _fetch_trending_tags() -> tuple[list[str], list[str]]:
    """拉取微博热搜话题名。
    返回 (科技分榜列表, 总榜列表)，科技分榜优先匹配。
    """
    # 获取 cookies
    cookie_str = ""
    try:
        from app.database import async_session
        from app.models.account import Account
        from sqlalchemy import select

        async with async_session() as s:
            row = await s.execute(select(Account).where(Account.id == 1))
            acc = row.scalar_one_or_none()
            if acc and acc.cookies:
                cookies = json.loads(acc.cookies)
                cookie_str = "; ".join(f"{c.get('name', '')}={c.get('value', '')}" for c in cookies)
    except Exception:
        pass

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://m.weibo.cn/",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str

    async def _fetch_one(url: str, label: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"[techblog] {label} 热搜返回 {resp.status_code}")
                    return []
                data = resp.json()
            tags = []
            for card in data.get("data", {}).get("cards", []):
                for item in card.get("card_group", []):
                    desc = item.get("desc", "").strip()
                    if desc:
                        tags.append(desc)
            logger.info(f"[techblog] {label}热搜 {len(tags)} 条")
            return tags
        except Exception as e:
            logger.warning(f"[techblog] {label}热搜获取失败: {e}")
            return []

    # 并发拉科技分榜 + 总榜
    tech_tags, general_tags = await asyncio.gather(
        _fetch_one(_TRENDS_TECH, "科技分榜"),
        _fetch_one(_TRENDS_BASE, "总榜"),
    )
    return tech_tags, general_tags


def _category_tags(content_lower: str, needed: int = 3) -> list[str]:
    """根据内容关键词匹配分类，返回兜底标签"""
    picked = []
    for cat, keywords in _CAT_KW.items():
        if any(kw in content_lower for kw in keywords):
            pool = _TAG_CATEGORIES.get(cat, [])
            if pool:
                picked.append(pool[0])  # 每类取第一个
        if len(picked) >= needed:
            break
    # 如果内容匹配太少，补「科技前沿」避免裸奔
    if not picked:
        picked = [_GENERIC_TAG, "#科技前沿#"]
    return picked[:needed]


# ============================================================
# AI 调用
# ============================================================

async def _call_llm(prompt: str) -> str | None:
    """DeepSeek 优先，失败降级 Claude。"""
    if settings.DEEPSEEK_API_KEY:
        result = await _call_deepseek(prompt)
        if result:
            return result
        if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            logger.info("[techblog] DeepSeek 失败，降级到 Claude")
            return await _call_claude(prompt)
    if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
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
                    "max_tokens": 1200,
                    "temperature": 0.75,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.exception(f"[techblog] Claude 失败: {e}")
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
                    "max_tokens": 1200,
                    "temperature": 0.75,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.exception(f"[techblog] DeepSeek 失败: {e}")
        return None
