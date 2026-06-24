import datetime
from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str = Field(..., max_length=100)
    platform: str = Field(..., max_length=50)
    app_key: str = Field(default="", max_length=200)
    app_secret: str = Field(default="", max_length=500)
    extra_config: str = Field(default="{}")
    is_active: bool = True


class SourceUpdate(BaseModel):
    name: str | None = None
    app_key: str | None = None
    app_secret: str | None = None
    extra_config: str | None = None
    is_active: bool | None = None


class SourceRead(SourceCreate):
    id: int
    last_fetch_at: datetime.datetime | None = None
    created_at: datetime.datetime

    model_config = {"from_attributes": True}
