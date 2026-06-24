"""飞书群消息抓取器 — 通过 tenant_access_token 拉取指定群当天的消息"""
import datetime
import json
import ssl
import time
import urllib.request
from loguru import logger

from app.config import settings

# 跳过 SSL 验证（兼容自签证书代理环境）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ---- token 内存缓存（飞书 tenant_access_token 有效期 7200s，提前 5 分钟续期）----
_token_cache: dict = {"token": None, "expires_at": 0.0}


def _get_access_token() -> str | None:
    """获取飞书 tenant_access_token（带内存缓存，避免每次请求都重新获取）"""
    now = time.time()
    # 提前 300s 刷新，避免恰好到期时请求失败
    if _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({
        "app_id": settings.FEISHU_APP_ID,
        "app_secret": settings.FEISHU_APP_SECRET,
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10, context=_SSL_CTX)
        result = json.loads(resp.read())
        if result.get("code") == 0:
            expire = result.get("expire", 7200)
            _token_cache["token"] = result["tenant_access_token"]
            _token_cache["expires_at"] = now + expire
            logger.debug(f"[feishu] token 刷新成功，{expire}s 后过期")
            return _token_cache["token"]
        logger.error(f"[feishu] 获取 token 失败: {result}")
        return None
    except Exception as e:
        logger.error(f"[feishu] 获取 token 异常: {e}")
        return None


def _extract_text(item: dict) -> str:
    """从消息 item 中提取纯文本内容"""
    msg_type = item.get("msg_type", "")
    body = item.get("body", {}).get("content", "")
    try:
        body_json = json.loads(body)
        if msg_type == "post":
            parts = []
            for para in body_json.get("content", []):
                for seg in para:
                    if seg.get("tag") == "text":
                        parts.append(seg.get("text", ""))
                parts.append("\n")
            return "".join(parts).strip()
        elif msg_type == "text":
            return body_json.get("text", "").strip()
        else:
            return ""
    except Exception:
        return ""


def fetch_today_messages(chat_id: str) -> list[dict]:
    """
    拉取指定群今天的所有文本/富文本消息。
    返回 list of {"time": "HH:MM", "text": "..."}，按时间正序。
    """
    token = _get_access_token()
    if not token:
        return []

    url = (
        f"https://open.feishu.cn/open-apis/im/v1/messages"
        f"?container_id_type=chat&container_id={chat_id}"
        f"&sort_type=ByCreateTimeDesc&page_size=50"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10, context=_SSL_CTX)
        result = json.loads(resp.read())
    except Exception as e:
        logger.error(f"[feishu] 拉取消息异常: {e}")
        return []

    items = result.get("data", {}).get("items", [])
    today = datetime.date.today()
    messages = []

    for item in items:
        ts = int(item.get("create_time", 0)) // 1000
        dt = datetime.datetime.fromtimestamp(ts)
        if dt.date() != today:
            continue
        if item.get("msg_type") not in ("post", "text"):
            continue
        text = _extract_text(item)
        if not text or len(text) < 50:
            continue
        # 过滤系统消息头（Cronjob Response 前缀）
        if text.startswith("Cronjob Response:"):
            # 去掉第一行（标题行）
            lines = text.split("\n")
            text = "\n".join(lines[1:]).strip()
        messages.append({
            "time": dt.strftime("%H:%M"),
            "text": text,
        })

    # 按时间正序
    messages.reverse()
    logger.info(f"[feishu] 今日群消息 {len(messages)} 条")
    return messages
