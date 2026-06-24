import os
from pathlib import Path
from dotenv import load_dotenv

# 从项目根目录加载 .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


class Settings:
    APP_NAME: str = "AutoTaoke"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/taoke.db")

    # 大淘客
    DATETOKE_APP_KEY: str = os.getenv("DATETOKE_APP_KEY", "")
    DATETOKE_APP_SECRET: str = os.getenv("DATETOKE_APP_SECRET", "")

    # 微博
    WEIBO_APP_KEY: str = os.getenv("WEIBO_APP_KEY", "")
    WEIBO_APP_SECRET: str = os.getenv("WEIBO_APP_SECRET", "")
    WEIBO_REDIRECT_URI: str = os.getenv(
        "WEIBO_REDIRECT_URI", "http://localhost:8000/api/weibo/callback"
    )

    # AI
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    # 中转站地址与模型（Claude 调用统一走这里，而非官方 api.anthropic.com）
    ANTHROPIC_BASE_URL: str = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    ZHIPU_API_KEY: str = os.getenv("ZHIPU_API_KEY", "")

    # 定时任务默认间隔
    DEFAULT_FETCH_INTERVAL_HOURS: int = int(os.getenv("DEFAULT_FETCH_INTERVAL_HOURS", "6"))
    DEFAULT_POST_INTERVAL_HOURS: int = int(os.getenv("DEFAULT_POST_INTERVAL_HOURS", "2"))

    # 图片存储
    IMAGE_STORAGE_PATH: str = os.getenv("IMAGE_STORAGE_PATH", "./data/images")

    # 飞书 AI 资讯群
    FEISHU_APP_ID: str = os.getenv("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET: str = os.getenv("FEISHU_APP_SECRET", "")
    FEISHU_AI_CHAT_ID: str = os.getenv("FEISHU_AI_CHAT_ID", "oc_e99858e2a752caeae351f0226da5a0e1")

    # 养号
    NURTURE_IMAGE_PATH: str = os.getenv("NURTURE_IMAGE_PATH", "./data/nurture_images")
    NURTURE_DEFAULT_INTERVAL_MINUTES: int = int(os.getenv("NURTURE_DEFAULT_INTERVAL_MINUTES", "30"))
    NURTURE_MAX_POSTS_PER_DAY: int = int(os.getenv("NURTURE_MAX_POSTS_PER_DAY", "5"))
    NURTURE_AI_MODEL: str = os.getenv("NURTURE_AI_MODEL", "claude-opus-4-7")
    NURTURE_ENABLE_IMAGE: bool = os.getenv("NURTURE_ENABLE_IMAGE", "true").lower() == "true"


settings = Settings()
