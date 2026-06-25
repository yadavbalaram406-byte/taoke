import asyncio, base64, json, os, random, re, time, uuid
import httpx
from playwright.async_api import async_playwright
from loguru import logger

from app.services.publisher.base import BasePublisher, PublishResult

# ====== 登录会话管理（内存中） ======
# 采用 Sina SSO 扫码登录：纯 HTTP 调接口，无需浏览器（VPS headless 友好）
_login_sessions: dict[str, dict] = {}  # session_id -> {qrid, created_at}

SSO_IMAGE = "https://login.sina.com.cn/sso/qrcode/image"
SSO_CHECK = "https://login.sina.com.cn/sso/qrcode/check"
SSO_LOGIN = "https://login.sina.com.cn/sso/login.php"
PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _cleanup_session(session_id: str):
    """清理登录会话"""
    _login_sessions.pop(session_id, None)


def _cleanup_expired_sessions():
    """清理超过 5 分钟的过期会话"""
    now = time.time()
    expired = [
        sid for sid, s in _login_sessions.items()
        if now - s.get("created_at", 0) > 300
    ]
    for sid in expired:
        logger.info(f"清理过期登录会话: {sid}")
        _login_sessions.pop(sid, None)


def _parse_jsonp(text: str) -> dict:
    """从 STK(...) / callback(...) 包裹的 JSONP 响应里提取 JSON 对象"""
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"无法解析SSO响应: {text[:200]}")


# ====== 行为模拟工具 ======

def human_delay(min_ms=80, max_ms=300):
    """模拟人类操作的随机延迟"""
    return random.uniform(min_ms, max_ms) / 1000


async def human_type(page, selector, text):
    """模拟人类打字：逐字输入，随机间隔"""
    for char in text:
        await page.type(selector, char, delay=random.randint(50, 150))
        if random.random() < 0.1:
            await asyncio.sleep(random.uniform(0.1, 0.3))


async def human_scroll(page):
    """模拟随机滚动"""
    distance = random.randint(100, 400)
    await page.evaluate(f"window.scrollBy(0, {distance})")
    await asyncio.sleep(human_delay(200, 500))


# ====== WebWeiboPublisher ======

class WebWeiboPublisher(BasePublisher):
    """
    微博网页版发布器 — 通过 Playwright 模拟 m.weibo.cn 发博
    不依赖微博开放平台 API，绕过接口权限和域名绑定限制
    """

    platform = "weibo_web"

    def __init__(self, cookies_json: str = "", headless: bool = True):
        self.cookies_json = cookies_json
        self.headless = headless

    def _parse_cookies(self) -> list[dict]:
        if not self.cookies_json:
            return []
        try:
            return json.loads(self.cookies_json)
        except json.JSONDecodeError:
            return []

    # ====== 扫码登录（Sina SSO 接口，纯HTTP，无浏览器） ======

    @staticmethod
    async def start_qr_login() -> dict | None:
        """
        获取 Sina SSO 登录二维码（纯 HTTP，无需浏览器）。
        流程：调 /sso/qrcode/image 拿 qrid + 图片URL → 下载二维码 → base64 返回。
        二维码内容是 passport.weibo.cn 扫码地址，微博App扫一扫即可。
        """
        _cleanup_expired_sessions()
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": PC_UA, "Referer": "https://weibo.com/"},
            ) as client:
                r = await client.get(SSO_IMAGE, params={
                    "entry": "weibo", "size": "180", "callback": "STK",
                })
                data = _parse_jsonp(r.text)
                if data.get("retcode") != 20000000:
                    logger.error(f"获取二维码失败: {data}")
                    return None

                d = data.get("data", {})
                qrid = d.get("qrid")
                image_url = d.get("image", "")
                if image_url.startswith("//"):
                    image_url = "https:" + image_url
                if not qrid or not image_url:
                    logger.error(f"二维码响应缺字段: {data}")
                    return None

                img_resp = await client.get(image_url)
                if img_resp.status_code != 200 or not img_resp.content:
                    logger.error(f"下载二维码图片失败: {img_resp.status_code}")
                    return None
                qr_base64 = base64.b64encode(img_resp.content).decode()

            session_id = uuid.uuid4().hex[:16]
            _login_sessions[session_id] = {"qrid": qrid, "created_at": time.time()}
            logger.info(f"SSO登录会话已创建: {session_id}, qrid={qrid[:12]}...")
            return {"session_id": session_id, "qr_code": qr_base64}

        except Exception as e:
            logger.exception(f"start_qr_login 异常: {e}")
            return None

    @staticmethod
    async def check_qr_login(session_id: str) -> dict | None:
        """
        轮询扫码状态。
        retcode: 50114001=未扫描, 50114002=已扫描待确认, 20000000=成功(含alt令牌)。
        成功后用 alt 走跨域登录换取 cookies。
        """
        _cleanup_expired_sessions()
        sess = _login_sessions.get(session_id)
        if not sess:
            return {"ready": False, "error": "会话已过期，请重新扫码"}

        if time.time() - sess.get("created_at", 0) > 300:
            _login_sessions.pop(session_id, None)
            return {"ready": False, "error": "登录超时（5分钟），请重新扫码"}

        qrid = sess["qrid"]
        try:
            async with httpx.AsyncClient(
                timeout=15,
                headers={"User-Agent": PC_UA, "Referer": "https://weibo.com/"},
            ) as client:
                r = await client.get(SSO_CHECK, params={
                    "entry": "weibo", "qrid": qrid, "callback": "STK",
                })
                data = _parse_jsonp(r.text)
                retcode = data.get("retcode")

                if retcode == 50114001:          # 未扫描
                    return {"ready": False}
                if retcode == 50114002:          # 已扫描，待手机确认
                    return {"ready": False, "scanned": True}
                if retcode != 20000000:          # 失效/异常
                    logger.warning(f"扫码状态异常 retcode={retcode}: {data}")
                    _login_sessions.pop(session_id, None)
                    return {"ready": False, "error": data.get("msg") or "二维码已失效，请重新扫码"}

                alt = data.get("data", {}).get("alt") or data.get("alt")
                if not alt:
                    logger.error(f"扫码成功但缺 alt 令牌: {data}")
                    _login_sessions.pop(session_id, None)
                    return {"ready": False, "error": "登录令牌缺失，请重试"}

            # 用 alt 换取 cookies
            cookies = await WebWeiboPublisher._exchange_alt_for_cookies(alt)
            _login_sessions.pop(session_id, None)
            if not cookies:
                return {"ready": False, "error": "换取登录态失败，请重试"}

            cookies_json = json.dumps(cookies, ensure_ascii=False)
            # 拉取昵称/uid，同时验证 cookie 对 m.weibo.cn 是否有效
            screen_name, uid = await WebWeiboPublisher._fetch_profile(cookies)
            logger.info(
                f"SSO扫码登录成功! cookies={len(cookies)}个, uid={uid or '未取到'}, "
                f"昵称={screen_name or '未取到'}"
            )
            return {
                "ready": True,
                "cookies": cookies_json,
                "cookie_count": len(cookies),
                "screen_name": screen_name,
                "uid": uid,
            }

        except Exception as e:
            logger.exception(f"check_qr_login 异常: {e}")
            _login_sessions.pop(session_id, None)
            return {"ready": False, "error": f"检查失败: {e}"}

    @staticmethod
    async def _exchange_alt_for_cookies(alt: str) -> list[dict]:
        """用 alt 令牌走跨域登录，收集所有微博域的 cookies（playwright 格式）"""
        collected: dict[tuple, dict] = {}
        async with httpx.AsyncClient(
            timeout=15, follow_redirects=True,
            headers={"User-Agent": PC_UA, "Referer": "https://weibo.com/"},
        ) as client:
            r = await client.get(SSO_LOGIN, params={
                "entry": "weibo",
                "returntype": "TEXT",
                "crossdomain": "1",
                "cdult": "3",
                "domain": "weibo.com",
                "alt": alt,
                "savestate": "30",
                "callback": "STK",
            })
            try:
                data = _parse_jsonp(r.text)
            except Exception:
                data = {}
            logger.info(f"login.php 返回: {str(data)[:300]}")

            urls = (
                data.get("crossDomainUrlList")
                or data.get("data", {}).get("crossDomainUrlList")
                or []
            )
            # 逐个访问跨域 URL 以在各域种下 cookie（.weibo.com / .sina.com.cn / .weibo.cn）
            for u in urls:
                if u.startswith("//"):
                    u = "https:" + u
                try:
                    await client.get(u)
                except Exception as e:
                    logger.warning(f"跨域种cookie失败 {u[:60]}: {e}")

            # 关键：访问 m.weibo.cn 首页（非API），触发 SSO 重定向链，
            # 用一次性的 mweibo_short_token 换取 .weibo.cn 域的 SUB（完成移动端登录，MLOGIN=1）
            try:
                await client.get(
                    "https://m.weibo.cn/",
                    headers={"User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"
                    )},
                )
            except Exception as e:
                logger.warning(f"访问 m.weibo.cn 首页失败: {e}")

            for c in client.cookies.jar:
                key = (c.name, c.domain, c.path)
                collected[key] = {
                    "name": c.name,
                    "value": c.value,
                    "domain": c.domain,
                    "path": c.path or "/",
                }

        cookies = list(collected.values())
        domains = sorted({c["domain"] for c in cookies})
        logger.info(f"收集到 {len(cookies)} 个 cookies，覆盖域: {domains}")
        # 调试：打印 weibo.cn 家族的关键 cookie，便于排查 m.weibo.cn 登录态
        wb_cn = [f"{c['name']}@{c['domain']}" for c in cookies
                 if c["domain"].lstrip(".").endswith("weibo.cn")]
        logger.info(f"weibo.cn 域 cookies: {wb_cn}")
        return cookies

    @staticmethod
    def _build_cookie_jar(cookies: list[dict]) -> "httpx.Cookies":
        """把 cookie 列表构造成带域名的 httpx.Cookies，由 httpx 按请求主机自动挑选正确的同名 cookie"""
        jar = httpx.Cookies()
        for c in cookies:
            try:
                jar.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
            except Exception:
                pass
        return jar

    @staticmethod
    async def _fetch_profile(cookies: list[dict]) -> tuple[str, str]:
        """用 cookies 调 m.weibo.cn 拿昵称和 uid（兼作 cookie 有效性验证）"""
        jar = WebWeiboPublisher._build_cookie_jar(cookies)
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": "https://m.weibo.cn/",
        }
        try:
            async with httpx.AsyncClient(timeout=10, cookies=jar) as client:
                cfg = (await client.get("https://m.weibo.cn/api/config", headers=headers)).json()
                uid = str(cfg.get("data", {}).get("uid", "") or "")
                screen_name = ""
                if uid:
                    prof = (await client.get(
                        f"https://m.weibo.cn/api/container/getIndex?containerid=100505{uid}&type=uid&value={uid}",
                        headers=headers,
                    )).json()
                    screen_name = prof.get("data", {}).get("userInfo", {}).get("screen_name", "")
                return screen_name, uid
        except Exception as e:
            logger.warning(f"获取昵称失败: {e}")
            return "", ""

    @staticmethod
    async def cancel_qr_login(session_id: str):
        """取消扫码登录，清理会话"""
        _cleanup_session(session_id)
        return {"ok": True}

    @staticmethod
    async def login_and_get_cookies() -> dict | None:
        """
        旧版同步登录（保留兼容）— headless 模式下打开浏览器，截取二维码，等待用户扫码
        适用于桌面端本地开发（浏览器窗口可见）
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                locale="zh-CN",
                has_touch=True,
            )

            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            """)

            page = await context.new_page()

            logger.info("打开微博登录页...")
            await page.goto("https://m.weibo.cn", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            try:
                login_btn = page.locator('a[href*="login"], a:has-text("登录"), a:has-text("我的")').first
                await login_btn.tap(timeout=3000)
                await asyncio.sleep(2)
                logger.info(f"已点击登录入口，当前URL: {page.url}")
            except Exception:
                pass

            # 等待用户完成登录（最多 180 秒）
            logger.info("等待用户完成登录 (请用微博App扫码)...")
            try:
                await page.wait_for_function(
                    "() => window.location.href.includes('m.weibo.cn') && !window.location.href.includes('passport')",
                    timeout=180000
                )
                await asyncio.sleep(3)
                logger.info(f"登录完成，当前URL: {page.url}")
            except Exception as e:
                logger.warning(f"登录等待超时: {e}")

            logged_in = False
            try:
                await page.wait_for_selector(
                    '.avatar, [class*="avatar"], [class*="profile"], .tab, .nav-item, .card',
                    timeout=8000
                )
                logged_in = True
                logger.info("检测到登录成功标志")
            except Exception:
                try:
                    resp = await page.evaluate("""
                        async () => {
                            const r = await fetch('https://m.weibo.cn/api/config');
                            const d = await r.json();
                            return d.data ? d.data.uid || true : false;
                        }
                    """)
                    if resp:
                        logged_in = True
                        logger.info("通过API检测到已登录")
                except Exception:
                    pass

            if not logged_in:
                logger.error("无法确认登录状态，请重试")
                await browser.close()
                return None

            cookies = await context.cookies()
            await browser.close()

            cookies_json = json.dumps(cookies, ensure_ascii=False)
            logger.info(f"登录成功! 获取到 {len(cookies)} 个 cookies")
            return {"cookies": cookies_json, "cookie_count": len(cookies)}

    # ====== Cookie 验证 ======

    async def check_token(self) -> bool:
        """验证 cookies 是否仍有效 — 直接用 httpx 请求检测"""
        cookies = self._parse_cookies()
        if not cookies:
            return False

        try:
            # 用带域名的 cookie jar，避免重名 cookie（如多个域的 SUB）互相覆盖
            jar = WebWeiboPublisher._build_cookie_jar(cookies)
            async with httpx.AsyncClient(timeout=10, cookies=jar) as client:
                resp = await client.get(
                    "https://m.weibo.cn/api/config",
                    headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                data = resp.json()
                if data.get("data", {}).get("uid"):
                    return True
                return False
        except Exception as e:
            logger.error(f"Cookie 验证失败: {e}")
            return False

    # ====== 发布微博 ======

    async def publish(self, content: str, images: list[str] = None) -> PublishResult:
        """通过 m.weibo.cn 网页发微博"""
        cookies = self._parse_cookies()
        if not cookies:
            return PublishResult(success=False, error_message="未登录微博，请先在账号管理中进行扫码登录")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                )

                context = await browser.new_context(
                    viewport={"width": 430, "height": 932},
                    user_agent=(
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"
                    ),
                    locale="zh-CN",
                    has_touch=True,
                )
                await context.add_cookies(cookies)
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                """)

                page = await context.new_page()

                # Step 1: 先逛首页，再进发微博页面
                logger.info("打开微博首页...")
                await page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(human_delay(1500, 3000))
                await human_scroll(page)

                if "passport" in page.url or "visitor" in page.url:
                    await browser.close()
                    return PublishResult(success=False, error_message="Cookie已过期，请重新扫码登录")

                logger.info("点击发微博按钮...")
                try:
                    compose_btn = page.locator('a[href*="compose"], [class*="compose"], .m-compose-btn').first
                    await compose_btn.tap(timeout=5000)
                except Exception:
                    await page.goto("https://m.weibo.cn/compose/", wait_until="domcontentloaded", timeout=15000)

                await asyncio.sleep(human_delay(800, 1500))
                await human_scroll(page)

                # Step 2: 逐字慢速输入
                logger.info(f"输入微博文字({len(content)}字)...")
                try:
                    textarea = page.locator("textarea").first
                    await textarea.wait_for(state="visible", timeout=10000)
                    await textarea.click()
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                    sentences = content.split('\n')
                    for si, sentence in enumerate(sentences):
                        if si > 0:
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(random.uniform(0.3, 0.8))

                        if not sentence.strip():
                            continue

                        await page.keyboard.type(sentence, delay=random.randint(80, 200))
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        if random.random() < 0.15:
                            await asyncio.sleep(random.uniform(1.0, 2.5))

                    await asyncio.sleep(random.uniform(0.5, 1.5))
                except Exception as e:
                    await browser.close()
                    return PublishResult(success=False, error_message=f"输入文字失败: {e}")

                # Step 3: 上传图片
                if images and len(images) > 0:
                    for img_path in images[:1]:
                        try:
                            abs_path = os.path.abspath(img_path)
                            if not os.path.exists(abs_path):
                                logger.warning(f"图片不存在: {abs_path}")
                                continue

                            logger.info(f"上传图片: {abs_path}")
                            file_input = page.locator('input[type="file"]')
                            await file_input.set_input_files(abs_path)
                            await asyncio.sleep(human_delay(1000, 2000))
                        except Exception as e:
                            logger.warning(f"上传图片失败: {e}")

                await human_scroll(page)

                # Step 4: 点击发送
                logger.info("点击发送...")
                send_clicked = False
                try:
                    await page.evaluate("""
                        () => {
                            const sel = '[class*="overlay"], [class*="mask"], [class*="popup"], [class*="modal"], [class*="toast"]';
                            document.querySelectorAll(sel).forEach(el => { if (el.offsetParent !== null) el.remove(); });
                        }
                    """)
                    await asyncio.sleep(0.5)

                    # 同时匹配「发送」和「发布」，兼容 a / button / [role="button"]
                    send_btn = page.locator(
                        "a:has-text('发送'), button:has-text('发送'), "
                        "[role='button']:has-text('发送'), "
                        "a:has-text('发布'), button:has-text('发布'), "
                        "[role='button']:has-text('发布')"
                    ).first
                    await send_btn.wait_for(state="visible", timeout=5000)
                    btn_text = await send_btn.inner_text()
                    await asyncio.sleep(0.3)
                    await send_btn.tap(timeout=5000)
                    send_clicked = True
                    logger.info(f"已点击发送按钮（文字='{btn_text.strip()}'）")
                except Exception as e:
                    logger.warning(f"tap 发送失败: {e}，尝试 JS 兜底")

                if not send_clicked:
                    try:
                        # 记录当前页面上的可见按钮/链接文字，便于排查
                        page_buttons = await page.evaluate("""
                            () => Array.from(document.querySelectorAll('a, button, [role="button"]'))
                                .filter(el => el.offsetParent !== null)
                                .map(el => el.textContent.trim())
                                .filter(Boolean)
                                .slice(0, 30)
                        """)
                        logger.warning(f"页面可见按钮/链接: {page_buttons}")

                        clicked = await page.evaluate("""
                            () => {
                                const KEYWORDS = ['发送', '发布'];
                                // 精确匹配
                                for (const kw of KEYWORDS) {
                                    for (const el of document.querySelectorAll('*')) {
                                        if (el.textContent.trim() === kw && el.children.length === 0
                                            && el.offsetParent !== null && el.tagName !== 'BODY') {
                                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                                            return kw + '(精确)';
                                        }
                                    }
                                }
                                // 模糊匹配 a/button
                                for (const kw of KEYWORDS) {
                                    for (const el of document.querySelectorAll('a, button, [role="button"]')) {
                                        if (el.textContent.includes(kw) && el.offsetParent !== null) {
                                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                                            return kw + '(模糊)';
                                        }
                                    }
                                }
                                return null;
                            }
                        """)
                        if not clicked:
                            # 截图存档，便于人工排查 UI 变化
                            try:
                                screenshot_path = f"/tmp/weibo_compose_fail_{int(time.time())}.png"
                                await page.screenshot(path=screenshot_path, full_page=True)
                                logger.error(f"发送按钮截图已保存: {screenshot_path}")
                            except Exception:
                                pass
                            await browser.close()
                            return PublishResult(success=False, error_message="未找到发送按钮，发布失败")
                        send_clicked = True
                        logger.info(f"JS 兜底点击成功: {clicked}")
                    except Exception as e2:
                        await browser.close()
                        return PublishResult(success=False, error_message=f"点击发送失败: {e2}")

                # Step 5: 等待结果
                logger.info("等待发布完成...")
                await asyncio.sleep(5)

                current_url = page.url
                logger.info(f"发布后URL: {current_url}")

                if "passport" in current_url or "visitor" in current_url:
                    await browser.close()
                    return PublishResult(success=False, error_message="Cookie已过期，请重新扫码登录")

                if "compose" not in current_url:
                    import re as _re
                    post_id = None
                    m = _re.search(r'/(?:status|detail)/([A-Za-z0-9]+)', current_url)
                    if m:
                        post_id = m.group(1)
                    logger.info(f"已离开 compose，发布成功 (post_id={post_id})")
                    await browser.close()
                    return PublishResult(success=True, external_id=post_id or "web", external_url=current_url)

                success_detected = await page.evaluate("""
                    () => {
                        const text = (document.body.innerText || '').slice(0, 600);
                        if (text.includes('已发送') || text.includes('发送成功') || text.includes('发布成功'))
                            return true;
                        const toasts = document.querySelectorAll(
                            '[class*="toast"], [class*="Toast"], [class*="notice"], [class*="tip"], [class*="fade-enter"]'
                        );
                        for (const t of toasts) {
                            if (t.innerText && (t.innerText.includes('成功') || t.innerText.includes('已发送')))
                                return true;
                        }
                        return false;
                    }
                """)

                if success_detected:
                    logger.info("检测到发送成功提示")
                    await browser.close()
                    return PublishResult(success=True, external_id="web", external_url=current_url)

                textarea_visible_len = await page.evaluate("""
                    () => {
                        const ta = document.querySelector('textarea');
                        if (!ta) return null;
                        const container = ta.closest('[class*="weibo-lite"], [class*="editor"], [class*="compose"]')
                            || ta.parentElement;
                        const visible = (container ? container.innerText : ta.innerText || '').trim();
                        return visible.length;
                    }
                """)

                if textarea_visible_len == 0:
                    logger.info("编辑区内容已清空，推断发布成功")
                    await browser.close()
                    return PublishResult(success=True, external_id="web", external_url=current_url)

                if textarea_visible_len and textarea_visible_len > 0:
                    logger.warning(f"编辑区仍有 {textarea_visible_len} 字，发送未生效")
                    await browser.close()
                    return PublishResult(success=False, error_message="发送未生效：编辑区仍有内容，请手动检查微博主页")

                await browser.close()
                logger.warning("无法确认发布状态，标记为失败")
                return PublishResult(success=False, error_message="无法确认发布状态，请到微博主页确认")

        except Exception as e:
            logger.exception(f"Web微博发布异常: {e}")
            return PublishResult(success=False, error_message=str(e))
