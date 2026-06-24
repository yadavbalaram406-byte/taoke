import asyncio, json, os, random, time
from playwright.async_api import async_playwright
from loguru import logger

from app.services.publisher.base import BasePublisher, PublishResult

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

    # ====== 登录流程 ======

    @staticmethod
    async def login_and_get_cookies() -> dict | None:
        """
        打开浏览器让用户手动登录，成功后返回 cookies
        用户可以用扫码或密码登录
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
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

            # 打开微博移动端登录页
            logger.info("打开微博登录页...")
            await page.goto("https://m.weibo.cn", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # 如果未登录，点击登录按钮
            try:
                login_btn = page.locator('a[href*="login"], a:has-text("登录"), a:has-text("我的")').first
                await login_btn.tap(timeout=3000)
                await asyncio.sleep(2)
                logger.info(f"已点击登录入口，当前URL: {page.url}")
            except Exception:
                # 可能已经在登录页或已登录
                pass

            # 等待用户完成登录（最多 180 秒）
            # 登录成功后页面会跳转到 m.weibo.cn 首页
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

            # 检查是否登录成功：查找用户相关元素
            logged_in = False
            try:
                await page.wait_for_selector(
                    '.avatar, [class*="avatar"], [class*="profile"], .tab, .nav-item, .card',
                    timeout=8000
                )
                logged_in = True
                logger.info("检测到登录成功标志")
            except Exception:
                # 替代检查：尝试访问 API 看是否返回用户信息
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

            # 获取 cookies
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
            # 转换 cookie 格式：从 Playwright cookies 到 httpx cookies
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

                # Step 1: 先逛首页，再进发微博页面（模拟真人路径，避免直接访问compose被识别为机器人）
                logger.info("打开微博首页...")
                await page.goto("https://m.weibo.cn", wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(human_delay(1500, 3000))
                await human_scroll(page)

                # 如果被重定向到验证页则Cookie无效
                if "passport" in page.url or "visitor" in page.url:
                    await browser.close()
                    return PublishResult(success=False, error_message="Cookie已过期，请重新扫码登录")

                logger.info("点击发微博按钮...")
                try:
                    compose_btn = page.locator('a[href*="compose"], [class*="compose"], .m-compose-btn').first
                    await compose_btn.tap(timeout=5000)
                except Exception:
                    # fallback: 直接导航
                    await page.goto("https://m.weibo.cn/compose/", wait_until="domcontentloaded", timeout=15000)

                await asyncio.sleep(human_delay(800, 1500))
                await human_scroll(page)

                # Step 2: 逐字慢速输入（模拟真人手机打字）
                logger.info(f"输入微博文字({len(content)}字)...")
                try:
                    textarea = page.locator("textarea").first
                    await textarea.wait_for(state="visible", timeout=10000)
                    await textarea.click()
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                    # 逐行输入，每行逐字敲，行间有 Enter + 停顿
                    sentences = content.split('\n')
                    for si, sentence in enumerate(sentences):
                        if si > 0:
                            # 换行
                            await page.keyboard.press("Enter")
                            await asyncio.sleep(random.uniform(0.3, 0.8))

                        if not sentence.strip():
                            continue

                        # 用 type 逐字输入，每个字 80-200ms 间隔
                        await page.keyboard.type(sentence, delay=random.randint(80, 200))

                        # 句末停顿
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        # 偶尔思考久一点
                        if random.random() < 0.15:
                            await asyncio.sleep(random.uniform(1.0, 2.5))

                    await asyncio.sleep(random.uniform(0.5, 1.5))
                except Exception as e:
                    await browser.close()
                    return PublishResult(success=False, error_message=f"输入文字失败: {e}")

                # Step 3: 上传图片（如果有）
                if images and len(images) > 0:
                    for img_path in images[:1]:  # 每次只发一张
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

                # Step 4: 点击发送按钮
                logger.info("点击发送...")
                send_clicked = False
                try:
                    # 清除可能的遮挡层
                    await page.evaluate("""
                        () => {
                            const sel = '[class*="overlay"], [class*="mask"], [class*="popup"], [class*="modal"], [class*="toast"]';
                            document.querySelectorAll(sel).forEach(el => { if (el.offsetParent !== null) el.remove(); });
                        }
                    """)
                    await asyncio.sleep(0.5)

                    # 用 text 精确匹配发送按钮（比 class 选择器更可靠）
                    send_btn = page.locator("a:has-text('发送')").first
                    # 等按钮可见且可点击
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
                                // 再搜 a/button 包含"发送"
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

                # Step 5: 等待发布结果（m.weibo.cn 是 SPA，发完仍留在 compose 页面）
                logger.info("等待发布完成...")
                await asyncio.sleep(5)

                current_url = page.url
                logger.info(f"发布后URL: {current_url}")

                # Cookie 失效
                if "passport" in current_url or "visitor" in current_url:
                    await browser.close()
                    return PublishResult(success=False, error_message="Cookie已过期，请重新扫码登录")

                # 信号 1: 离开 compose → 肯定成功
                if "compose" not in current_url:
                    import re as _re
                    post_id = None
                    m = _re.search(r'/(?:status|detail)/([A-Za-z0-9]+)', current_url)
                    if m:
                        post_id = m.group(1)
                    logger.info(f"已离开 compose，发布成功 (post_id={post_id})")
                    await browser.close()
                    return PublishResult(success=True, external_id=post_id or "web", external_url=current_url)

                # 信号 2: 仍在 compose，检测成功提示（toast 弹窗等）
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

                # 信号 3: React 内容长度检测
                # m.weibo.cn 是 SPA，textarea.value 始终为空（React state 管理内容）。
                # 改用 innerText / textContent 读取实际显示内容，再与原文比对。
                # 若显示内容为空或已大幅缩短，推断发送成功后被清空。
                textarea_visible_len = await page.evaluate("""
                    () => {
                        const ta = document.querySelector('textarea');
                        if (!ta) return null;
                        // 优先取父级编辑容器的 innerText（React 渲染的可见文字）
                        const container = ta.closest('[class*="weibo-lite"], [class*="editor"], [class*="compose"]')
                            || ta.parentElement;
                        const visible = (container ? container.innerText : ta.innerText || '').trim();
                        return visible.length;
                    }
                """)

                # visible_len 为 null（找不到 textarea）→ 信号不明
                # visible_len == 0 → 内容已被清空，推断成功
                # visible_len > 0 → 内容仍在，说明发送未触发
                if textarea_visible_len == 0:
                    logger.info("编辑区内容已清空，推断发布成功")
                    await browser.close()
                    return PublishResult(success=True, external_id="web", external_url=current_url)

                if textarea_visible_len and textarea_visible_len > 0:
                    logger.warning(f"编辑区仍有 {textarea_visible_len} 字，发送未生效")
                    await browser.close()
                    return PublishResult(success=False, error_message="发送未生效：编辑区仍有内容，请手动检查微博主页")

                # 信号不明 → 保守处理：标记失败
                await browser.close()
                logger.warning("无法确认发布状态，标记为失败")
                return PublishResult(success=False, error_message="无法确认发布状态，请到微博主页确认")

        except Exception as e:
            logger.exception(f"Web微博发布异常: {e}")
            return PublishResult(success=False, error_message=str(e))
