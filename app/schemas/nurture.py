import datetime
from pydantic import BaseModel


class NurtureTopicRead(BaseModel):
    id: int
    topic_name: str
    heat_score: int
    category: str
    rank: int
    is_suitable: bool
    filter_reason: str
    scanned_at: datetime.datetime

    model_config = {"from_attributes": True}


class NurtureRecordRead(BaseModel):
    id: int
    topic_name: str
    content: str
    image_local: str
    image_prompt: str
    account_id: int
    external_url: str
    external_id: str
    views: int
    status: str
    error_message: str
    published_at: datetime.datetime | None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class NurtureRecordPage(BaseModel):
    items: list[NurtureRecordRead]
    total: int
    page: int
    page_size: int
    total_pages: int
    daily_views: int


class NurtureScheduleCreate(BaseModel):
    name: str
    account_id: int
    interval_minutes: int = 30
    max_posts_per_day: int = 5
    content_style: str = "sharp"  # sharp/humorous/knowledge/warm/rotate
    filter_keywords: str = ""  # 逗号分隔的自定义过滤词
    preferred_categories: str = ""  # 逗号分隔的偏好分类
    enable_image: bool = True
    active_start_hour: int = 7   # 北京时，发布开始时（含），默认 7:00
    active_end_hour: int = 23    # 北京时，发布结束时（不含），默认 23:00
    is_active: bool = True


class NurtureScheduleRead(BaseModel):
    id: int
    name: str
    account_id: int | None
    interval_minutes: int
    max_posts_per_day: int
    content_style: str
    filter_keywords: str
    preferred_categories: str
    enable_image: bool
    active_start_hour: int
    active_end_hour: int
    is_active: bool
    last_run_at: datetime.datetime | None
    today_post_count: int = 0
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class NurtureScheduleUpdate(BaseModel):
    name: str | None = None
    account_id: int | None = None
    interval_minutes: int | None = None
    max_posts_per_day: int | None = None
    content_style: str | None = None
    filter_keywords: str | None = None
    preferred_categories: str | None = None
    enable_image: bool | None = None
    active_start_hour: int | None = None
    active_end_hour: int | None = None
    is_active: bool | None = None
