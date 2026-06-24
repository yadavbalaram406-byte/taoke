import datetime
from sqlalchemy import String, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class NurtureBlockedTopic(Base):
    """全局不参与话题库 — 永久过滤"""
    __tablename__ = "nurture_blocked_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(200), unique=True, comment="屏蔽关键词")
    reason: Mapped[str] = mapped_column(String(500), default="", comment="屏蔽原因")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class NurtureIncident(Base):
    """经验教训日志 — 记录运营过程中遇到的问题和解决方案"""
    __tablename__ = "nurture_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(50), default="bug",
                                          comment="分类: bug/filter/content/strategy")
    title: Mapped[str] = mapped_column(String(300), comment="问题标题")
    detail: Mapped[str] = mapped_column(Text, default="", comment="详细描述")
    solution: Mapped[str] = mapped_column(Text, default="", comment="解决方案")
    severity: Mapped[str] = mapped_column(String(20), default="medium",
                                          comment="严重程度: critical/high/medium/low")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class NurtureTopic(Base):
    """热搜话题 — 扫描入库的候选话题"""
    __tablename__ = "nurture_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_name: Mapped[str] = mapped_column(String(200), comment="话题名称")
    topic_query: Mapped[str] = mapped_column(String(200), default="", comment="搜索查询词")
    heat_score: Mapped[int] = mapped_column(Integer, default=0, comment="热度值")
    category: Mapped[str] = mapped_column(String(50), default="", comment="分类")
    rank: Mapped[int] = mapped_column(Integer, default=0, comment="热搜排名")
    is_suitable: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否适合参与")
    filter_reason: Mapped[str] = mapped_column(String(500), default="", comment="过滤原因")
    raw_data: Mapped[str] = mapped_column(Text, default="{}", comment="原始JSON数据")
    scanned_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )


class NurtureRecord(Base):
    """养号记录 — 每次发布的完整记录"""
    __tablename__ = "nurture_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_name: Mapped[str] = mapped_column(String(200), comment="参与的话题")
    content: Mapped[str] = mapped_column(Text, default="", comment="发布文案")
    image_local: Mapped[str] = mapped_column(String(500), default="", comment="配图本地路径")
    image_prompt: Mapped[str] = mapped_column(Text, default="", comment="配图生成提示词")
    account_id: Mapped[int] = mapped_column(Integer, comment="发布账号ID")
    external_url: Mapped[str] = mapped_column(String(1000), default="", comment="微博链接")
    external_id: Mapped[str] = mapped_column(String(200), default="", comment="微博帖子ID")
    views: Mapped[int] = mapped_column(Integer, default=0, comment="阅读量")
    reposts: Mapped[int] = mapped_column(Integer, default=0, comment="转发数")
    comments: Mapped[int] = mapped_column(Integer, default=0, comment="评论数")
    likes: Mapped[int] = mapped_column(Integer, default=0, comment="点赞数")
    content_style: Mapped[str] = mapped_column(String(20), default="", comment="使用的文案风格")
    status: Mapped[str] = mapped_column(
        String(20), default="draft", comment="状态: draft/published/failed"
    )
    error_message: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
