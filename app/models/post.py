import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, comment="关联商品ID")
    account_id: Mapped[int] = mapped_column(Integer, comment="关联账号ID")
    platform: Mapped[str] = mapped_column(String(50), comment="发布平台: weibo")
    content: Mapped[str] = mapped_column(Text, default="", comment="发布文案")
    images: Mapped[str] = mapped_column(Text, default="[]", comment="配图JSON数组")
    external_id: Mapped[str] = mapped_column(String(200), default="", comment="外部平台帖子ID")
    external_url: Mapped[str] = mapped_column(String(1000), default="", comment="外部帖子链接")
    status: Mapped[str] = mapped_column(
        String(20), default="draft", comment="状态: draft/published/failed"
    )
    error_message: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
