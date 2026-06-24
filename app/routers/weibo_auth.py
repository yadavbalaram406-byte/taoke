from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from loguru import logger

from app.database import async_session
from app.models.account import Account
from app.services.publisher.weibo import WeiboPublisher

router = APIRouter(prefix="/api/weibo", tags=["weibo"])


@router.get("/auth/url")
async def get_auth_url():
    """获取微博 OAuth2 授权 URL"""
    url = WeiboPublisher.get_auth_url()
    return {"url": url}


@router.get("/callback")
async def oauth_callback(code: str | None = None):
    """微博 OAuth2 回调：用 code 换 access_token，自动创建账号"""
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 code")

    token_data = await WeiboPublisher.exchange_code(code)
    if not token_data:
        raise HTTPException(status_code=400, detail="换取 access_token 失败，请检查 App Key / Secret 或重新授权")

    async with async_session() as session:
        existing = await session.execute(
            select(Account).where(
                Account.platform == "weibo",
                Account.uid == token_data["uid"],
            )
        )
        account = existing.scalar_one_or_none()

        if account:
            account.access_token = token_data["access_token"]
            account.token_expires_at = None
            account.is_active = True
            await session.commit()
            logger.info(f"已更新微博账号 {account.name} 的 token")
            uid = str(account.id)
        else:
            account = Account(
                platform="weibo",
                name=f"微博用户_{token_data['uid'][:8]}",
                uid=token_data["uid"],
                access_token=token_data["access_token"],
                is_active=True,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            logger.info(f"已创建微博账号: {account.name}")
            uid = str(account.id)

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>授权成功</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #f5f6fa; }}
        .box {{ background: #fff; padding: 40px; border-radius: 12px; text-align: center; box-shadow: 0 4px 20px rgba(0,0,0,0.1); }}
        .ok {{ font-size: 48px; }} p {{ color: #666; margin: 16px 0; }}
    </style></head>
    <body>
        <div class="box">
            <div class="ok">✅</div>
            <h2>微博授权成功！</h2>
            <p>账号已绑定到自动运营系统</p>
            <p style="font-size:12px;color:#aaa;">账号 ID: {uid} | UID: {token_data['uid']}</p>
            <a href="/admin/accounts" style="color:#0984e3;">→ 返回账号管理</a>
        </div>
    </body></html>
    """)


@router.post("/exchange-code")
async def exchange_code_manual(code: str):
    """手动输入授权码换取 token（用于 localhost 不被微博接受时的降级方案）"""
    if not code:
        raise HTTPException(status_code=400, detail="缺少授权码 code")

    token_data = await WeiboPublisher.exchange_code(code)
    if not token_data:
        raise HTTPException(status_code=400, detail="换取 access_token 失败，请检查 code 是否正确或是否已过期（code 仅5分钟有效）")

    async with async_session() as session:
        existing = await session.execute(
            select(Account).where(
                Account.platform == "weibo",
                Account.uid == token_data["uid"],
            )
        )
        account = existing.scalar_one_or_none()

        if account:
            account.access_token = token_data["access_token"]
            account.is_active = True
            await session.commit()
            account_id = account.id
        else:
            account = Account(
                platform="weibo",
                name=f"微博用户_{token_data['uid'][:8]}",
                uid=token_data["uid"],
                access_token=token_data["access_token"],
                is_active=True,
            )
            session.add(account)
            await session.commit()
            await session.refresh(account)
            account_id = account.id

    return {
        "ok": True,
        "account_id": account_id,
        "uid": token_data["uid"],
        "message": f"授权成功！账号 ID: {account_id}",
    }


@router.get("/check-token")
async def check_token(account_id: int):
    """验证指定账号的微博 token 是否有效"""
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return {"valid": False, "error": "账号不存在"}
        publisher = WeiboPublisher(access_token=account.access_token)
        valid = await publisher.check_token()
        return {"valid": valid, "uid": account.uid}
