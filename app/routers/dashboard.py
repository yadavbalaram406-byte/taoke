from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.product import Product
from app.models.source import Source
from app.models.account import Account
from app.models.schedule import Schedule
from app.models.post import Post
from app.templates_env import templates_env

router = APIRouter(prefix="/admin", tags=["dashboard"])


def render_template(name: str, context: dict) -> HTMLResponse:
    template = templates_env.get_template(name)
    html = template.render(**context)
    return HTMLResponse(html)


@router.get("")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    product_count = (await db.execute(select(func.count(Product.id)))).scalar()
    source_count = (await db.execute(select(func.count(Source.id)))).scalar()
    account_count = (await db.execute(select(func.count(Account.id)))).scalar()
    schedule_count = (await db.execute(select(func.count(Schedule.id)))).scalar()

    recent_posts = (await db.execute(
        select(Post).order_by(Post.created_at.desc()).limit(10)
    )).scalars().all()

    return render_template("dashboard.html", {
        "request": request,
        "title": "仪表盘",
        "stats": {
            "products": product_count,
            "sources": source_count,
            "accounts": account_count,
            "schedules": schedule_count,
        },
        "recent_posts": recent_posts,
    })


@router.get("/products")
async def products_page(request: Request, db: AsyncSession = Depends(get_db)):
    products = (await db.execute(
        select(Product).order_by(Product.score.desc()).limit(100)
    )).scalars().all()
    sources = (await db.execute(select(Source).order_by(Source.id))).scalars().all()
    return render_template("products.html", {
        "request": request,
        "title": "商品管理",
        "products": products,
        "sources": sources,
    })


@router.get("/sources")
async def sources_page(request: Request, db: AsyncSession = Depends(get_db)):
    sources = (await db.execute(select(Source).order_by(Source.id))).scalars().all()
    return render_template("sources.html", {
        "request": request,
        "title": "商品源配置",
        "sources": sources,
    })


@router.get("/accounts")
async def accounts_page(request: Request, db: AsyncSession = Depends(get_db)):
    accounts = (await db.execute(select(Account).order_by(Account.id))).scalars().all()
    return render_template("accounts.html", {
        "request": request,
        "title": "账号管理",
        "accounts": accounts,
    })


@router.get("/schedules")
async def schedules_page(request: Request, db: AsyncSession = Depends(get_db)):
    schedules = (await db.execute(select(Schedule).order_by(Schedule.id))).scalars().all()
    sources = (await db.execute(select(Source).order_by(Source.id))).scalars().all()
    accounts = (await db.execute(select(Account).order_by(Account.id))).scalars().all()
    return render_template("schedules.html", {
        "request": request,
        "title": "定时任务",
        "schedules": schedules,
        "sources": sources,
        "accounts": accounts,
    })


@router.get("/earnings")
async def earnings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """收益看板页面 — 默认空数据，前端异步加载"""
    return render_template("earnings.html", {
        "request": request,
        "title": "收益看板",
        "summary": {"estimated": 0, "settled": 0, "order_count": 0},
        "orders": [],
    })


@router.get("/posts")
async def posts_page(request: Request, db: AsyncSession = Depends(get_db)):
    """微博发布记录"""
    posts = (await db.execute(
        select(Post).order_by(Post.created_at.desc()).limit(50)
    )).scalars().all()
    return render_template("posts.html", {
        "request": request,
        "title": "发布记录",
        "posts": posts,
    })
