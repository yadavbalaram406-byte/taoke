import asyncio, base64, json, os, random, time, uuid
from playwright.async_api import async_playwright
from loguru import logger

from app.services.publisher.base import BasePublisher, PublishResult

# ====== 登录会话管理（内存中） ======
_login_sessions: dict[str, dict] = {}  # session_id -> {browser, context, page, created_at}


def _cleanup_session(session_id: str):
    """清理登录会话"""
    if session_id in _login_sessions:
        try:
            sess = _login_sessions.pop(session_id)
            asyncio.ensure_future(_safe_close(sess))
        except Exception:
            pass


async def _safe_close(sess: dict):
    """安全关闭浏览器"""
    try:
        if "browser" in sess:
            await sess["browser"].close()
    except Exception:
        pass


async def _cleanup_expired_sessions():
    """清理超过 5 分钟的过期会话"""
    now = time.time()
    expired = [
        sid for sid, s in _login_sessions.items()
        if now - s.get("created_at", 0) > 300
    ]
    for sid in expired:
        logger.info(f"清理过期登录会话: {sid}")
        _cleanup_session(sid)


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

    # ====== 扫码登录（VPS headless + 手机扫码） ======

    @staticmethod
    async def start_qr_login() -> dict | None:
        """
        启动 headless 浏览器 → 打开微博登录页 → 截取二维码 → 返回 base64 + session_id
        浏览器会话保持在内存中，等待用户扫码
        """
        await _cleanup_expired_sessions()

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

            # 注入反检测脚本
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => false});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            """)

            page = await context.new_page()

            # Step 1: 打开微博移动端首页
            logger.info("打开微博首页...")
            await page.goto("https://m.weibo.cn", wait_until="networkidle", timeout=20000)
            await asyncio.sleep(2)

            # Step 2: 点击登录入口，进入 passport 登录页
            logger.info(f"当前页面 URL: {page.url}")
            clicked_login = False
            try:
                # 优先匹配底部导航栏的「我」或「登录」
                login_btn = page.locator('a:has-text("我"), a:has-text("登录"), [href*="login"]').first
                await login_btn.wait_for(state="visible", timeout=5000)
                await login_btn.tap()
                await asyncio.sleep(2)
                logger.info(f"点击登录后 URL: {page.url}")
                clicked_login = True
            except Exception as e:
                logger.warning(f"首页点击登录失败: {e}")

            # Step 3: 如果没跳转到 passport，尝试直接访问
            if not clicked_login or "passport" not in page.url:
                logger.info("尝试直接访问 passport 登录页...")
                try:
                    await page.goto("https://passport.weibo.cn/signin/login", wait_until="networkidle", timeout=20000)
                    await asyncio.sleep(3)
                    logger.info(f"直接访问后 URL: {page.url}")
                except Exception as e:
                    logger.warning(f"直接访问 passport 失败: {e}")

            # Step 4: 等待并截取二维码
            # m.weibo.cn passport 登录页默认展示二维码
            await asyncio.sleep(3)

            # 如果不在 passport 页面，再等一下
            if "passport" not in page.url:
                await asyncio.sleep(3)
                logger.info(f"等待后 URL: {page.url}")

            qr_base64 = ""
            qr_found = False

            # 尝试多种方式定位二维码
            # 方式1: img 标签含 qrcode
            for selector in [
                'img[src*="qrcode"]',
                'img[src*="QR"]',
                '.qrcode img',
                '[class*="qrcode"] img',
                '[class*="qr"] img',
                'img[src*="passport"]',
                '.login-main img',
                '.form img',
                # 方式2: canvas
                'canvas',
            ]:
                try:
                    el = page.locator(selector).first
                    if await el.count() > 0:
                        qr_bytes = await el.screenshot(timeout=5000)
                        if qr_bytes and len(qr_bytes) > 500:  # 有效图片至少 500 bytes
                            qr_base64 = base64.b64encode(qr_bytes).decode()
                            logger.info(f"二维码已截取 (selector: {selector}, size: {len(qr_bytes)} bytes)")
                            qr_found = True
                            break
                except Exception:
                    continue

            # fallback: 截取页面中央区域（二维码通常在中央偏上）
            if not qr_found:
                logger.warning("无法定位二维码元素，截取页面中央区域")
                try:
                    # 截取完整页面，然后裁剪中央区域
                    full_bytes = await page.screenshot(full_page=False)
                    qr_base64 = base64.b64encode(full_bytes).decode()
                    logger.info(f"已截取整页 (size: {len(full_bytes)} bytes)")
                except Exception as e:
                    logger.error(f"截取失败: {e}")

            if not qr_base64:
                await browser.close()
                return None

            # 生成 session_id 并保存浏览器会话
            session_id = uuid.uuid4().hex[:16]
            _login_sessions[session_id] = {
                "browser": browser,
                "context": context,
                "page": page,
                "created_at": time.time(),
            }

            logger.info(f"登录会话已创建: {session_id}, URL: {page.url}")
            return {
                "session_id": session_id,
                "qr_code": qr_base64,
            }

    @staticmethod
    async def check_qr_login(session_id: str) -> dict | None:
        """检查扫码登录是否完成，完成则返回 cookies"""
        await _cleanup_expired_sessions()

        sess = _login_sessions.get(session_id)
        if not sess:
            return {"ready": False, "error": "会话已过期，请重新扫码"}

        page = sess["page"]
        context = sess["context"]
        browser = sess["browser"]

        try:
            current_url = page.url
            logger.info(f"检查登录状态，当前URL: {current_url}")

            # 检查是否已登录（URL 已跳转到 m.weibo.cn 首页）
            if "m.weibo.cn" in current_url and "passport" not in current_url:
                await asyncio.sleep(2)

                # 确认登录成功
                logged_in = False
                try:
                    await page.wait_for_selector(
                        '.avatar, [class*="avatar"], [class*="profile"], .tab, .nav-item, .card',
                        timeout=5000
                    )
                    logged_in = True
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
                    except Exception:
                        pass

                if logged_in:
                    cookies = await context.cookies()
                    cookies_json = json.dumps(cookies, ensure_ascii=False)
                    logger.info(f"扫码登录成功! 获取到 {len(cookies)} 个 cookies")

                    # 清理会话
                    await browser.close()
                    _login_sessions.pop(session_id, None)

                    return {
                        "ready": True,
                        "cookies": cookies_json,
                        "cookie_count": len(cookies),
                    }

            # 检查是否超时（5 分钟）
            if time.time() - sess.get("created_at", 0) > 300:
                await browser.close()
                _login_sessions.pop(session_id, None)
                return {"ready": False, "error": "登录超时（5分钟），请重新扫码"}

            return {"ready": False}

        except Exception as e:
            logger.error(f"检查登录状态异常: {e}")
            try:
                await browser.close()
            except Exception:
                pass
            _login_sessions.pop(session_id, None)
            return {"ready": False, "error": f"检查失败: {e}"}

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
            import httpx
            cookie_dict = {}
            for c in cookies:
                cookie_dict[c.get("name", "")] = c.get("value", "")

            async with httpx.AsyncClient(timeout=10, cookies=cookie_dict) as client:
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

                    send_btn = page.locator("a:has-text('发送')").first
                    await send_btn.wait_for(state="visible", timeout=5000)
                    await asyncio.sleep(0.3)
                    await send_btn.tap(timeout=5000)
                    send_clicked = True
                    logger.info("已点击发送按钮")
                except Exception as e:
                    logger.warning(f"tap 发送失败: {e}，尝试 JS 兜底")

                if not send_clicked:
                    try:
                        clicked = await page.evaluate("""
                            () => {
                                const all = document.querySelectorAll('*');
                                for (const el of all) {
                                    if (el.textContent.trim() === '发送' && el.children.length === 0
                                        && el.offsetParent !== null && el.tagName !== 'BODY') {
                                        el.dispatchEvent(new Event('click', {bubbles: true}));
                                        return true;
                                    }
                                }
                                for (const el of document.querySelectorAll('a, button, [role="button"]')) {
                                    if (el.textContent.includes('发送') && el.offsetParent !== null) {
                                        el.dispatchEvent(new Event('click', {bubbles: true}));
                                        return true;
                                    }
                                }
                                return false;
                            }
                        """)
                        if not clicked:
                            await browser.close()
                            return PublishResult(success=False, error_message="未找到发送按钮，发布失败")
                        send_clicked = True
                        logger.info("JS 兜底点击成功")
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
