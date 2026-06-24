import json
import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from loguru import logger

from app.database import get_db
from app.models.product import Product
from app.models.account import Account
from app.models.post import Post
from app.services.content.copywriter import Copywriter, ImageProcessor
from app.services.publisher.weibo import WeiboPublisher
from app.services.fetcher.dataoke import DataokeFetcher

router = APIRouter(prefix="/api/publish", tags=["publish"])

# 也提供抓取触发接口
fetch_router = APIRouter(prefix="/api/fetch", tags=["fetch"])


@fetch_router.post("/trigger")
async def trigger_fetch(source_id: int | None = None):
    """手动触发商品抓取"""
    from app.services.scheduler import execute_fetch_products
    await execute_fetch_products(source_id=source_id)
    return {"ok": True, "message": "抓取完成，请查看商品列表"}


class PublishRequest(BaseModel):
    product_id: int
    account_id: int
    copy_style: str = Field(default="discount", description="文案风格: discount/content/ai")


class CopyPreviewRequest(BaseModel):
    product_id: int
    copy_style: str = Field(default="discount")


@router.post("/preview")
async def preview_copy(req: CopyPreviewRequest, db: AsyncSession = Depends(get_db)):
    """预览生成的推广文案"""
    result = await db.execute(select(Product).where(Product.id == req.product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    product_dict = {
        "title": product.title,
        "short_title": product.short_title or product.title,
        "price": product.price,
        "coupon_price": product.coupon_price,
        "coupon_amount": product.coupon_amount,
        "commission": product.commission,
        "commission_rate": product.commission_rate,
        "sales_volume": product.sales_volume,
        "shop_name": product.shop_name,
        "coupon_link": product.coupon_link,
        "cps_link": product.cps_link or "",
        "tao_password": product.tao_password or "",
        "description": product.description,
    }

    copywriter = Copywriter(style=req.copy_style)
    content = copywriter.generate(product_dict)

    return {
        "product_id": req.product_id,
        "copy_style": req.copy_style,
        "content": content,
        "product": {
            "title": product.title,
            "image_url": product.image_url,
            "image_local": product.image_local,
        },
    }


@router.post("")
async def publish_product(req: PublishRequest, db: AsyncSession = Depends(get_db)):
    """手动发布商品到指定平台"""
    # 查商品
    product_result = await db.execute(select(Product).where(Product.id == req.product_id))
    product = product_result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # 查账号
    account_result = await db.execute(select(Account).where(Account.id == req.account_id))
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")

    product_dict = {
        "title": product.title,
        "short_title": product.short_title or product.title,
        "price": product.price,
        "coupon_price": product.coupon_price,
        "coupon_amount": product.coupon_amount,
        "commission": product.commission,
        "commission_rate": product.commission_rate,
        "sales_volume": product.sales_volume,
        "shop_name": product.shop_name,
        "coupon_link": product.coupon_link,
        "cps_link": product.cps_link or "",
        "tao_password": product.tao_password or "",
        "description": product.description,
    }

    # 生成文案
    if req.copy_style == "ai":
        copywriter = Copywriter(style="content")
        content = await copywriter.generate_with_ai(product_dict)
    else:
        copywriter = Copywriter(style=req.copy_style)
        content = copywriter.generate(product_dict)

    # 准备图片
    images = []
    if product.image_local:
        images.append(product.image_local)
    elif product.image_url:
        # 尝试下载
        filename = f"{product.source}_{product.source_id}_main.jpg"
        path = await ImageProcessor.download(product.image_url, filename)
        if path:
            images.append(path)
            product.image_local = path
            await db.commit()

    # 发布 — 优先使用网页 cookies，否则用 API token
    if account.cookies:
        from app.services.publisher.weibo_web import WebWeiboPublisher
        publisher = WebWeiboPublisher(cookies_json=account.cookies, headless=False)
        token_valid = await publisher.check_token()
        if not token_valid:
            raise HTTPException(status_code=400, detail="微博 cookies 已过期，请重新扫码登录")
        result = await publisher.publish(content, images)
    elif account.access_token:
        publisher = WeiboPublisher(access_token=account.access_token)
        token_valid = await publisher.check_token()
        if not token_valid:
            raise HTTPException(status_code=400, detail="微博 access_token 无效或已过期")
        result = await publisher.publish(content, images)
    else:
        raise HTTPException(status_code=400, detail="微博账号未登录，请先扫码登录")

    # 记录发布
    post = Post(
        product_id=product.id,
        account_id=account.id,
        platform=account.platform,
        content=content,
        images=json.dumps(images, ensure_ascii=False),
        external_id=result.external_id,
        external_url=result.external_url,
        status="published" if result.success else "failed",
        error_message=result.error_message,
        published_at=datetime.datetime.utcnow() if result.success else None,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    if result.success:
        logger.info(f"手动发布成功: {product.short_title or product.title} → {result.external_url}")
        return {
            "success": True,
            "post_id": post.id,
            "external_url": result.external_url,
            "content": content,
        }
    else:
        raise HTTPException(status_code=500, detail=f"发布失败: {result.error_message}")


@router.get("/posts")
async def list_posts(page: int = 1, page_size: int = 20, db: AsyncSession = Depends(get_db)):
    """查看发布历史"""
    result = await db.execute(
        select(Post).order_by(Post.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    posts = result.scalars().all()
    return [
        {
            "id": p.id,
            "product_id": p.product_id,
            "account_id": p.account_id,
            "platform": p.platform,
            "content": p.content,
            "status": p.status,
            "external_url": p.external_url,
            "error_message": p.error_message,
            "published_at": p.published_at.isoformat() if p.published_at else None,
            "created_at": p.created_at.isoformat(),
        }
        for p in posts
    ]


# ====== 收益/订单查询 ======

earnings_router = APIRouter(prefix="/api/earnings", tags=["earnings"])


@earnings_router.get("/orders")
async def get_earnings_orders(period: str = "today"):
    """查询订单收益"""
    from datetime import datetime, timedelta

    now = datetime.utcnow()

    if period == "today":
        start = now.replace(hour=0, minute=0, second=0)
        end = now
    elif period == "yesterday":
        end = now.replace(hour=0, minute=0, second=0)
        start = end - timedelta(days=1)
    elif period == "7days":
        start = now - timedelta(days=7)
        end = now
    elif period == "30days":
        start = now - timedelta(days=30)
        end = now
    else:
        start = now.replace(hour=0, minute=0, second=0)
        end = now

    # 大淘客要求间隔不超过3小时，需要分批查询
    fetcher = DataokeFetcher()
    all_orders = []
    total_estimated = 0.0
    total_settled = 0.0

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(hours=2, minutes=55), end)
        result = await fetcher.get_order_details(
            start_time=chunk_start.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
            query_type=1,
            page_size=100,
        )
        if result.get("success"):
            all_orders.extend(result["orders"])
            total_estimated += result["summary"]["total_estimated"]
            total_settled += result["summary"]["total_settled"]
        chunk_start = chunk_end

    return {
        "success": True,
        "orders": all_orders,
        "summary": {
            "total_estimated": round(total_estimated, 2),
            "total_settled": round(total_settled, 2),
            "order_count": len(all_orders),
        },
    }
