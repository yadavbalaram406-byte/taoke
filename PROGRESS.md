# 项目进度 PROGRESS

> 微博自动发布系统（taoke）—— Python / FastAPI / Playwright
> 最后更新：2026-06-11

## 项目概览

两类自动发布任务，跑在 APScheduler（IntervalTrigger）上：

1. **常规养号（蹭热搜）** —— 账号「思澈同学」（account_id=1）
   - 抓微博热搜话题 → 用「温暖正面 / 干货」风格写文案 → 发布
2. **科技博主** —— 账号「带货测试」（account_id=2）
   - 抓海外科技/AI/金融资讯 → 深度单篇解读 → 配原文截图 → 发布

后台页面：`/admin` → 自动养号，分两个 tab（常规养号 / 科技博主）。

## 关键文件

- `app/services/techblog/fetcher.py` —— 多源资讯抓取 + 来源截图
  - `TWITTER_TEST = True`（约 line 78）：当前为**纯 Twitter 模式**，只抓推特
  - `screenshot_source_page()`：Twitter 用移动端视口 430×932 截图
- `app/services/techblog/twitter.py` —— Twitter/X 登录 + 抓推
  - `AI_ACCOUNTS`：54 个账号（含 elonmusk、realDonaldTrump、SpaceX、Tesla、OpenAI 等）
  - Cookie 存 `twitter_cookies.json`，登录用系统 Chrome（`channel="chrome"`）
- `app/services/techblog/bulletin.py` —— 选题 + AI 解读 + 智能话题标签
  - 按 freshness 排序，话题标签优先匹配微博科技分榜（cate=10103）
- `app/services/techblog/service.py` —— 科技博主发布主流程
- `app/services/nurture/nurture_service.py` —— 蹭热搜主流程
- `app/services/nurture/topic_scanner.py` —— 热搜话题抓取 + 政治词过滤
- `app/services/nurture/content_writer.py` —— 文案生成（4 种风格）
- `app/services/publisher/weibo_web.py` —— Playwright 发布到微博网页版
- `app/routers/nurture.py` —— 后台 API（含账号唯一性校验、Twitter 登录接口）
- `app/templates/nurture.html` —— 后台 tab 页面

## 已完成

- [x] 自动养号页面改为 tab 布局（常规养号 / 科技博主）
- [x] 科技博文：从聚合小日报改为单篇深度解读
- [x] 接入 Twitter/X 作为内容源（Playwright 抓取）
- [x] 修复 NameError 崩溃（`best` 在 SPORTS_REMIX_KW 检查处被提前引用）
- [x] 修复「假成功」发布（textarea 非空 = 失败，不再假定成功）
- [x] 修复账号串号（techPublishNow 动态取 schedule 的 account_id）
- [x] 修复调度器死循环（所有提前 return 都调 `_touch()` 更新 last_run_at）
- [x] 过滤政治/外交话题（朝鲜、金正恩、普京、国事访问等 30+ 词）
- [x] 修复 AI 误解话题名（文案 prompt 加入「话题简介」帮助理解）
- [x] 账号唯一性：一个账号同时只能有一个活跃任务

## 待办 / 未确认

- [ ] **Twitter App 下载弹窗截图问题**：移动端网页打开推文必弹「See this post in the app」浮层。
      当前方案：JS 点击 `[aria-label="Close"]` 关闭按钮 + Escape 兜底（`fetcher.py` screenshot_source_page）。
      **尚未确认是否生效**。备选方案：
      - 用 Playwright 原生 locator `.click()` 替代 JS evaluate
      - 拦截/屏蔽加载弹窗的网络请求
      - 页面加载前注入 CSS `display:none` 隐藏弹窗元素
      - 只截推文元素的 bounding box（clip 到 article 元素）
- [ ] **确认蹭热搜任务正常运行**：NameError + `_touch()` 修复后，需验证「思澈同学」的养号任务真的在跑。

## 运行方式

终端启动服务（非后台调度器独立进程）：
```
python3 /Users/gemini/vscode/taoke/run.py
```
后台地址：http://localhost:8000/admin

## 内容策略备注

- 政治/外交相关话题流量为零，已加入过滤，不参与。
- 科技博文文案要求：开头点明信息来源身份（如「OpenAI 研究员 XX 发推表示」），
  2-3 个解读角度，全程中文，300-500 字。
- 话题标签不要泛泛的 `#日报#`，优先用微博科技分榜的真实热搜词。
