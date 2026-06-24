import httpx
import os
from loguru import logger

from app.config import settings


class Copywriter:
    """文案生成器 — 支持模板和AI两种模式"""

    DISCOUNT_TEMPLATES = [
        "🔥【{short_title}】券后仅需¥{coupon_price}！原价¥{price}，立省¥{coupon_amount}。{shop_name}爆款，月销{sales_volume}+！\n\n📱 {tao_password}\n🔗 {cps_link}\n#好物推荐#",
        "💰 好价速抢！{title}\n券后价：¥{coupon_price}（原价¥{price}）\n优惠券：满减¥{coupon_amount}\n月销{sales_volume}+ | {shop_name}\n\n📱 {tao_password}\n🔗 {cps_link}",
        "✨ 好物安利 | {short_title}\n📌 券后 ¥{coupon_price}  |  已售 {sales_volume}+\n🏪 {shop_name}\n\n📱 {tao_password}\n🔗 {cps_link}",
    ]

    CONTENT_TEMPLATES = [
        "📦 今日种草 | {short_title}\n\n{description}\n\n💰 券后价：¥{coupon_price}（原价¥{price}）\n🏪 店铺：{shop_name}\n📈 月销 {sales_volume}+ | 好评如潮\n\n📱 {tao_password}\n🔗 {cps_link}\n\n#好物分享# #种草# #省钱#",
        "最近挖到一个好东西！{title}\n\n{description}\n\n关键是现在有券，券后才 ¥{coupon_price}，比原价便宜了 ¥{coupon_amount}！{shop_name} 的爆款，月销 {sales_volume}+，品质有保障。\n\n📱 {tao_password}\n🔗 {cps_link}",
    ]

    def __init__(self, style: str = "discount"):
        self.style = style

    def generate(self, product: dict) -> str:
        if self.style == "content":
            return self._generate_content(product)
        return self._generate_discount(product)

    def _generate_discount(self, product: dict) -> str:
        import random
        template = random.choice(self.DISCOUNT_TEMPLATES)
        tao_pwd = product.get("tao_password", "")
        cps = product.get("cps_link", "")
        return template.format(
            title=product.get("title", ""),
            short_title=product.get("short_title") or product.get("title", ""),
            price=product.get("price", 0),
            coupon_price=product.get("coupon_price", 0),
            coupon_amount=product.get("coupon_amount", 0),
            commission=product.get("commission", 0),
            sales_volume=product.get("sales_volume", 0),
            shop_name=product.get("shop_name", ""),
            tao_password=tao_pwd,
            cps_link=cps,
        )

    def _generate_content(self, product: dict) -> str:
        import random
        template = random.choice(self.CONTENT_TEMPLATES)
        desc = product.get("description", "")
        if len(desc) > 200:
            desc = desc[:200] + "..."
        tao_pwd = product.get("tao_password", "")
        cps = product.get("cps_link", "")
        return template.format(
            title=product.get("title", ""),
            short_title=product.get("short_title") or product.get("title", ""),
            description=desc,
            price=product.get("price", 0),
            coupon_price=product.get("coupon_price", 0),
            coupon_amount=product.get("coupon_amount", 0),
            commission=product.get("commission", 0),
            sales_volume=product.get("sales_volume", 0),
            shop_name=product.get("shop_name", ""),
            tao_password=tao_pwd,
            cps_link=cps,
        )

    async def generate_with_ai(self, product: dict) -> str:
        """使用AI生成内容型文案（需要配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY）"""
        prompt = self._build_ai_prompt(product)

        if settings.ANTHROPIC_API_KEY and "your_anthropic" not in settings.ANTHROPIC_API_KEY:
            return await self._call_claude(prompt)
        elif settings.DEEPSEEK_API_KEY:
            return await self._call_deepseek(prompt)
        elif settings.OPENAI_API_KEY and "your_openai" not in settings.OPENAI_API_KEY:
            return await self._call_openai(prompt)

        logger.warning("未配置AI API Key，回退到模板生成")
        return self.generate(product)

    def _build_ai_prompt(self, product: dict) -> str:
        return f"""你是一个专业的带货博主。请为以下商品写一段微博推广文案，140字以内，口语化、有吸引力。
商品信息：
- 标题：{product.get('title')}
- 券后价：¥{product.get('coupon_price')}（原价¥{product.get('price')}）
- 优惠券：满减¥{product.get('coupon_amount')}
- 销量：{product.get('sales_volume')}+
- 店铺：{product.get('shop_name')}
- 佣金：¥{product.get('commission')}
请直接输出文案，不要包含任何前缀说明。"""

    async def _call_claude(self, prompt: str) -> str:
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
                        "model": settings.ANTHROPIC_MODEL,
                        "max_tokens": 300,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["content"][0]["text"].strip()
        except Exception as e:
            logger.exception(f"AI文案生成失败: {e}")
            return self.generate(product={})

    async def _call_openai(self, prompt: str) -> str:
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
                        "max_tokens": 300,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.exception(f"AI文案生成失败: {e}")
            return self.generate(product={})

    async def _call_deepseek(self, prompt: str) -> str:
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
                        "max_tokens": 300,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.exception(f"DeepSeek文案生成失败: {e}")
            return self.generate(product={})


class ImageProcessor:
    """图片处理 — 下载商品图片到本地"""

    @staticmethod
    async def download(url: str, filename: str) -> str | None:
        os.makedirs(settings.IMAGE_STORAGE_PATH, exist_ok=True)
        filepath = os.path.join(settings.IMAGE_STORAGE_PATH, filename)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
            logger.info(f"图片已下载: {filepath}")
            return filepath
        except Exception as e:
            logger.exception(f"图片下载失败 {url}: {e}")
            return None

    @staticmethod
    async def download_product_images(product: dict) -> list[str]:
        """下载商品主图和详情图"""
        paths = []
        main_url = product.get("image_url", "")
        if main_url:
            source_id = product.get("source_id", "unknown")
            ext = ".jpg"
            if ".png" in main_url:
                ext = ".png"
            elif ".webp" in main_url:
                ext = ".webp"
            filename = f"{source_id}_main{ext}"
            path = await ImageProcessor.download(main_url, filename)
            if path:
                paths.append(path)
        return paths
