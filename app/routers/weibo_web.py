from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from loguru import logger

from app.database import async_session
from app.models.account import Account
from app.services.publisher.weibo_web import WebWeiboPublisher

router = APIRouter(prefix="/api/weibo-web", tags=["weibo-web"])


class SaveCookiesRequest(BaseModel):
    cookies: str
    name: str = ""
    uid: str = ""


@router.post("/login")
async def web_login():
    """打开浏览器进行微博扫码登录，返回 cookies"""
    result = await WebWeiboPublisher.login_and_get_cookies()
    if not result:
        raise HTTPException(status_code=500, detail="登录失败或超时")

    # 用 Cookie 调微博 API 获取昵称和 UID
    import json, httpx
    cookies_list = json.loads(result["cookies"])
    cookie_dict = {c["name"]: c["value"] for c in cookies_list}
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": "https://m.weibo.cn/",
    }
    screen_name = ""
    uid = ""
    try:
        async with httpx.AsyncClient(timeout=10, cookies=cookie_dict) as client:
            # Step 1: 拿 uid
            cfg = (await client.get("https://m.weibo.cn/api/config", headers=headers)).json()
            uid = str(cfg.get("data", {}).get("uid", ""))
            # Step 2: 用 uid 拿昵称
            if uid:
                profile = (await client.get(
                    f"https://m.weibo.cn/api/container/getIndex?containerid=100505{uid}&type=uid&value={uid}",
                    headers=headers,
                )).json()
                screen_name = profile.get("data", {}).get("userInfo", {}).get("screen_name", "")
    except Exception as e:
        logger.warning(f"获取微博昵称失败: {e}")

    return {
        "ok": True,
        "cookies": result["cookies"],
        "cookie_count": result["cookie_count"],
        "screen_name": screen_name,
        "uid": uid,
        "message": "登录成功！",
    }


@router.post("/accounts/{account_id}/save-cookies")
async def save_cookies(account_id: int, req: SaveCookiesRequest):
    """将 cookies 保存到指定账号"""
    cookies = req.cookies
    if not cookies:
        raise HTTPException(status_code=400, detail="cookies 不能为空")

    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")

        account.cookies = cookies
        account.is_active = True
        if req.name:
            account.name = req.name
        if req.uid:
            account.uid = req.uid
        await session.commit()

        logger.info(f"已保存账号 {account.name} 的 cookies")

        # 验证 cookies 是否有效
        publisher = WebWeiboPublisher(cookies_json=cookies)
        valid = await publisher.check_token()
        return {"ok": True, "cookies_valid": valid, "message": "Cookies 已保存" + (" (验证有效)" if valid else " (可能已过期)")}


@router.get("/check")
async def check_web_cookies(account_id: int):
    """验证指定账号的 cookies 是否有效"""
    async with async_session() as session:
        result = await session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if not account:
            return {"valid": False, "error": "账号不存在"}
        if not account.cookies:
            return {"valid": False, "error": "未登录，请先扫码登录"}
        publisher = WebWeiboPublisher(cookies_json=account.cookies)
        valid = await publisher.check_token()
        return {"valid": valid}
