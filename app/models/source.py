import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), comment="来源名称")
    platform: Mapped[str] = mapped_column(String(50), comment="平台: dataoke/taobao/jd/pdd")
    app_key: Mapped[str] = mapped_column(String(200), default="", comment="API Key")
    app_secret: Mapped[str] = mapped_column(String(500), default="", comment="API Secret")
    extra_config: Mapped[str] = mapped_column(Text, default="{}", comment="额外配置JSON")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
