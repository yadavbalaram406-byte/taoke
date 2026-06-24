"""热搜话题扫描器 — 从微博获取实时热搜并过滤敏感话题"""
import json
import re
import httpx
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class HotTopic:
    name: str
    query: str = ""
    heat: int = 0
    rank: int = 0
    category: str = ""
    desc: str = ""


@dataclass
class ScanResult:
    success: bool
    topics: list[HotTopic] = field(default_factory=list)
    error_message: str = ""


# 默认过滤关键词
DEFAULT_FILTER_KEYWORDS = {
    # 政治敏感
    "政治", "习近平", "国务院", "外交部", "中央", "党委", "政府", "主席",
    "台湾", "新疆", "西藏", "台独", "港独", "疆独", "藏独",
    "六四", "法轮功", "天安门", "民主", "人权", "自由",
    # 外交/领导人/国事（容易0互动，不参与）
    "朝鲜", "朝方", "平壤", "中朝", "朝中", "金正恩", "总书记",
    "普京", "俄罗斯", "莫斯科", "克里姆林宫", "泽连斯基", "乌克兰",
    "特朗普", "拜登", "白宫", "国会", "选举", "竞选",
    "联合国", "安理会", "北约", "欧盟", "制裁",
    "国事访问", "正式访问", "欢迎仪式", "仪仗队", "检阅", "阅兵",
    "领导人", "总统", "首相", "总理", "外长", "大使",
    "礼宾", "车队", "护卫", "凯旋门",
    # 外交/军事
    "美国制裁", "南海", "钓鱼岛", "军队", "导弹", "核武器", "战争",
    # 负面社会
    "死亡", "自杀", "杀人", "强奸", "暴力", "恐怖", "血腥", "事故",
    "车祸", "火灾", "地震", "洪水", "矿难",
    # 商业广告 / 电商软文 / 品牌推广
    "广告", "促销", "大促", "双11", "618", "秒杀", "满减",
    "京东", "天猫", "淘宝", "拼多多", "苏宁",
    "降价", "特价", "优惠", "折扣", "补贴", "红包", "预售", "抢购",
    "首发", "上新", "品牌日", "限时", "好价", "低价", "必买", "推荐", "清单",
    "开售", "热卖", "爆款", "值得买", "下单", "到手价", "券后",
    # 化妆品/日化品牌（纯品牌名无事件大概率是软广）
    "欧莱雅", "兰蔻", "雅诗兰黛", "香奈儿", "迪奥", "SK-II", "SK2",
    "资生堂", "玉兰油", "珀莱雅", "薇诺娜", "完美日记", "花西子",
    "海蓝之谜", "娇兰", "纪梵希", "圣罗兰", "阿玛尼", "赫莲娜",
    "代言", "联名", "同款", "限量", "定制", "礼盒",
    "戛纳", "时装周", "红毯", "秀场", "发布会", "新品",
    # 注：科技/汽车品牌（华为、小米、特斯拉等）可能是真实新闻，不过滤，
    # 如需屏蔽请在任务「自定义过滤词」中添加
    # 色情擦边
    "裸", "性爱", "色情", "约炮",
}

# 官方发布类话题关键词 — 蓝V/官方号主导，不适合个人观点参与
OFFICIAL_NOTICE_KW = [
    "大到暴雨", "暴雨预警", "台风", "预警信号", "应急响应",
    "天气预报", "天气", "降温", "寒潮", "高温",
    "通知", "公告", "通报", "公示", "办法", "条例",
    "实施方案", "工作方案", "实施意见",
    "新闻发布会", "记者会", "发布会",
    "数据发布", "统计数据", "公报",
]

# 分类推断关键词
CATEGORY_KW = {
    "娱乐": ["明星", "电影", "电视剧", "综艺", "歌手", "演员", "导演", "播出", "上映", "音乐", "歌"],
    "体育": ["足球", "篮球", "NBA", "CBA", "世界杯", "奥运", "冠军", "比赛", "球队", "球员", "联赛"],
    "科技": ["AI", "人工智能", "芯片", "手机", "苹果", "华为", "特斯拉", "机器人", "大模型", "GPT", "5G", "科技"],
    "美食": ["美食", "小吃", "火锅", "奶茶", "咖啡", "探店", "食谱", "料理", "甜品"],
    "游戏": ["游戏", "电竞", "王者荣耀", "原神", "LOL", "手游", "端游", "主机"],
    "旅游": ["旅游", "旅行", "景点", "打卡", "周末", "自驾", "户外", "露营"],
    "财经": ["股市", "A股", "基金", "比特币", "房价", "利率", "经济", "理财"],
    "教育": ["高考", "考研", "留学", "大学", "教育", "考试"],
    "情感": ["恋爱", "分手", "婚姻", "相亲", "闺蜜", "前任", "爱情", "情侣"],
    "宠物": ["猫", "狗", "宠物", "萌宠", "猫咪", "狗狗"],
    "时尚": ["穿搭", "美妆", "护肤", "发型", "穿搭", "包包", "化妆"],
}


class TopicScanner:
    """热搜话题扫描 + 过滤"""

    # m.weibo.cn 移动端热搜接口（无需 OAuth，Cookie 即可）
    HOT_SEARCH_URL = (
        "https://m.weibo.cn/api/container/getIndex"
        "?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot"
    )

    def __init__(self, cookies: dict | None = None, extra_filter_keywords: set[str] | None = None,
                 preferred_categories: set[str] | None = None):
        self.cookies = cookies or {}
        self.filter_kw = DEFAULT_FILTER_KEYWORDS | (extra_filter_keywords or set())
        self.preferred = preferred_categories or set()

    # ====== 抓取实时热搜（httpx + Playwright 双通道） ======

    async def fetch_hot_search(self) -> ScanResult:
        """从微博移动端 API 抓取实时热搜榜，httpx 失败时用 Playwright"""
        result = await self._fetch_via_httpx()
        if result.success and result.topics:
            return result

        logger.info("httpx 抓取失败，尝试 Playwright 浏览器抓取...")
        return await self._fetch_via_playwright()

    async def _fetch_via_httpx(self) -> ScanResult:
        """通过 httpx 直接请求 API"""
        try:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/17.0 Mobile/15E148 Safari/604.1"
                ),
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://m.weibo.cn/",
            }
            if cookie_str:
                headers["Cookie"] = cookie_str

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.HOT_SEARCH_URL, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"热搜接口返回 {resp.status_code}")
                    return ScanResult(success=False, error_message=f"HTTP {resp.status_code}")
                data = resp.json()

            return self._parse_hot_search_response(data)

        except Exception as e:
            logger.warning(f"httpx 热搜抓取失败: {e}")
            return ScanResult(success=False, error_message=str(e))

    async def _fetch_via_playwright(self) -> ScanResult:
        """通过 Playwright 浏览器抓取（带 Cookie，防检测）"""
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ])
                context = await browser.new_context(
                    viewport={"width": 430, "height": 932},
                    user_agent=(
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/17.0 Mobile/15E148 Safari/604.1"
                    ),
                    locale="zh-CN",
                )

                # 注入 Cookie
                if self.cookies:
                    pw_cookies = []
                    for name, value in self.cookies.items():
                        pw_cookies.append({
                            "name": name,
                            "value": value,
                            "domain": ".weibo.cn",
                            "path": "/",
                        })
                    await context.add_cookies(pw_cookies)

                page = await context.new_page()

                # 拦截 API 响应，直接拿 JSON
                api_response = None

                async def handle_response(response):
                    nonlocal api_response
                    if "container/getIndex" in response.url and "realtimehot" in response.url:
                        try:
                            api_response = await response.json()
                        except Exception:
                            pass

                page.on("response", handle_response)

                await page.goto(
                    "https://m.weibo.cn/p/106003type=25&t=3&disable_hot=1&filter_type=realtimehot",
                    wait_until="networkidle",
                    timeout=20000,
                )

                await page.wait_for_timeout(2000)  # 等 API 响应
                await browser.close()

                if api_response:
                    logger.info("Playwright 成功抓取热搜数据")
                    return self._parse_hot_search_response(api_response)

                logger.warning("Playwright 未拦截到热搜 API 响应")
                return ScanResult(success=False, error_message="未拦截到热搜数据")

        except Exception as e:
            logger.warning(f"Playwright 热搜抓取失败: {e}")
            return ScanResult(success=False, error_message=str(e))

    def _parse_hot_search_response(self, data: dict) -> ScanResult:
        """解析热搜 API 响应 JSON"""
        cards = data.get("data", {}).get("cards", [])
        topics = []
        for card in cards:
            if not card.get("card_group"):
                continue
            for item in card["card_group"]:
                # 跳过广告位（含"荐"标签的推广内容）
                if (item.get("is_ad_pos") or item.get("topic_ad")
                        or "promotion" in item
                        or "荐" in str(item.get("desc", ""))):
                    logger.debug(f"广告: {item.get('desc','')[:30]}")
                    continue

                name = self._extract_topic_name(item)
                if not name:
                    continue

                heat_val = item.get("desc_extr", 0)
                if isinstance(heat_val, str):
                    heat_val = 0

                desc = item.get("desc", "")
                if isinstance(desc, str) and not desc:
                    desc = ""

                topic = HotTopic(
                    name=name,
                    query=item.get("scheme", ""),
                    heat=int(heat_val) if heat_val else 0,
                    rank=len(topics) + 1,
                    desc=desc if isinstance(desc, str) else "",
                )
                topics.append(topic)

        logger.info(f"解析出 {len(topics)} 个热搜话题")
        return ScanResult(success=True, topics=topics)

    @staticmethod
    def _extract_topic_name(item: dict) -> str:
        """从热搜 item 中提取话题名"""
        # 方式1: 从 itemid 中解析 key:#话题名#
        itemid = item.get("itemid", "")
        if "key:" in itemid:
            import re as _re
            m = _re.search(r'key:#(.+?)#', itemid)
            if m:
                return m.group(1)

        # 方式2: 从 scheme URL 的 q 参数解析
        from urllib.parse import unquote
        scheme = item.get("scheme", "")
        if "q=" in scheme:
            q = scheme.split("q=")[1].split("&")[0]
            q = unquote(q).strip("#")
            return q

        # 方式3: 直接字段
        return (item.get("title_sub") or item.get("title") or "").strip()

    # ====== 备选：使用官方 API trends/hourly ======

    async def fetch_trends_hourly(self, access_token: str) -> ScanResult:
        """通过微博官方 API 获取每小时热门话题"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.weibo.com/2/trends/hourly.json",
                    params={"access_token": access_token, "base_app": 0},
                )
                data = resp.json()

            trends = data.get("trends", {})
            topics = []
            for date_key, trend_list in trends.items():
                for item in trend_list:
                    name = item.get("name", "")
                    if name:
                        topics.append(HotTopic(
                            name=name,
                            query=item.get("query", name),
                            rank=len(topics) + 1,
                        ))

            logger.info(f"官方API获取到 {len(topics)} 个话题")
            return ScanResult(success=True, topics=topics)

        except Exception as e:
            logger.exception(f"官方API获取话题失败: {e}")
            return ScanResult(success=False, error_message=str(e))

    # ====== 话题过滤与评分 ======

    def filter_and_score(self, topics: list[HotTopic]) -> list[HotTopic]:
        """过滤敏感话题，对剩余话题评分排序，返回最适合参与的话题"""
        results = []
        for t in topics:
            # 1. 关键词过滤
            blocked = False
            blocked_reason = ""
            for kw in self.filter_kw:
                if kw in t.name:
                    blocked = True
                    blocked_reason = f"命中过滤词「{kw}」"
                    break

            if blocked:
                logger.debug(f"过滤: {t.name} — {blocked_reason}")
                continue

            # 2. 官方发布类话题检测 → 降权但不完全过滤
            is_official = any(kw in t.name for kw in OFFICIAL_NOTICE_KW)
            if is_official:
                logger.debug(f"官方发布类话题: {t.name}，大幅降权")
                t.heat = -999  # 极低分，只在没有其他选择时才会被选中

            # 3. 推断分类
            t.category = self._classify(t.name)

            # 4. 可参与度评分
            if not is_official:
                t.heat = self._score(t)

            results.append(t)

        # 偏好分类加分
        if self.preferred:
            for t in results:
                if t.category in self.preferred:
                    t.heat += 20

        results.sort(key=lambda x: x.heat, reverse=True)
        return results

    def _classify(self, name: str) -> str:
        for cat, keywords in CATEGORY_KW.items():
            for kw in keywords:
                if kw in name:
                    return cat
        return "综合"

    def _score(self, topic: HotTopic) -> int:
        """综合评分：排名越靠前 + 可参与度越高"""
        score = max(0, 100 - topic.rank * 2)
        # 话题名称长度适中（3-15字）加分
        length = len(topic.name)
        if 3 <= length <= 15:
            score += 10
        elif length > 25:
            score -= 15
        # 非纯数字/英文加分
        if re.search(r'[一-鿿]', topic.name):
            score += 5
        return score

    # ====== 完整扫描流程 ======

    async def scan(self) -> list[HotTopic]:
        """扫描 → 过滤 → 评分，返回候选话题列表"""
        # 加载数据库中的全局屏蔽话题
        await self._load_blocked_from_db()

        # 优先使用网页抓取（更实时）
        result = await self.fetch_hot_search()
        if not result.success:
            logger.warning("网页抓取热搜失败，返回空列表")
            return []

        filtered = self.filter_and_score(result.topics)
        logger.info(f"过滤后剩余 {len(filtered)} 个可参与话题，最佳: {filtered[0].name if filtered else '无'}")
        return filtered

    async def _load_blocked_from_db(self):
        """从数据库加载全局不参与话题到 filter_kw 中"""
        try:
            from app.database import async_session
            from app.models.nurture import NurtureBlockedTopic
            from sqlalchemy import select

            async with async_session() as session:
                result = await session.execute(select(NurtureBlockedTopic))
                topics = result.scalars().all()
                for t in topics:
                    self.filter_kw.add(t.keyword)
            if topics:
                logger.debug(f"从数据库加载了 {len(topics)} 个全局屏蔽话题")
        except Exception as e:
            logger.warning(f"加载全局屏蔽话题失败: {e}")

    def pick_best(self, topics: list[HotTopic], exclude_names: set[str] | None = None) -> HotTopic | None:
        """从候选话题中选出最优的一个，排除已参与过的"""
        exclude = exclude_names or set()
        for t in topics:
            if t.name not in exclude:
                return t
        return None
