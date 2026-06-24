import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), comment="平台: weibo/wechat")
    name: Mapped[str] = mapped_column(String(100), comment="账号名称/备注")
    uid: Mapped[str] = mapped_column(String(100), default="", comment="平台用户ID")
    access_token: Mapped[str] = mapped_column(Text, default="", comment="Access Token")
    refresh_token: Mapped[str] = mapped_column(Text, default="", comment="Refresh Token")
    token_expires_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    cookies: Mapped[str] = mapped_column(Text, default="", comment="网页登录Cookie JSON")
    extra_data: Mapped[str] = mapped_column(Text, default="{}", comment="额外数据JSON")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
