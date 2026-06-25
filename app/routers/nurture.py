"""养号管理 — API 路由 + 管理后台页面"""
import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update

from app.database import get_db
from app.templates_env import to_local, today_start_utc
from app.models.account import Account
from app.models.nurture import NurtureTopic, NurtureRecord, NurtureBlockedTopic
from app.models.schedule import Schedule
from app.schemas.nurture import (
    NurtureTopicRead, NurtureRecordRead,
    NurtureScheduleCreate, NurtureScheduleUpdate, NurtureScheduleRead,
)
from app.services.nurture.nurture_service import (
    execute_nurture_scan, execute_nurture_publish, run_nurture_manual,
)
from app.services.scheduler import scheduler, load_schedules_from_db
from apscheduler.triggers.interval import IntervalTrigger
from app.templates_env import templates_env
from app.config import settings

# ====== 路由定义 ======
router = APIRouter(prefix="/api/nurture", tags=["nurture"])


def render_template(name: str, context: dict) -> HTMLResponse:
    template = templates_env.get_template(name)
    html = template.render(**context)
    return HTMLResponse(html)


# ====== 话题 API ======

@router.get("/topics", response_model=list[NurtureTopicRead])
async def list_topics(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NurtureTopic).where(NurtureTopic.is_suitable == True)
        .order_by(NurtureTopic.heat_score.desc()).limit(limit)
    )
    return result.scalars().all()


# ====== 记录 API ======

@router.get("/records")
async def list_records(page: int = 1, page_size: int = 20, db: AsyncSession = Depends(get_db)):
    """养号记录分页查询，含每日阅读量统计"""
    since = today_start_utc()

    # 总数
    total = (await db.execute(
        select(func.count(NurtureRecord.id))
    )).scalar() or 0

    # 每日阅读量
    daily_views = (await db.execute(
        select(func.coalesce(func.sum(NurtureRecord.views), 0)).where(
            NurtureRecord.created_at >= since,
        )
    )).scalar() or 0

    # 分页数据
    offset = (page - 1) * page_size
    result = await db.execute(
        select(NurtureRecord).order_by(NurtureRecord.created_at.desc())
        .offset(offset).limit(page_size)
    )
    items = result.scalars().all()

    total_pages = max(1, (total + page_size - 1) // page_size)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "daily_views": daily_views,
    }


# ====== 手动发布 ======

@router.post("/publish")
async def manual_publish(
    account_id: int,
    style: str = "sharp",
    enable_image: bool = True,
    simulate: bool = False,
):
    """手动触发一次养号发布"""
    result = await run_nurture_manual(
        account_id=account_id,
        style=style,
        enable_image=enable_image,
        simulate=simulate,
    )
    if not result.get("success") and not result.get("simulate"):
        raise HTTPException(status_code=400, detail=result.get("error", "未知错误"))
    return result


# ====== 立即扫描 ======

@router.post("/scan")
async def scan_now():
    await execute_nurture_scan()
    return {"ok": True}


@router.post("/engage")
async def trigger_engage(likes: int = 3, comments: int = 1):
    """手动触发自动互动（体育/数码/汽车领域点赞+评论）"""
    from app.services.nurture.engagement import run_engagement
    result = await run_engagement(
        likes=likes, comments=comments,
        categories=["体育", "数码", "汽车"],
    )
    return result


@router.post("/optimize-weights")
async def optimize_weights():
    """根据近期互动数据动态调整风格权重"""
    from app.services.nurture.style_optimizer import apply_weights_to_schedule
    weights = await apply_weights_to_schedule()
    return {"ok": True, "weights": weights}


@router.post("/update-views")
async def update_views():
    """手动刷新阅读量"""
    from app.services.nurture.view_scraper import update_all_account_views
    count = await update_all_account_views()
    return {"ok": True, "updated": count}


# ====== 全局不参与话题管理 ======

@router.get("/blocked-topics")
async def list_blocked_topics(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NurtureBlockedTopic).order_by(NurtureBlockedTopic.created_at.desc())
    )
    topics = result.scalars().all()
    return [{"id": t.id, "keyword": t.keyword, "reason": t.reason,
             "created_at": t.created_at.isoformat()} for t in topics]


@router.post("/blocked-topics")
async def add_blocked_topic(keyword: str, reason: str = "", db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(NurtureBlockedTopic).where(NurtureBlockedTopic.keyword == keyword)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="该关键词已存在")
    topic = NurtureBlockedTopic(keyword=keyword, reason=reason)
    db.add(topic)
    await db.commit()
    return {"ok": True, "id": topic.id}


@router.delete("/blocked-topics/{topic_id}")
async def delete_blocked_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NurtureBlockedTopic).where(NurtureBlockedTopic.id == topic_id)
    )
    topic = result.scalar_one_or_none()
    if not topic:
        raise HTTPException(status_code=404, detail="不存在")
    await db.delete(topic)
    await db.commit()
    return {"ok": True}


# ====== 养号配置（存在 Schedule 表中 task_type=nurture_publish）=====

def _schedule_to_read(s: Schedule, today_count: int = 0) -> dict:
    extra = json.loads(s.extra_data) if s.extra_data else {}
    return {
        "id": s.id,
        "name": s.name,
        "account_id": s.account_id,
        "interval_minutes": extra.get("interval_minutes", settings.NURTURE_DEFAULT_INTERVAL_MINUTES),
        "max_posts_per_day": extra.get("max_posts_per_day", settings.NURTURE_MAX_POSTS_PER_DAY),
        "content_style": extra.get("content_style", "sharp"),
        "filter_keywords": extra.get("filter_keywords", ""),
        "preferred_categories": extra.get("preferred_categories", ""),
        "enable_image": extra.get("enable_image", True),
        "active_start_hour": extra.get("active_start_hour", 7),
        "active_end_hour": extra.get("active_end_hour", 23),
        "is_active": s.is_active,
        "last_run_at": s.last_run_at,
        "today_post_count": today_count,
        "created_at": s.created_at,
    }


@router.get("/schedules")
async def list_nurture_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule).where(Schedule.task_type == "nurture_publish").order_by(Schedule.id)
    )
    schedules = result.scalars().all()

    # 为每个任务计算今日发布数
    since = today_start_utc()
    out = []
    for s in schedules:
        if s.account_id:
            count = (await db.execute(
                select(func.count(NurtureRecord.id)).where(
                    NurtureRecord.account_id == s.account_id,
                    NurtureRecord.status == "published",
                    NurtureRecord.created_at >= since,
                )
            )).scalar() or 0
        else:
            count = 0
        out.append(_schedule_to_read(s, today_count=count))
    return out


@router.post("/schedules")
async def create_nurture_schedule(data: NurtureScheduleCreate, db: AsyncSession = Depends(get_db)):
    if data.account_id and not await _check_account_unique(data.account_id, None, db):
        raise HTTPException(status_code=400, detail="该账号已被其他养号/科技任务占用，一个账号只能绑定一个任务")

    interval = data.interval_minutes
    cron = f"*/{interval} * * * *" if interval < 60 else f"0 */{interval // 60} * * *"

    extra = json.dumps({
        "interval_minutes": data.interval_minutes,
        "max_posts_per_day": data.max_posts_per_day,
        "content_style": data.content_style,
        "filter_keywords": data.filter_keywords,
        "preferred_categories": data.preferred_categories,
        "enable_image": data.enable_image,
        "active_start_hour": data.active_start_hour,
        "active_end_hour": data.active_end_hour,
    }, ensure_ascii=False)

    schedule = Schedule(
        name=data.name,
        task_type="nurture_publish",
        cron_expression=cron,
        account_id=data.account_id,
        extra_data=extra,
        is_active=data.is_active,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    # 注册到调度器
    await _refresh_nurture_jobs(db)
    return _schedule_to_read(schedule)


@router.put("/schedules/{schedule_id}")
async def update_nurture_schedule(
    schedule_id: int, data: NurtureScheduleUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.task_type == "nurture_publish")
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="养号任务不存在")

    # 更新 extra_data
    extra = json.loads(schedule.extra_data) if schedule.extra_data else {}
    field_map = {
        "interval_minutes": "interval_minutes",
        "max_posts_per_day": "max_posts_per_day",
        "content_style": "content_style",
        "filter_keywords": "filter_keywords",
        "preferred_categories": "preferred_categories",
        "enable_image": "enable_image",
        "active_start_hour": "active_start_hour",
        "active_end_hour": "active_end_hour",
    }
    for field, extra_key in field_map.items():
        val = getattr(data, field, None)
        if val is not None:
            extra[extra_key] = val
    schedule.extra_data = json.dumps(extra, ensure_ascii=False)

    # 更新间隔
    if data.interval_minutes is not None:
        interval = data.interval_minutes
        schedule.cron_expression = f"*/{interval} * * * *" if interval < 60 else f"0 */{interval // 60} * * *"

    for field in ("name", "account_id", "is_active"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(schedule, field, val)

    # 切账号时检查唯一性
    if data.account_id and not await _check_account_unique(data.account_id, schedule_id, db):
        raise HTTPException(status_code=400, detail="该账号已被其他养号/科技任务占用，一个账号只能绑定一个任务")

    await db.commit()
    await db.refresh(schedule)
    await _refresh_nurture_jobs(db)
    return _schedule_to_read(schedule)


@router.delete("/schedules/{schedule_id}")
async def delete_nurture_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.task_type == "nurture_publish")
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="养号任务不存在")
    await db.delete(schedule)
    await db.commit()
    await _refresh_nurture_jobs(db)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run")
async def run_nurture_now(schedule_id: int):
    await execute_nurture_publish(schedule_id, skip_delay=True)
    return {"ok": True}


@router.post("/tech/publish")
async def tech_publish_now(account_id: int = 1):
    """手动触发一次科技博主发布"""
    from app.services.techblog.service import execute_tech_publish
    result = await execute_tech_publish(account_id=account_id)
    return result


@router.post("/tech/twitter-login")
async def twitter_login():
    """打开浏览器让用户手动登录 Twitter，保存 Cookie"""
    from app.services.techblog.twitter import login_and_save_cookies
    result = await login_and_save_cookies()
    if not result:
        raise HTTPException(status_code=500, detail="登录失败或超时")
    return result


@router.get("/tech/twitter-status")
async def twitter_status():
    """检查 Twitter Cookie 是否存在"""
    import os
    cookie_file = os.path.join(os.path.dirname(__file__), "..", "services", "techblog", "twitter_cookies.json")
    exists = os.path.exists(os.path.abspath(cookie_file))
    return {"ok": exists}


@router.get("/tech/schedules")
async def list_tech_schedules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule).where(Schedule.task_type == "tech_publish").order_by(Schedule.id)
    )
    schedules = result.scalars().all()
    since = today_start_utc()
    out = []
    for s in schedules:
        cnt = (await db.execute(
            select(func.count(NurtureRecord.id)).where(
                NurtureRecord.account_id == (s.account_id or 1),
                NurtureRecord.topic_name == "AI科技日报",
                NurtureRecord.status == "published",
                NurtureRecord.created_at >= since,
            )
        )).scalar() or 0
        extra = json.loads(s.extra_data) if s.extra_data else {}
        out.append({
            "id": s.id,
            "name": s.name,
            "account_id": s.account_id,
            "interval_minutes": extra.get("interval_minutes", 60),
            "max_posts_per_day": extra.get("max_posts_per_day", 8),
            "is_active": s.is_active,
            "last_run_at": s.last_run_at,
            "today_post_count": cnt,
        })
    return out


async def _check_account_unique(account_id: int, exclude_schedule_id: int | None, db) -> bool:
    """检查账号是否已被其他活跃养号/科技任务占用"""
    result = await db.execute(
        select(Schedule).where(
            Schedule.account_id == account_id,
            Schedule.is_active == True,
            Schedule.task_type.in_(["nurture_publish", "tech_publish"]),
        )
    )
    for row in result.scalars().all():
        if row.id != exclude_schedule_id:
            return False  # 冲突了
    return True


@router.post("/tech/schedules")
async def create_tech_schedule(data: dict, db: AsyncSession = Depends(get_db)):
    account_id = data.get("account_id")
    if account_id and not await _check_account_unique(account_id, None, db):
        raise HTTPException(status_code=400, detail="该账号已被其他养号/科技任务占用，一个账号只能绑定一个任务")
    extra = {
        "interval_minutes": data.get("interval_minutes", 60),
        "max_posts_per_day": data.get("max_posts_per_day", 8),
    }
    s = Schedule(
        name=data.get("name", "AI科技日报"),
        task_type="tech_publish",
        cron_expression="0 * * * *",
        account_id=account_id,
        is_active=True,
        extra_data=json.dumps(extra),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    await _refresh_tech_jobs()
    return {"ok": True, "id": s.id}


@router.put("/tech/schedules/{schedule_id}")
async def update_tech_schedule(schedule_id: int, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="任务不存在")
    new_account_id = data.get("account_id", s.account_id)
    if new_account_id and not await _check_account_unique(new_account_id, schedule_id, db):
        raise HTTPException(status_code=400, detail="该账号已被其他养号/科技任务占用，一个账号只能绑定一个任务")
    if "name" in data:
        s.name = data["name"]
    if "account_id" in data:
        s.account_id = data["account_id"]
    if "is_active" in data:
        s.is_active = data["is_active"]
    extra = json.loads(s.extra_data) if s.extra_data else {}
    if "interval_minutes" in data:
        extra["interval_minutes"] = data["interval_minutes"]
    if "max_posts_per_day" in data:
        extra["max_posts_per_day"] = data["max_posts_per_day"]
    s.extra_data = json.dumps(extra)
    await db.commit()
    await _refresh_tech_jobs()
    return {"ok": True}


@router.delete("/tech/schedules/{schedule_id}")
async def delete_tech_schedule(schedule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="任务不存在")
    await db.delete(s)
    await db.commit()
    job_id = f"schedule_{schedule_id}"
    for job in scheduler.get_jobs():
        if job.id == job_id:
            job.remove()
    return {"ok": True}


# ====== 飞书AI资讯（feishu_publish）— 仅支持暂停/恢复 ======

class FeishuToggleRequest(BaseModel):
    is_active: bool


@router.put("/feishu/schedules/{schedule_id}")
async def toggle_feishu_schedule(
    schedule_id: int, data: FeishuToggleRequest, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Schedule).where(Schedule.id == schedule_id, Schedule.task_type == "feishu_publish")
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="飞书任务不存在")
    s.is_active = data.is_active
    await db.commit()
    await _refresh_nurture_jobs(db)
    return {"ok": True, "id": s.id, "is_active": s.is_active}


async def _refresh_nurture_jobs(db=None):
    """刷新养号相关的调度任务"""
    # 移除所有养号任务（按函数名字符串匹配，避免热重载后引用对不上）
    for job in scheduler.get_jobs():
        if job.func.__name__ in ("execute_nurture_publish", "execute_nurture_scan"):
            job.remove()

    # 重新加载
    async with async_session_factory() as session:
        result = await session.execute(
            select(Schedule).where(
                Schedule.task_type.in_(["nurture_publish", "nurture_scan"]),
                Schedule.is_active == True,
            )
        )
        schedules = result.scalars().all()

    for s in schedules:
        extra = json.loads(s.extra_data) if s.extra_data else {}
        interval = extra.get("interval_minutes", settings.NURTURE_DEFAULT_INTERVAL_MINUTES)

        # 根据上次执行时间计算下次触发时刻
        next_run = None
        if s.last_run_at:
            elapsed = (datetime.datetime.utcnow() - s.last_run_at).total_seconds()
            remaining = interval * 60 - elapsed
            if remaining > 0:
                next_run = datetime.datetime.now() + datetime.timedelta(seconds=remaining)
            else:
                next_run = datetime.datetime.now() + datetime.timedelta(seconds=5)
        else:
            next_run = datetime.datetime.now() + datetime.timedelta(seconds=10)

        scheduler.add_job(
            execute_nurture_publish if s.task_type == "nurture_publish" else execute_nurture_scan,
            trigger=IntervalTrigger(minutes=interval),
            id=f"schedule_{s.id}",
            kwargs={"schedule_id": s.id},
            replace_existing=True,
            next_run_time=next_run,
        )


from app.database import async_session as async_session_factory


async def _refresh_tech_jobs():
    """刷新科技博主调度任务"""
    for job in scheduler.get_jobs():
        if job.func.__name__ == "execute_tech_publish":
            job.remove()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Schedule).where(Schedule.task_type == "tech_publish", Schedule.is_active == True)
        )
        schedules = result.scalars().all()
    for s in schedules:
        extra = json.loads(s.extra_data) if s.extra_data else {}
        interval = extra.get("interval_minutes", 60)
        next_run = datetime.datetime.now() + datetime.timedelta(seconds=10)
        if s.last_run_at:
            remaining = interval * 60 - (datetime.datetime.utcnow() - s.last_run_at).total_seconds()
            next_run = datetime.datetime.now() + datetime.timedelta(seconds=max(remaining, 5))
        from app.services.techblog.service import execute_tech_publish
        scheduler.add_job(
            execute_tech_publish,
            trigger=IntervalTrigger(minutes=interval),
            id=f"schedule_{s.id}",
            kwargs={"account_id": s.account_id or 1},
            replace_existing=True,
            next_run_time=next_run,
        )


# ====== 管理后台页面 ======

admin = APIRouter(prefix="/admin", tags=["nurture_admin"])


@admin.get("/nurture")
async def nurture_page(request: Request, db: AsyncSession = Depends(get_db)):
    """自动养号主页"""
    schedules = await db.execute(
        select(Schedule).where(Schedule.task_type == "nurture_publish").order_by(Schedule.id)
    )
    schedules_list = schedules.scalars().all()

    accounts = (await db.execute(select(Account).order_by(Account.id))).scalars().all()

    scan_count = (await db.execute(select(func.count(NurtureTopic.id)))).scalar()
    since = today_start_utc()

    # 按账号统计今日发布数
    schedule_data = []
    total_today = 0
    for s in schedules_list:
        if s.account_id:
            cnt = (await db.execute(
                select(func.count(NurtureRecord.id)).where(
                    NurtureRecord.account_id == s.account_id,
                    NurtureRecord.status == "published",
                    NurtureRecord.created_at >= since,
                )
            )).scalar() or 0
            total_today += cnt
        else:
            cnt = 0
        schedule_data.append(_schedule_to_read(s, today_count=cnt))

    tech_today = (await db.execute(
        select(func.count(NurtureRecord.id)).where(
            NurtureRecord.topic_name == "AI科技日报",
            NurtureRecord.status == "published",
            NurtureRecord.created_at >= since,
        )
    )).scalar() or 0

    feishu_rows = (await db.execute(
        select(Schedule).where(Schedule.task_type == "feishu_publish").order_by(Schedule.id)
    )).scalars().all()
    feishu_today = (await db.execute(
        select(func.count(NurtureRecord.id)).where(
            NurtureRecord.topic_name == "飞书AI资讯",
            NurtureRecord.status == "published",
            NurtureRecord.created_at > since,
        )
    )).scalar() or 0
    feishu_schedules = [
        {"id": s.id, "name": s.name, "is_active": s.is_active, "last_run_at": s.last_run_at}
        for s in feishu_rows
    ]

    return render_template("nurture.html", {
        "request": request,
        "title": "自动养号",
        "schedules": schedule_data,
        "accounts": accounts,
        "scan_count": scan_count or 0,
        "today_posts": total_today,
        "default_interval": settings.NURTURE_DEFAULT_INTERVAL_MINUTES,
        "default_max_posts": settings.NURTURE_MAX_POSTS_PER_DAY,
        "tech_today": tech_today,
        "feishu_schedules": feishu_schedules,
        "feishu_today": feishu_today,
    })


@admin.get("/nurture/report")
async def nurture_report_page(request: Request):
    """效果分析报告页面"""
    return render_template("nurture_report.html", {
        "request": request,
        "title": "效果分析",
    })


@router.post("/report/generate")
async def generate_report_api(days: int = 1):
    """生成效果分析报告（异步）"""
    from app.services.nurture.analyzer import generate_report
    report = await generate_report(days=days)
    return {"ok": True, "report": report}


@admin.get("/nurture/records")
async def nurture_records_page(request: Request, db: AsyncSession = Depends(get_db)):
    """养号发布记录（服务端首屏渲染，后续翻页走 API）"""
    since = today_start_utc()

    # 每日阅读量
    daily_views = (await db.execute(
        select(func.coalesce(func.sum(NurtureRecord.views), 0)).where(
            NurtureRecord.created_at >= since,
        )
    )).scalar() or 0

    # 每日发布数
    daily_posts = (await db.execute(
        select(func.count(NurtureRecord.id)).where(
            NurtureRecord.created_at >= since,
        )
    )).scalar() or 0

    # 首屏 20 条
    result = await db.execute(
        select(NurtureRecord).order_by(NurtureRecord.created_at.desc()).limit(20)
    )
    records = result.scalars().all()

    # 总数
    total = (await db.execute(
        select(func.count(NurtureRecord.id))
    )).scalar() or 0
    total_pages = max(1, (total + 19) // 20)

    return render_template("nurture_records.html", {
        "request": request,
        "title": "养号记录",
        "records": records,
        "daily_views": daily_views,
        "daily_posts": daily_posts,
        "total": total,
        "total_pages": total_pages,
        "page": 1,
    })
