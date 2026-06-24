from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleRead, ScheduleCreate, ScheduleUpdate
from app.services.scheduler import scheduler, shutdown_scheduler, load_schedules_from_db
from apscheduler.triggers.cron import CronTrigger

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


async def _refresh_jobs():
    """刷新定时任务"""
    scheduler.remove_all_jobs()
    await load_schedules_from_db()


@router.get("", response_model=list[ScheduleRead])
async def list_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).order_by(Schedule.id))
    return result.scalars().all()


@router.post("", response_model=ScheduleRead)
async def create_schedule(data: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    schedule = Schedule(**data.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    await _refresh_jobs()
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleRead)
async def get_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return schedule


@router.put("/{schedule_id}", response_model=ScheduleRead)
async def update_schedule(schedule_id: int, data: ScheduleUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(schedule, key, val)
    await db.commit()
    await db.refresh(schedule)
    await _refresh_jobs()
    return schedule


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    await db.delete(schedule)
    await db.commit()
    await _refresh_jobs()
    return {"ok": True}


@router.post("/{schedule_id}/run")
async def run_schedule_now(schedule_id: int, db: AsyncSession = Depends(get_db)):
    """手动触发一次执行（按任务类型分发，与自动调度行为一致）"""
    from app.services.scheduler import (
        execute_full_cycle, execute_fetch_products, execute_publish_post,
        execute_nurture_scan, execute_nurture_publish,
    )
    from app.services.techblog.service import execute_tech_publish
    from app.services.feishu.service import execute_feishu_publish

    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="定时任务不存在")

    task_type = schedule.task_type
    try:
        if task_type == "fetch_products":
            await execute_fetch_products(source_id=schedule.source_id)
        elif task_type == "publish_post":
            await execute_publish_post(schedule_id=schedule_id)
        elif task_type == "full_cycle":
            await execute_full_cycle(schedule_id=schedule_id)
        elif task_type == "nurture_scan":
            await execute_nurture_scan(schedule_id=schedule_id)
        elif task_type == "nurture_publish":
            await execute_nurture_publish(schedule_id=schedule_id)
        elif task_type == "tech_publish":
            await execute_tech_publish(account_id=schedule.account_id or 2)
        elif task_type == "feishu_publish":
            await execute_feishu_publish(account_id=schedule.account_id or 2)
        else:
            raise HTTPException(status_code=400, detail=f"未知任务类型: {task_type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"执行失败: {e}")

    return {"ok": True, "task_type": task_type}
