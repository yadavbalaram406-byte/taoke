import datetime
from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    name: str = Field(..., max_length=100)
    task_type: str = Field(..., max_length=50)
    cron_expression: str = Field(..., max_length=100)
    source_id: int | None = None
    account_id: int | None = None
    copy_style: str = Field(default="discount", max_length=50)
    max_products_per_run: int = Field(default=1)
    refresh_before_post: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    copy_style: str | None = None
    max_products_per_run: int | None = None
    refresh_before_post: bool | None = None
    is_active: bool | None = None


class ScheduleRead(ScheduleCreate):
    id: int
    is_active: bool
    last_run_at: datetime.datetime | None = None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}
