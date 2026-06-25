"""AI 养号文案生成器 — 基于热搜话题创作有观点、有互动的微博"""
import asyncio
import httpx
import random
from loguru import logger

from app.config import settings

# 发布前安全检查：命中以下关键词的文案拦截不发
PUBLISH_BLOCK_KW = [
    # 涉毒/违禁品
    "吸毒", "嗑药", "毒品", "大麻", "摇头丸", "冰毒",
    "走私", "贩卖", "非法交易",
    # 煽动对立
    "抵制", "抗议", "示威", "游行", "罢免", "下台",
    # 色情
    "约炮", "嫖娼", "卖淫",
    # 暴力
    "打死", "砍死", "弄死",
]


# 所有风格通用的防幻觉约束
SAFETY_RULES = (
    "【严格约束，违反即失败】\n"
    "1. 这是一个微博实时热搜话题。你无法知道它对应的具体是哪届赛事、哪次事件或哪场比赛。\n"
    "   禁止用你的训练数据去猜测或填充具体背景（例如：不能因为话题名含「韩国」「南非」就联想到某届历史上的比赛）。\n"
    "2. 禁止说出任何赛事届次（如「女足世界杯」「男足亚洲杯」「奥运会」「某年某届」），\n"
    "   只能用模糊表达：「这场比赛」「这次结果」「这支球队」「这件事」。\n"
    "3. 禁止编造：比分、进球数、得分、选手名字、具体时间、精确数据、当事人未公开说过的话。\n"
    "4. 如果只知道话题名称、不知道具体细节，就只谈这个话题引发的普遍讨论或情感共鸣，不谈细节。\n\n"
)

STYLE_PROMPTS = {
    "knowledge": SAFETY_RULES
                 + "你是一个知识型微博博主。请针对「{topic}」这个话题分享观点和背景。\n"
                 "话题简介：{desc}\n"
                 "要求：\n"
                 "1. 先理解话题到底在说什么（综艺节目？社会事件？网络梗？），再分享知识\n"
                 "2. 从常理、历史规律、行业共识角度提供有信息量的分析\n"
                 "3. 有信息增量，让人看完有收获\n"
                 "4. 表达通俗易懂，不要像教科书\n"
                 "5. 140字以内\n"
                 "6. 引用数据时必须是广为人知的公开数据，不确定就不写\n"
                 "直接输出微博文案，不要加前缀说明。",

    "warm": SAFETY_RULES
            + "你是一个温暖治愈的微博博主。请针对「{topic}」这个话题写一段微博。\n"
            "话题简介：{desc}\n"
            "要求：\n"
            "1. 先理解话题到底在说什么（综艺节目？社会事件？网络梗？），再从温暖正面的角度切入\n"
            "2. 给读者带来力量或安慰\n"
            "3. 语言柔软有温度\n"
            "4. 120字以内\n"
            "5. 避免鸡汤味太重，要真诚\n"
            "直接输出微博文案，不要加前缀说明。",
}


class NurtureWriter:
    """养号内容创作器"""

    def __init__(self, style: str = "knowledge"):
        self.style = style if style in STYLE_PROMPTS else "knowledge"

    async def generate(self, topic: str, topic_desc: str = "", use_remix: bool = False) -> str:
        """生成养号微博文案（自动追加 #话题词#）。use_remix=True 时改写热搜广场爆款。"""
        if use_remix:
            content = await self._generate_remix(topic)
            if content:
                if self._is_risky(content):
                    logger.warning("[安全审查] remix 内容违规，回退常规生成")
                else:
                    return self._append_topic_tag(content, topic)
            logger.info("remix 失败，回退到常规生成")

        prompt = STYLE_PROMPTS[self.style].format(
            topic=topic,
            desc=topic_desc or "微博热搜话题，参考话题页了解具体内容",
        )

        # 依次尝试各个 AI provider（DeepSeek → Claude → OpenAI），失败时降级到下一个
        if settings.DEEPSEEK_API_KEY:
            content = await self._call_deepseek(prompt)
            if content is None and settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
                logger.warning("DeepSeek 失败，降级到 Claude")
                content = await self._call_claude(prompt)
            if content is None and settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
                logger.warning("Claude 也失败，降级到 OpenAI")
                content = await self._call_openai(prompt)
        elif settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            content = await self._call_claude(prompt)
            if content is None and settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
                logger.warning("Claude 失败，降级到 OpenAI")
                content = await self._call_openai(prompt)
        elif settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
            content = await self._call_openai(prompt)
        else:
            content = None

        # 所有 AI provider 都失败时，使用本地安全模板兜底
        if content is None:
            logger.warning("所有 AI provider 均不可用，使用安全模板兜底")
            content = self._generate_fallback(topic)

        # 安全检查：命中违禁词则回退到安全模板
        if self._is_risky(content):
            logger.warning(f"[安全审查] 拦截高风险文案，改用安全模板")
            content = self._generate_fallback(topic)

        return self._append_topic_tag(content, topic)

    @staticmethod
    def _is_risky(content: str) -> bool:
        return any(kw in content for kw in PUBLISH_BLOCK_KW)

    async def _generate_remix(self, topic: str) -> str | None:
        """抓取话题广场排名靠前的博文，用 AI 改写"""
        try:
            from urllib.parse import quote
            from playwright.async_api import async_playwright
            from app.database import async_session
            from app.models.account import Account
            from sqlalchemy import select
            import json as _json

            async with async_session() as session:
                result = await session.execute(select(Account).where(Account.id == 1))
                acc = result.scalar_one_or_none()
                if not acc or not acc.cookies:
                    return None
                cookies = _json.loads(acc.cookies)

            search_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(topic)}"
            logger.info(f"抓取热门博文: {topic}")

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context(
                    viewport={"width": 430, "height": 932},
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                    locale="zh-CN",
                )
                await context.add_cookies(cookies)
                page = await context.new_page()
                await page.goto(search_url, wait_until="networkidle", timeout=20000)
                await __import__("asyncio").sleep(2)

                # 提取前几条高赞博文文本
                posts = await page.evaluate("""
                    () => {
                        const articles = document.querySelectorAll('article, .card-wrap, .m-wrap');
                        const results = [];
                        for (const a of articles) {
                            const text = (a.innerText || '').trim();
                            if (text.length > 20 && text.length < 500) {
                                results.push(text);
                            }
                            if (results.length >= 5) break;
                        }
                        return results;
                    }
                """)
                await browser.close()

            if not posts:
                logger.warning("未抓取到热门博文")
                return None

            # 用 AI 改写
            source_text = "\n\n---\n\n".join(posts[:5])
            remix_prompt = (
                f"以下是微博话题「{topic}」下排名靠前的热门博文：\n\n{source_text}\n\n"
                f"请借鉴以上爆款博文的表达方式、情绪节奏和结构，写一条全新的原创微博。要求：\n"
                f"1. 观点和表述都是新的，不是照抄\n"
                f"2. 保持和原博文类似的情绪风格\n"
                f"3. 120字以内，适合微博传播\n"
                f"4. 不要编造具体数据或细节\n"
                f"直接输出微博文案，不要加前缀说明。"
            )

            if settings.DEEPSEEK_API_KEY:
                result = await self._call_deepseek(remix_prompt)
                if result is not None:
                    return result
                # DeepSeek 失败，尝试降级
                if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
                    logger.info("remix DeepSeek 失败，降级到 Claude")
                    return await self._call_claude(remix_prompt)
            elif settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
                return await self._call_claude(remix_prompt)
            return None

        except Exception as e:
            logger.warning(f"remix 生成失败: {e}")
            return None

    @staticmethod
    def _append_topic_tag(content: str, topic: str) -> str:
        """在文案末尾追加 #话题词#，确保微博可被热搜话题索引"""
        tag = f"#{topic}#"
        if content.rstrip().endswith(tag):
            return content
        if tag in content:
            return content
        return f"{content.rstrip()}\n\n{tag}"

    async def _call_claude(self, prompt: str, retries: int = 3) -> str | None:
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                        headers={
                            "x-api-key": settings.ANTHROPIC_API_KEY,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        },
                        json={
                            "model": settings.ANTHROPIC_MODEL,
                            "max_tokens": 400,
                            "temperature": 0.85,
                            "messages": [{"role": "user", "content": prompt}],
                        },
                    )
                    if resp.status_code == 429 and attempt < retries - 1:
                        wait = 2 ** attempt * 5
                        logger.warning(f"Claude 429 限流，{wait}s 后重试")
                        await asyncio.sleep(wait)
                        continue
                    data = resp.json()
                    if "content" in data:
                        return data["content"][0]["text"].strip()
                    logger.error(f"Claude API 返回异常: {data}")
            except Exception as e:
                if attempt < retries - 1:
                    wait = 2 ** attempt * 3
                    logger.warning(f"Claude 失败({e})，{wait}s 后重试")
                    await asyncio.sleep(wait)
                    continue
                logger.exception(f"Claude 文案生成失败: {e}")
        return None

    async def _call_openai(self, prompt: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 400,
                        "temperature": 0.85,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.exception(f"OpenAI 文案生成失败: {e}")
            return None

    async def _call_deepseek(self, prompt: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 400,
                        "temperature": 0.85,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.exception(f"DeepSeek 文案生成失败: {e}")
            return None

    def _generate_fallback(self, topic: str) -> str:
        # 安全兜底：如果 topic 为空，用通用文案
        display_topic = topic.strip() if topic else "最近的热门话题"
        templates = [
            f"今天被「{display_topic}」刷屏了，来说说我的看法。其实这个问题的关键不在于表面现象，而是背后反映出的趋势值得我们每个人关注。你怎么看？",
            f"关于「{display_topic}」，很多人都在讨论。我的观点很简单：看问题不能只看一面，多换几个角度会有不一样的发现。不服来辩。",
            f"刷到「{display_topic}」这个话题，忍不住说两句。有时候我们太容易被带节奏了，冷静下来想想，事实真的如大家说的那样吗？",
        ]
        return random.choice(templates)
