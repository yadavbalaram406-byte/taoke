from app.routers import products, sources, accounts, schedules, dashboard, publish, weibo_auth, weibo_web, nurture

routers = [
    products.router,
    sources.router,
    accounts.router,
    schedules.router,
    dashboard.router,
    publish.router,
    publish.fetch_router,
    publish.earnings_router,
    weibo_auth.router,
    weibo_web.router,
    nurture.router,
    nurture.admin,
]
