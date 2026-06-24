from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FetchResult:
    success: bool
    products: list[dict]
    total_count: int = 0
    error_message: str = ""


class BaseFetcher(ABC):
    platform: str = ""

    @abstractmethod
    async def fetch_products(self, page: int = 1, page_size: int = 20) -> FetchResult:
        """获取商品列表"""
        ...

    @abstractmethod
    async def fetch_product_detail(self, source_id: str) -> dict | None:
        """获取商品详情"""
        ...

    async def fetch_high_value(self, min_sales: int = 100, min_commission: float = 5.0) -> FetchResult:
        """获取高价值商品（高销量 + 高佣金）"""
        result = await self.fetch_products(page=1, page_size=50)
        if not result.success:
            return result

        filtered = [
            p for p in result.products
            if p.get("sales_volume", 0) >= min_sales
            and p.get("commission", 0) >= min_commission
        ]

        import math
        for p in filtered:
            sales = max(p.get("sales_volume", 0), 1)
            sales_score = math.log10(sales) * 15
            commission_score = p.get("commission_rate", 0) * 0.8
            score = sales_score + commission_score
            p["score"] = round(min(score, 100), 2)

        filtered.sort(key=lambda p: p.get("score", 0), reverse=True)
        return FetchResult(success=True, products=filtered, total_count=len(filtered))
