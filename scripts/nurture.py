#!/usr/bin/env python3
"""手动触发养号发布 — CLI 工具

用法:
  python scripts/nurture.py scan              # 扫描热搜
  python scripts/nurture.py publish <账户ID>  # 发布一条
  python scripts/nurture.py simulate <账户ID> # 模拟生成（不发布）
  python scripts/nurture.py topics            # 查看候选话题

示例:
  python scripts/nurture.py scan
  python scripts/nurture.py publish 1 --style humorous
  python scripts/nurture.py simulate 1 --style sharp
"""
import sys, os, asyncio, argparse

_proj_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _proj_dir)
os.chdir(_proj_dir)

from app.services.nurture.topic_scanner import TopicScanner
from app.services.nurture.content_writer import NurtureWriter
from app.services.nurture.image_generator import NurtureImageGenerator
from app.services.nurture.nurture_service import run_nurture_manual


async def cmd_scan(account_id: int | None = None):
    print("🔍 正在扫描微博实时热搜...")
    cookies = {}
    if account_id:
        from app.database import async_session
        from app.models.account import Account
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(select(Account).where(Account.id == account_id))
            account = result.scalar_one_or_none()
            if account and account.cookies:
                import json
                cookie_list = json.loads(account.cookies)
                cookies = {c.get("name", ""): c.get("value", "") for c in cookie_list}
                print(f"使用账号 #{account_id} ({account.name}) 的 Cookie")

    scanner = TopicScanner(cookies=cookies)
    topics = await scanner.scan()
    if not topics:
        print("未找到合适话题（可能需要用 --account 指定已登录的微博账号ID）")
        return
    print(f"\n过滤后 {len(topics)} 个可参与话题:\n")
    for t in topics[:15]:
        print(f"  {t.rank:>2}. [{t.category}] {t.name}  (热度:{t.heat})")


async def cmd_publish(account_id: int, style: str = "sharp", simulate: bool = False):
    label = "模拟" if simulate else "发布"
    print(f"✍️ 正在{label}养号微博... (账号#{account_id}, 风格:{style})")

    result = await run_nurture_manual(
        account_id=account_id,
        style=style,
        enable_image=True,
        simulate=simulate,
    )

    if not result.get("success") and not result.get("simulate"):
        print(f"❌ 失败: {result.get('error', '未知错误')}")
        return

    print(f"\n{'='*50}")
    print(f"话题: {result['topic']}")
    print(f"{'='*50}")
    print(result['content'])
    print(f"{'='*50}")
    if result.get("image"):
        print(f"配图: {result['image']}")
    if result.get("url") and not result.get("simulate"):
        print(f"链接: {result['url']}")
    print(f"{'='*50}")


async def cmd_topics():
    from app.database import async_session
    from app.models.nurture import NurtureTopic
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(NurtureTopic).where(NurtureTopic.is_suitable == True)
            .order_by(NurtureTopic.heat_score.desc()).limit(30)
        )
        topics = result.scalars().all()

    if not topics:
        print("暂无扫描到的话题，请先执行 scan")
        return

    print(f"\n最近扫描的 {len(topics)} 个候选话题:\n")
    for t in topics:
        print(f"  [{t.category}] {t.topic_name} (热度:{t.heat_score}, 排名:{t.rank})")


async def cmd_test_writer(topic: str, style: str = "sharp"):
    """测试文案生成"""
    writer = NurtureWriter(style=style)
    content = await writer.generate(topic)
    print(f"\n话题: {topic}")
    print(f"风格: {style}")
    print(f"{'='*50}")
    print(content)
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description="养号工具")
    sub = parser.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="扫描热搜话题")
    p_scan.add_argument("--account", type=int, default=None, help="微博账号ID（用于Cookie）")
    sub.add_parser("topics", help="查看候选话题")

    p_pub = sub.add_parser("publish", help="发布一条养号微博")
    p_pub.add_argument("account_id", type=int, help="微博账号ID")
    p_pub.add_argument("--style", default="sharp",
                       choices=["knowledge", "warm"],
                       help="文案风格 (默认: sharp)")

    p_sim = sub.add_parser("simulate", help="模拟生成（不实际发布）")
    p_sim.add_argument("account_id", type=int, help="微博账号ID")
    p_sim.add_argument("--style", default="sharp",
                       choices=["knowledge", "warm"],
                       help="文案风格 (默认: sharp)")

    sub.add_parser("update-views", help="刷新阅读量")

    sub.add_parser("optimize-weights", help="根据互动数据动态调整风格权重")

    sub.add_parser("tech", help="科技博主：聚合 AI 资讯并发布")

    sub.add_parser("twitter-login", help="登录 Twitter 账号并保存 Cookie")

    p_eng = sub.add_parser("engage", help="自动互动（点赞+评论）模拟真人养号")
    p_eng.add_argument("--likes", type=int, default=4, help="点赞数 (默认4)")
    p_eng.add_argument("--comments", type=int, default=2, help="评论数 (默认2)")

    p_rpt = sub.add_parser("report", help="生成效果分析报告")
    p_rpt.add_argument("--days", type=int, default=1, help="统计天数 (默认1天)")

    p_test = sub.add_parser("test-writer", help="测试文案生成")
    p_test.add_argument("topic", type=str, help="话题内容")
    p_test.add_argument("--style", default="sharp",
                        choices=["knowledge", "warm"],
                        help="文案风格 (默认: sharp)")

    args = parser.parse_args()

    if args.cmd == "scan":
        asyncio.run(cmd_scan(account_id=getattr(args, 'account', None)))
    elif args.cmd == "topics":
        asyncio.run(cmd_topics())
    elif args.cmd == "publish":
        asyncio.run(cmd_publish(args.account_id, args.style))
    elif args.cmd == "simulate":
        asyncio.run(cmd_publish(args.account_id, args.style, simulate=True))
    elif args.cmd == "test-writer":
        asyncio.run(cmd_test_writer(args.topic, args.style))
    elif args.cmd == "update-views":
        from app.services.nurture.view_scraper import update_views
        count = asyncio.run(update_views(account_id=1))
        print(f"阅读量更新完成，共 {count} 条记录")
    elif args.cmd == "optimize-weights":
        from app.services.nurture.style_optimizer import apply_weights_to_schedule
        asyncio.run(apply_weights_to_schedule())
    elif args.cmd == "tech":
        from app.services.techblog.service import execute_tech_publish
        result = asyncio.run(execute_tech_publish())
        print(f"OK: {result.get('success')} {result.get('content','')[:60]}")
    elif args.cmd == "twitter-login":
        from app.services.techblog.twitter import login_and_save_cookies
        result = asyncio.run(login_and_save_cookies())
        if result and result.get("ok"):
            print(f"✅ Twitter 登录成功！Cookie 已保存 ({result.get('cookie_count', 0)} 条)")
        else:
            print("❌ Twitter 登录失败")
    elif args.cmd == "engage":
        from app.services.nurture.engagement import run_engagement
        result = asyncio.run(run_engagement(likes=args.likes, comments=args.comments))
        print(f"点赞 {result.get('liked', 0)}  评论 {result.get('commented', 0)}")
    elif args.cmd == "report":
        from app.services.nurture.analyzer import generate_report
        report = asyncio.run(generate_report(days=args.days))
        print(report)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
