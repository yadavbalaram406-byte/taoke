import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import routers
from app.services.scheduler import start_scheduler, shutdown_scheduler, load_schedules_from_db
from app.templates_env import templates_env

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await load_schedules_from_db()
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.state.templates = templates_env
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

for router in routers:
    app.include_router(router)
