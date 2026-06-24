#!/usr/bin/env python3
"""手动触发商品抓取"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from app.services.fetcher.dataoke import DataokeFetcher
from app.services.scheduler import _save_products


async def main():
    fetcher = DataokeFetcher()
    print("正在从大淘客抓取高价值商品...")
    result = await fetcher.fetch_high_value(min_sales=50, min_commission=3.0)
    if result.success and result.products:
        await _save_products(result.products)
        print(f"成功抓取 {len(result.products)} 件商品")
        for p in result.products[:5]:
            print(f"  - {p.get('short_title') or p.get('title', '')[:50]}")
            print(f"    券后 ¥{p.get('coupon_price')} | 佣金 ¥{p.get('commission')} | 评分 {p.get('score')}")
    else:
        print(f"抓取失败或无结果: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
