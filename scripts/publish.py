#!/usr/bin/env python3
"""手动发布商品到微博"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from app.database import async_session
from app.models.product import Product
from app.models.account import Account
from app.services.content.copywriter import Copywriter
from app.services.publisher.weibo import WeiboPublisher

from sqlalchemy import select


async def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/publish.py <product_id> [account_id] [style]")
        print("  style: discount (默认) | content | ai")
        sys.exit(1)

    product_id = int(sys.argv[1])
    account_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    style = sys.argv[3] if len(sys.argv) > 3 else "discount"

    async with async_session() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalar_one_or_none()

        if not product:
            print(f"商品 {product_id} 不存在")
            return
        if not account:
            print(f"账号 {account_id} 不存在")
            return

        product_dict = {
            "title": product.title,
            "short_title": product.short_title or product.title,
            "price": product.price,
            "coupon_price": product.coupon_price,
            "coupon_amount": product.coupon_amount,
            "commission": product.commission,
            "sales_volume": product.sales_volume,
            "shop_name": product.shop_name,
            "coupon_link": product.coupon_link,
            "description": product.description,
        }

        copywriter = Copywriter(style=style)
        content = copywriter.generate(product_dict)

        print("=" * 50)
        print(content)
        print("=" * 50)

        confirm = input("\n确认发布到微博？[y/N] ")
        if confirm.lower() != 'y':
            print("已取消")
            return

        publisher = WeiboPublisher(access_token=account.access_token)
        images = [product.image_local] if product.image_local else []
        result = await publisher.publish(content, images)

        if result.success:
            print(f"发布成功: {result.external_url}")
        else:
            print(f"发布失败: {result.error_message}")


if __name__ == "__main__":
    asyncio.run(main())
