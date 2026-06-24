import datetime
from sqlalchemy import String, Integer, DateTime, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), comment="任务名称")
    task_type: Mapped[str] = mapped_column(
        String(50), comment="任务类型: fetch_products/publish_post/full_cycle"
    )
    cron_expression: Mapped[str] = mapped_column(String(100), comment="Cron表达式")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="商品源ID")
    account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="发布账号ID")
    copy_style: Mapped[str] = mapped_column(
        String(50), default="discount", comment="文案风格: discount/content"
    )
    max_products_per_run: Mapped[int] = mapped_column(Integer, default=1, comment="每次发布数量")
    refresh_before_post: Mapped[bool] = mapped_column(Boolean, default=True, comment="发前刷新商品库")
    extra_data: Mapped[str] = mapped_column(Text, default="{}", comment="额外配置JSON（养号等扩展使用）")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_posted_product_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="上次发布商品ID")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
