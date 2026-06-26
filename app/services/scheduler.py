import datetime
import json

from sqlalchemy import select, update, text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.database import async_session
from app.models.schedule import Schedule
from app.models.product import Product
from app.models.post import Post
from app.models.account import Account
from app.services.fetcher.dataoke import DataokeFetcher
from app.services.content.copywriter import Copywriter
from app.services.publisher.weibo import WeiboPublisher
from app.services.nurture.nurture_service import execute_nurture_scan, execute_nurture_publish
from app.services.techblog.service import execute_tech_publish
from app.services.feishu.service import execute_feishu_publish
from app.config import settings


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


async def _load_schedule_config(task_id: int) -> Schedule | None:
    async with async_session() as session:
        result = await session.execute(select(Schedule).where(Schedule.id == task_id))
        return result.scalar_one_or_none()


async def _save_products(products: list[dict]):
    from app.services.content.copywriter import ImageProcessor

    async with async_session() as session:
        for p in products:
            existing = await session.execute(
                text("SELECT id FROM products WHERE source = :source AND source_id = :source_id"),
                {"source": p["source"], "source_id": p["source_id"]},
            )
            if existing.scalar():
                continue
            product = Product(**p)
            # 异步下载主图
            image_url = p.get("image_url", "")
            if image_url:
                filename = f"{p.get('source', '')}_{p.get('source_id', '')}_main.jpg"
                local_path = await ImageProcessor.download(image_url, filename)
                if local_path:
                    product.image_local = local_path
            session.add(product)
        await session.commit()


async def _create_post_record(
    product_id: int, account_id: int, platform: str,
    content: str, images: list[str], status: str,
    external_id: str = "", external_url: str = "",
    error_message: str = "",
):
    async with async_session() as session:
        post = Post(
            product_id=product_id,
            account_id=account_id,
            platform=platform,
            content=content,
            images=json.dumps(images, ensure_ascii=False),
            external_id=external_id,
            external_url=external_url,
            status=status,
            error_message=error_message,
        )
        session.add(post)
        await session.commit()


async def execute_fetch_products(source_id: int | None = None):
    """执行商品抓取任务：抓取 → 保存 → 转链"""
    logger.info("开始执行商品抓取任务")
    fetcher = DataokeFetcher()
    result = await fetcher.fetch_high_value(min_sales=50, min_commission=3.0)
    if result.success and result.products:
        # 转链
        logger.info(f"正在为 {len(result.products)} 件商品生成CPS链接...")
        await fetcher.batch_convert_links(result.products)
        # 保存（含 cps_link / tao_password）
        await _save_products(result.products)
        logger.info(f"成功抓取并转链 {len(result.products)} 件商品")
    else:
        logger.warning(f"商品抓取无结果: {result.error_message}")


async def execute_publish_post(schedule_id: int):
    """执行发布任务：刷新产品 → 去重 → 选取 → 生成文案 → 发布"""
    config = await _load_schedule_config(schedule_id)
    if not config or not config.account_id:
        logger.error(f"定时任务{schedule_id}配置无效")
        return

    async with async_session() as session:
        # 1. 发前刷新商品库
        if config.refresh_before_post:
            logger.info("发前刷新商品库...")
            fetcher = DataokeFetcher()
            fetch_result = await fetcher.fetch_high_value(min_sales=50, min_commission=3.0)
            if fetch_result.success and fetch_result.products:
                await fetcher.batch_convert_links(fetch_result.products)
                await _save_products(fetch_result.products)
                logger.info(f"刷新完成: 新增 {len(fetch_result.products)} 件")

        # 2. 选取未发布过的高分商品
        posted_ids = (
            await session.execute(
                select(Post.product_id).where(
                    Post.status == "published",
                    Post.account_id == config.account_id,
                )
            )
        ).scalars().all()

        query = select(Product).where(
            Product.status == "active",
            Product.cps_link != "",
        )
        if posted_ids:
            query = query.where(Product.id.notin_(posted_ids))

        query = query.order_by(Product.score.desc()).limit(config.max_products_per_run)
        products_result = await session.execute(query)
        products = products_result.scalars().all()

        if not products:
            logger.warning("没有可发布的商品（全部已发过），重置去重")
            products_result = await session.execute(
                select(Product)
                .where(Product.status == "active", Product.cps_link != "")
                .order_by(Product.score.desc())
                .limit(config.max_products_per_run)
            )
            products = products_result.scalars().all()

        if not products:
            logger.error("商品库为空，无法发布")
            return

        # 3. 获取账号
        account_result = await session.execute(
            select(Account).where(Account.id == config.account_id)
        )
        account = account_result.scalar_one_or_none()
        if not account:
            logger.error(f"账号{config.account_id}不存在")
            return

        # 4. 逐商品生成文案并发布（publisher 实例在循环外共享，避免重复启动 Playwright）
        from app.services.publisher.weibo_web import WebWeiboPublisher
        publisher = WebWeiboPublisher(cookies_json=account.cookies, headless=True)

        for product in products:
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
                "cps_link": product.cps_link or "",
                "tao_password": product.tao_password or "",
                "description": product.description or "",
            }

            copywriter = Copywriter(style=config.copy_style)
            content = copywriter.generate(product_dict)
            images = [product.image_local] if product.image_local else []

            logger.info(f"发布: {product.short_title[:40]}")

            # publisher 已在循环外创建，此处直接调用
            result = await publisher.publish(content, images)

            await _create_post_record(
                product_id=product.id,
                account_id=account.id,
                platform="weibo",
                content=content,
                images=images,
                status="published" if result.success else "failed",
                external_id=result.external_id,
                external_url=result.external_url,
                error_message=result.error_message,
            )

            if result.success:
                logger.info(f"发布成功: {product.short_title or product.title} → {result.external_url}")
            else:
                logger.error(f"发布失败: {result.error_message}")

        # 5. 更新任务状态
        await session.execute(
            update(Schedule)
            .where(Schedule.id == schedule_id)
            .values(
                last_run_at=datetime.datetime.utcnow(),
                last_posted_product_id=products[0].id if products else None,
            )
        )
        await session.commit()

    logger.info(f"定时发布任务[{config.name}]完成，发布{len(products)}条")


async def execute_full_cycle(schedule_id: int):
    """执行完整周期：强制抓取最新 → 发布"""
    config = await _load_schedule_config(schedule_id)
    if not config:
        return

    await execute_fetch_products(source_id=config.source_id)
    await execute_publish_post(schedule_id)


async def load_schedules_from_db():
    """从数据库加载所有启用的定时任务"""
    async with async_session() as session:
        # 自动迁移：修复飞书补发 cron 从 10-23 缩小到 10-11，避免深夜发帖
        migrate = await session.execute(
            select(Schedule).where(
                Schedule.id == 8,
                Schedule.task_type == "feishu_publish",
                Schedule.cron_expression == "0 10-23 * * *",
            )
        )
        s8 = migrate.scalar_one_or_none()
        if s8:
            s8.cron_expression = "0 10-11 * * *"
            await session.commit()
            logger.info("[迁移] schedule #8 cron 已自动修复: 0 10-23 → 0 10-11")

        result = await session.execute(
            select(Schedule).where(Schedule.is_active == True)
        )
        schedules = result.scalars().all()

        for s in schedules:
            job_id = f"schedule_{s.id}"

            if s.task_type == "fetch_products":
                func = execute_fetch_products
                kwargs = {"source_id": s.source_id}
            elif s.task_type == "publish_post":
                func = execute_publish_post
                kwargs = {"schedule_id": s.id}
            elif s.task_type == "full_cycle":
                func = execute_full_cycle
                kwargs = {"schedule_id": s.id}
            elif s.task_type == "nurture_scan":
                func = execute_nurture_scan
                kwargs = {"schedule_id": s.id}
            elif s.task_type == "nurture_publish":
                func = execute_nurture_publish
                kwargs = {"schedule_id": s.id}
            elif s.task_type == "tech_publish":
                func = execute_tech_publish
                kwargs = {"account_id": s.account_id or 2}
            elif s.task_type == "feishu_publish":
                func = execute_feishu_publish
                kwargs = {"account_id": s.account_id or 2}
            else:
                continue

            # 养号/科技博主任务使用间隔触发器，带货任务使用 cron 触发器
            if s.task_type and (s.task_type.startswith("nurture_") or s.task_type.startswith("tech_")):
                extra = json.loads(s.extra_data) if s.extra_data else {}
                interval = extra.get("interval_minutes", settings.NURTURE_DEFAULT_INTERVAL_MINUTES)

                # 根据上次执行时间计算下次触发时刻，避免重启后重置计时
                next_run = None
                if s.last_run_at:
                    elapsed = (datetime.datetime.utcnow() - s.last_run_at).total_seconds()
                    remaining = interval * 60 - elapsed
                    if remaining > 0:
                        next_run = datetime.datetime.now() + datetime.timedelta(seconds=remaining)
                        logger.info(f"  [{s.name}] 距上次执行 {elapsed:.0f}s，{remaining:.0f}s 后触发")
                    else:
                        logger.info(f"  [{s.name}] 距上次执行 {elapsed:.0f}s，已超时，5s 后立即触发")
                        next_run = datetime.datetime.now() + datetime.timedelta(seconds=5)
                else:
                    next_run = datetime.datetime.now() + datetime.timedelta(seconds=10)

                scheduler.add_job(
                    func,
                    trigger=IntervalTrigger(minutes=interval),
                    id=job_id,
                    kwargs=kwargs,
                    replace_existing=True,
                    next_run_time=next_run,
                )
            else:
                scheduler.add_job(
                    func,
                    trigger=CronTrigger.from_crontab(s.cron_expression),
                    id=job_id,
                    kwargs=kwargs,
                    replace_existing=True,
                )
            logger.info(f"加载定时任务 [{s.name}]: {s.cron_expression}")

    logger.info(f"已加载 {len(schedules)} 个定时任务")


def start_scheduler():
    scheduler.start()
    logger.info("任务调度器已启动")


def shutdown_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("任务调度器已关闭")
