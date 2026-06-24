from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.product import Product
from app.schemas.product import ProductRead, ProductUpdate
from app.services.fetcher.dataoke import DataokeFetcher

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=list[ProductRead])
async def list_products(
    page: int = 1,
    page_size: int = 100,
    source: str | None = None,
    min_score: float | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Product)
    if source:
        query = query.where(Product.source == source)
    if min_score is not None:
        query = query.where(Product.score >= min_score)
    if status:
        query = query.where(Product.status == status)
    else:
        query = query.where(Product.status != "archived")
    query = query.order_by(Product.score.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product


@router.put("/{product_id}", response_model=ProductRead)
async def update_product(product_id: int, data: ProductUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(product, key, val)
    await db.commit()
    await db.refresh(product)
    return product


@router.post("/{product_id}/convert")
async def convert_product_link(product_id: int, db: AsyncSession = Depends(get_db)):
    """为单个商品手动转链"""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if not product.goods_id:
        raise HTTPException(status_code=400, detail="商品缺少淘宝goods_id，无法转链。请通过大淘客抓取获取完整商品信息。")

    fetcher = DataokeFetcher()
    link_data = await fetcher.get_privilege_link(product.goods_id)

    if not link_data:
        raise HTTPException(status_code=500, detail="转链失败，请检查API配置")

    product.cps_link = link_data["cps_link"]
    product.tao_password = link_data["tao_password"]
    await db.commit()
    await db.refresh(product)

    return {
        "ok": True,
        "cps_link": product.cps_link,
        "tao_password": product.tao_password,
    }
