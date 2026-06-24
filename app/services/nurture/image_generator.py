"""养号配图生成器 — 智谱 CogView-4 AI 生图，失败时无图（不出大字报）"""
import asyncio
import json
import os
import httpx
import uuid
from loguru import logger

from app.config import settings


class NurtureImageGenerator:
    """为养号微博生成配图

    生成链路:
    1. LLM 将话题转成中文图片提示词
    2. 智谱 CogView-4 根据提示词生成图片
    3. 下载到本地
    4. 失败时返回空，不配图（绝不出大字报）
    """

    ZHIPU_IMAGE_URL = "https://open.bigmodel.cn/api/paas/v4/images/generations"

    # 真人新闻类关键词 — 这类话题 AI 生图不靠谱，应抓真实图片
    REAL_PERSON_KW = [
        "航天员", "宇航员", "运动员", "球员", "球星", "选手", "教练",
        "明星", "演员", "歌手", "导演", "冠军", "主帅", "队长",
        "去世", "逝世", "遇难", "牺牲", "悼念",
        # 体育赛事/运动员名（避免AI生成无头人）
        "法网", "温网", "澳网", "美网", "大满贯", "公开赛",
        "网球", "羽毛球", "乒乓球", "游泳", "田径", "滑雪",
        "NBA", "CBA", "中超", "欧冠", "英超", "西甲",
        "拳击", "格斗", "UFC", "F1", "赛车",
    ]

    def __init__(self):
        os.makedirs(settings.NURTURE_IMAGE_PATH, exist_ok=True)

    # ====== 主入口 ======

    def _is_real_person_topic(self, topic: str) -> bool:
        """判断是否为真人新闻类话题（运动员、明星、航天员等）"""
        return any(kw in topic for kw in self.REAL_PERSON_KW)

    async def _scrape_weibo_image(self, topic: str) -> str | None:
        """从微博话题广场抓取一张真实配图"""
        try:
            from urllib.parse import quote
            from playwright.async_api import async_playwright

            # 取账号 cookies
            from app.database import async_session
            from app.models.account import Account
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(select(Account).where(Account.id == 1))
                acc = result.scalar_one_or_none()
                if not acc or not acc.cookies:
                    return None
                cookies = json.loads(acc.cookies)

            search_url = f"https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D{quote(topic)}"
            logger.info(f"抓取微博真实图片: {topic}")

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
                await asyncio.sleep(2)

                # 从搜索结果中提取第一张合适的图片
                img_url = await page.evaluate("""
                    () => {
                        const imgs = document.querySelectorAll('article img[src]');
                        for (const img of imgs) {
                            const src = img.src;
                            // 跳过头像、图标、表情
                            if (src.includes('avatar') || src.includes('icon') || src.includes('emoji')) continue;
                            if (src.includes('sinaimg.cn') || src.includes('wbimg.cn') || src.includes('weibo.cn')) {
                                return src.replace('/orj360/', '/large/').replace('/thumb150/', '/large/');
                            }
                            if (img.width > 200 && img.height > 200) return src;
                        }
                        return null;
                    }
                """)

                await browser.close()

                if img_url:
                    logger.info(f"找到真实图片: {img_url[:80]}...")
                    return await self._download_image(img_url, topic)

                return None

        except Exception as e:
            logger.warning(f"抓取微博图片失败: {e}")
            return None

    async def generate(self, topic: str, content: str = "") -> str:
        """生成配图，返回本地文件路径。
        真人新闻话题 → 抓微博真实图片，其他话题 → AI 生图 → 模板兜底"""
        # 真人新闻：抓真实图片，不生成
        if self._is_real_person_topic(topic):
            logger.info(f"真人话题「{topic}」，抓取微博真实图片...")
            result = await self._scrape_weibo_image(topic)
            if result:
                return result
            logger.info("未找到真实图片，跳过配图")
            return ""  # 没找到也不出大字报，直接无图

        # 非真人话题：AI 生图
        if not settings.ZHIPU_API_KEY:
            logger.info("未配置 ZHIPU_API_KEY，跳过配图")
            return ""

        result = await self._generate_cogview(topic, content)
        if result:
            return result

        logger.info("CogView 生图失败，跳过配图")
        return ""

    # ====== CogView-4 生图 ======

    async def _generate_cogview(self, topic: str, content: str) -> str | None:
        """完整链路：LLM 写中文提示词 → CogView-4 生图 → 下载"""
        try:
            # Step 1: 用 DeepSeek 生成中文图片提示词（CogView-4 中文理解更好）
            image_prompt = await self._get_image_prompt(topic, content)
            if not image_prompt:
                return None

            logger.info(f"图片提示词: {image_prompt[:100]}...")

            # Step 2: 按优先级尝试生图，免费模型最多重试5次
            import asyncio as _asyncio
            for model in ("cogView-4-250304", "cogview-3-flash"):
                max_retries = 5 if model == "cogview-3-flash" else 1
                for attempt in range(1, max_retries + 1):
                    image_url = await self._call_cogview(image_prompt, model)
                    if image_url:
                        break
                    if attempt < max_retries:
                        delay = attempt * 2
                        logger.info(f"{model} 第{attempt}次失败，{delay}秒后重试...")
                        await _asyncio.sleep(delay)
                if image_url:
                    break
            if not image_url:
                return None

            # Step 3: 下载到本地
            filepath = await self._download_image(image_url, topic)
            if filepath:
                logger.info(f"配图已生成: {filepath}")
                return filepath

            return None

        except Exception as e:
            logger.warning(f"CogView-4 配图生成失败: {e}")
            return None

    async def _get_image_prompt(self, topic: str, content: str) -> str:
        """用 LLM 生成中文图片提示词（CogView-4 中文理解更好）"""
        llm_prompt = (
            f"为以下微博话题生成一个中文图片描述（30字以内），用于 AI 文生图。"
            f"要求：画面简洁、现代、适合社交媒体配图。不要包含文字。\n"
            f"话题：{topic}\n"
            f"只输出描述，不要其他内容。"
        )

        if settings.DEEPSEEK_API_KEY:
            result = await self._llm_deepseek(llm_prompt)
            if result:
                return result
            if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
                return await self._llm_claude(llm_prompt)
        elif settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            return await self._llm_claude(llm_prompt)
        elif settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
            return await self._llm_openai(llm_prompt)
        return ""

    async def _call_cogview(self, prompt: str, model: str = "cogView-4-250304") -> str | None:
        """调用智谱 CogView API 生成图片，返回图片 URL。优先 cogView-4-250304，不可用时降级 cogview-3-flash（免费）"""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    self.ZHIPU_IMAGE_URL,
                    headers={
                        "Authorization": f"Bearer {settings.ZHIPU_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "prompt": prompt,
                    },
                )
                data = resp.json()

                if resp.status_code == 429:
                    logger.warning(f"CogView({model}) 不可用: {data.get('error', {}).get('message', '')}")
                    return None

                if resp.status_code != 200:
                    logger.error(f"CogView({model}) API 错误: {data}")
                    return None

                items = data.get("data", [])
                if items and items[0].get("url"):
                    url = items[0]["url"]
                    logger.info(f"CogView({model}) 生成图片: {url[:80]}...")
                    return url

                logger.error(f"CogView({model}) 返回无图片: {data}")
                return None

        except Exception as e:
            logger.warning(f"CogView({model}) 调用失败: {e}")
            return None

    async def _download_image(self, url: str, topic: str) -> str | None:
        """下载生成的图片到本地"""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"下载图片失败: HTTP {resp.status_code}")
                    return None

                safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in topic)[:20]
                filepath = os.path.join(
                    settings.NURTURE_IMAGE_PATH,
                    f"nurture_{safe_name}_{uuid.uuid4().hex[:8]}.png",
                )
                with open(filepath, "wb") as f:
                    f.write(resp.content)

                logger.info(f"图片已下载: {filepath} ({len(resp.content)} bytes)")
                return filepath

        except Exception as e:
            logger.warning(f"下载图片失败: {e}")
            return None

    # ====== LLM 调用（生成图片提示词） ======

    async def _llm_deepseek(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "max_tokens": 80,
                        "temperature": 0.9,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""

    async def _llm_claude(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.ANTHROPIC_BASE_URL}/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 80,
                        "temperature": 0.9,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data.get("content", [{}])[0].get("text", "").strip()
        except Exception:
            return ""

    async def _llm_openai(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "max_tokens": 80,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""

    # ====== 不再使用 Pillow 大字报模板 ======
