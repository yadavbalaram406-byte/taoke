import datetime
from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    platform: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    uid: str = Field(default="", max_length=100)
    access_token: str = Field(default="")
    refresh_token: str = Field(default="")
    token_expires_at: datetime.datetime | None = None
    extra_data: str = Field(default="{}")
    is_active: bool = True


class AccountUpdate(BaseModel):
    name: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime.datetime | None = None
    extra_data: str | None = None
    is_active: bool | None = None


class AccountRead(AccountCreate):
    id: int
    created_at: datetime.datetime

    model_config = {"from_attributes": True}
