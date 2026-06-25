from fastapi import APIRouter, HTTPException, Query
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


# ====== 新版：QR 码扫码登录（VPS headless + 手机扫码） ======

@router.post("/login/start")
async def start_qr_login():
    """启动 headless 浏览器 → 截取微博登录二维码 → 返回 base64 图片 + session_id"""
    result = await WebWeiboPublisher.start_qr_login()
    if not result:
        raise HTTPException(status_code=500, detail="无法打开微博登录页，请重试")
    return {
        "ok": True,
        "session_id": result["session_id"],
        "qr_code": result["qr_code"],
        "message": "请用微博App扫描二维码",
    }


@router.get("/login/check")
async def check_qr_login(session_id: str = Query(...)):
    """检查扫码登录是否完成"""
    result = await WebWeiboPublisher.check_qr_login(session_id)
    if result is None:
        return {"ready": False, "error": "会话不存在"}
    return result


@router.post("/login/cancel")
async def cancel_qr_login(session_id: str = Query(...)):
    """取消扫码登录"""
    return await WebWeiboPublisher.cancel_qr_login(session_id)


# ====== 旧版兼容 ======

@router.post("/login")
async def web_login():
    """旧版同步登录（保留兼容）— headless 模式下等待扫码"""
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
            cfg = (await client.get("https://m.weibo.cn/api/config", headers=headers)).json()
            uid = str(cfg.get("data", {}).get("uid", ""))
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


# ====== Cookie 保存 & 验证 ======

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

        publisher = WebWeiboPublisher(cookies_json=cookies)
        valid = await publisher.check_token()
        return {"ok": True, "cookies_valid": valid, "message": "Cookies 已保存" + (" (验证有效)" if valid else " (可能已过期)")}


@router.get("/check")
async def check_web_cookies(account_id: int = Query(...)):
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
