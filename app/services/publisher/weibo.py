import httpx
from loguru import logger

from app.config import settings
from app.services.publisher.base import BasePublisher, PublishResult


class WeiboPublisher(BasePublisher):
    """
    微博发布器 — 使用微博开放平台 API V2

    核心接口:
    - statuses/update.json  → 发文字微博（纯文本）
    - statuses/share.json   → 发图文微博（文字+图片一步完成）
    - statuses/upload.json  → 先上传图片拿 pic_id
    - account/get_uid.json  → 验证 token

    OAuth2: https://open.weibo.com/wiki/%E6%8E%88%E6%9D%83%E6%9C%BA%E5%88%B6
    """

    platform = "weibo"
    API_BASE = "https://api.weibo.com/2"

    def __init__(self, access_token: str = ""):
        self.access_token = access_token

    # ====== OAuth2 授权 (静态方法，不依赖 token) ======

    @staticmethod
    def get_auth_url() -> str:
        """生成微博 OAuth2 授权 URL"""
        return (
            f"https://api.weibo.com/oauth2/authorize"
            f"?client_id={settings.WEIBO_APP_KEY}"
            f"&redirect_uri={settings.WEIBO_REDIRECT_URI}"
            f"&response_type=code"
            f"&scope=all"
        )

    @staticmethod
    async def exchange_code(code: str) -> dict | None:
        """用授权码换取 access_token"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.weibo.com/oauth2/access_token",
                    data={
                        "client_id": settings.WEIBO_APP_KEY,
                        "client_secret": settings.WEIBO_APP_SECRET,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": settings.WEIBO_REDIRECT_URI,
                    },
                )
                data = resp.json()
                if "access_token" in data:
                    return {
                        "access_token": data["access_token"],
                        "uid": str(data.get("uid", "")),
                        "expires_in": data.get("expires_in", 0),
                    }
                logger.error(f"换取 token 失败: {data}")
                return None
        except Exception as e:
            logger.exception(f"OAuth2 token exchange error: {e}")
            return None

    # ====== Token 验证 ======

    async def check_token(self) -> bool:
        """检查 access_token 是否有效"""
        if not self.access_token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.API_BASE}/account/get_uid.json",
                    params={"access_token": self.access_token},
                )
                data = resp.json()
                return "uid" in data and "error" not in data
        except Exception as e:
            logger.error(f"微博 token 验证失败: {e}")
            return False

    # ====== 发微博 ======

    async def publish(self, content: str, images: list[str] = None) -> PublishResult:
        """
        发布微博。有图片时用 share.json，无图片时用 update.json
        """
        try:
            if not self.access_token:
                return PublishResult(success=False, error_message="未配置微博 access_token")

            if images and len(images) > 0:
                return await self._publish_with_image(content, images[0])
            else:
                return await self._publish_text(content)

        except Exception as e:
            logger.exception(f"微博发布异常: {e}")
            return PublishResult(success=False, error_message=str(e))

    async def _publish_text(self, content: str) -> PublishResult:
        """发纯文字微博 — statuses/update.json"""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.API_BASE}/statuses/update.json",
                    data={
                        "access_token": self.access_token,
                        "status": content,
                        "visible": "0",
                        "rip": "127.0.0.1",
                    },
                )
                data = resp.json()

            if "id" in data or "idstr" in data:
                post_id = str(data.get("idstr") or data.get("id"))
                uid = data.get("user", {}).get("idstr", "")
                url = f"https://weibo.com/{uid}/{post_id}" if uid else ""
                logger.info(f"文字微博发布成功: {url}")
                return PublishResult(success=True, external_id=post_id, external_url=url)
            else:
                error = f"code={data.get('error_code', '?')} {data.get('error', '')}"
                logger.error(f"微博发布失败: {error}")
                return PublishResult(success=False, error_message=error)

        except Exception as e:
            logger.exception(f"文字微博发布异常: {e}")
            return PublishResult(success=False, error_message=str(e))

    async def _publish_with_image(self, content: str, image_path: str) -> PublishResult:
        """
        发图文微博 — 先上传图片拿 pic_id，再用 statuses/update.json 发
        微博标准流程: upload.json → update.json
        """
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                # Step 1: 上传图片
                pic_id = None
                try:
                    import os
                    abs_path = os.path.abspath(image_path)
                    with open(abs_path, "rb") as f:
                        resp = await client.post(
                            f"{self.API_BASE}/statuses/upload.json",
                            data={
                                "access_token": self.access_token,
                                "rip": "127.0.0.1",
                            },
                            files={"pic": abs_path.split("/")[-1]},
                        )
                    upload_data = resp.json()
                    if "pic_id" in upload_data:
                        pic_id = str(upload_data["pic_id"])
                        logger.info(f"图片上传成功: pic_id={pic_id}")
                    else:
                        logger.error(f"图片上传失败: {upload_data}")
                        return await self._publish_text(content)  # 降级为纯文字
                except FileNotFoundError:
                    logger.warning(f"图片文件不存在: {image_path}，发纯文字")
                    return await self._publish_text(content)

                # Step 2: 发微博
                params = {
                    "access_token": self.access_token,
                    "status": content,
                    "visible": "0",
                    "rip": "127.0.0.1",
                }
                if pic_id:
                    params["pic_id"] = pic_id

                resp = await client.post(
                    f"{self.API_BASE}/statuses/update.json",
                    data=params,
                )
                data_resp = resp.json()

            if "id" in data_resp or "idstr" in data_resp:
                post_id = str(data_resp.get("idstr") or data_resp.get("id"))
                uid = data_resp.get("user", {}).get("idstr", "")
                url = f"https://weibo.com/{uid}/{post_id}" if uid else ""
                logger.info(f"图文微博发布成功: {url}")
                return PublishResult(success=True, external_id=post_id, external_url=url)
            else:
                error = f"code={data_resp.get('error_code', '?')} {data_resp.get('error', '')}"
                logger.error(f"微博发布失败: {error}")
                return PublishResult(success=False, error_message=error)

        except Exception as e:
            logger.exception(f"图文微博发布异常: {e}")
            return PublishResult(success=False, error_message=str(e))

    # ====== 图片单独上传（备用） ======

    async def upload_image(self, image_path: str) -> str | None:
        """
        先上传图片拿 pic_id（用于 statuses/update.json 批量发图场景）
        API: statuses/upload.json
        """
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(image_path, "rb") as f:
                    resp = await client.post(
                        f"{self.API_BASE}/statuses/upload.json",
                        data={"access_token": self.access_token},
                        files={"pic": f},
                    )
                    data = resp.json()
                    pic_id = data.get("pic_id", "")
                    if pic_id:
                        logger.info(f"图片上传成功: pic_id={pic_id}")
                        return str(pic_id)
                    logger.error(f"图片上传失败: {data}")
                    return None
        except Exception as e:
            logger.exception(f"图片上传异常: {e}")
            return None
