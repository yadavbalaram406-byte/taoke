"""AI 养号文案生成器 — 基于热搜话题创作有观点、有互动的微博"""
import asyncio
import re as _re
import httpx
import random
from urllib.parse import quote
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


# 有真实热帖上下文时的 prompt（AI 参考真实内容写）
CONTEXT_PROMPT = {
    "knowledge": (
        "以下是微博话题「{topic}」当前的热门博文（这是真实的当前内容，请以此为准）：\n\n"
        "{posts}\n\n"
        "---\n"
        "你是一个知识型微博博主。请参考上面真实内容了解话题背景，从中提炼有信息量的观点写一条微博。\n"
        "要求：\n"
        "1. 不要复制上面的内容，要有自己的视角\n"
        "2. 提供知识增量或有价值的分析\n"
        "3. 140字以内，表达通俗易懂\n"
        "4. 不要编造上面没有提到的具体数据\n"
        "直接输出微博文案，不要加前缀说明。"
    ),
    "warm": (
        "以下是微博话题「{topic}」当前的热门博文（这是真实的当前内容，请以此为准）：\n\n"
        "{posts}\n\n"
        "---\n"
        "你是一个温暖治愈的微博博主。请参考上面真实内容了解话题背景，从温暖正面的角度写一条微博。\n"
        "要求：\n"
        "1. 给读者带来力量或安慰，语言柔软有温度\n"
        "2. 120字以内，真诚不鸡汤\n"
        "3. 不要编造上面没有提到的具体数据\n"
        "直接输出微博文案，不要加前缀说明。"
    ),
}

# 无上下文时的兜底 prompt（加强防幻觉约束）
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

    def __init__(self, style: str = "knowledge", cookies: dict | None = None):
        self.style = style if style in STYLE_PROMPTS else "knowledge"
        self.cookies = cookies or {}

    async def generate(self, topic: str, topic_desc: str = "", use_remix: bool = False) -> str:
        """生成养号微博文案（自动追加 #话题词#）。
        优先通过 httpx 抓取话题热帖作为真实上下文；失败时降级到防幻觉约束 prompt。
        """
        # 1. 先用 httpx 抓话题热帖（轻量，无需 Playwright）
        posts = await self._fetch_topic_posts(topic)
        if posts:
            logger.info(f"抓到 {len(posts)} 条话题热帖，使用真实上下文生成")
            posts_text = "\n\n".join(f"【{i+1}】{p}" for i, p in enumerate(posts))
            style_key = self.style if self.style in CONTEXT_PROMPT else "knowledge"
            prompt = CONTEXT_PROMPT[style_key].format(topic=topic, posts=posts_text)
        else:
            logger.info("未抓到话题热帖，降级到防幻觉约束 prompt")
            prompt = STYLE_PROMPTS[self.style].format(
                topic=topic,
                desc=topic_desc or "微博热搜话题",
            )

        # 2. 调 AI 生成文案
        content = await self._call_ai(prompt)

        # 3. 兜底 & 安全检查
        if content is None:
            logger.warning("所有 AI provider 均不可用，使用安全模板兜底")
            content = self._generate_fallback(topic)
        if self._is_risky(content):
            logger.warning("[安全审查] 拦截高风险文案，改用安全模板")
            content = self._generate_fallback(topic)

        return self._append_topic_tag(content, topic)

    async def _fetch_topic_posts(self, topic: str) -> list[str]:
        """用 httpx 调微博搜索 JSON API 拿话题热帖，无需 Playwright。"""
        if not self.cookies:
            return []
        try:
            url = (
                "https://m.weibo.cn/api/container/getIndex"
                f"?containerid=100103type%3D1%26q%3D{quote(topic)}&page_type=searchall"
            )
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://m.weibo.cn/",
            }
            async with httpx.AsyncClient(timeout=8, cookies=self.cookies, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"话题搜索 API 返回 {resp.status_code}")
                    return []
                data = resp.json()

            posts = []
            for card in data.get("data", {}).get("cards", []):
                for item in (card.get("card_group") or [card]):
                    mblog = item.get("mblog") or {}
                    text = mblog.get("raw_text") or mblog.get("text") or ""
                    text = _re.sub(r"<[^>]+>", "", text).strip()
                    if len(text) > 15:
                        posts.append(text)
                    if len(posts) >= 5:
                        break
                if len(posts) >= 5:
                    break
            return posts
        except Exception as e:
            logger.warning(f"话题热帖抓取失败: {e}")
            return []

    async def _call_ai(self, prompt: str) -> str | None:
        """依次尝试 DeepSeek → Claude → OpenAI"""
        if settings.DEEPSEEK_API_KEY:
            content = await self._call_deepseek(prompt)
            if content is not None:
                return content
            if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
                logger.warning("DeepSeek 失败，降级到 Claude")
                content = await self._call_claude(prompt)
                if content is not None:
                    return content
            if settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
                logger.warning("Claude 也失败，降级到 OpenAI")
                return await self._call_openai(prompt)
        elif settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            content = await self._call_claude(prompt)
            if content is not None:
                return content
            if settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
                logger.warning("Claude 失败，降级到 OpenAI")
                return await self._call_openai(prompt)
        elif settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
            return await self._call_openai(prompt)
        return None

    @staticmethod
    def _is_risky(content: str) -> bool:
        return any(kw in content for kw in PUBLISH_BLOCK_KW)

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
