from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.source import Source
from app.schemas.source import SourceRead, SourceCreate, SourceUpdate

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("", response_model=list[SourceRead])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).order_by(Source.id))
    return result.scalars().all()


@router.post("", response_model=SourceRead)
async def create_source(data: SourceCreate, db: AsyncSession = Depends(get_db)):
    source = Source(**data.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.get("/{source_id}", response_model=SourceRead)
async def get_source(source_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="商品源不存在")
    return source


@router.put("/{source_id}", response_model=SourceRead)
async def update_source(source_id: int, data: SourceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="商品源不存在")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(source, key, val)
    await db.commit()
    await db.refresh(source)
    return source


@router.delete("/{source_id}")
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="商品源不存在")
    await db.delete(source)
    await db.commit()
    return {"ok": True}
