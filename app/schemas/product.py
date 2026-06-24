import datetime
from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    source: str = Field(..., max_length=50)
    source_id: str = Field(..., max_length=100)
    goods_id: str = Field(default="", max_length=100)
    title: str = Field(..., max_length=500)
    short_title: str = Field(default="", max_length=200)
    price: float = Field(default=0.0)
    coupon_price: float = Field(default=0.0)
    coupon_amount: float = Field(default=0.0)
    coupon_link: str = Field(default="", max_length=1000)
    cps_link: str = Field(default="", max_length=1000)
    tao_password: str = Field(default="", max_length=500)
    commission_rate: float = Field(default=0.0)
    commission: float = Field(default=0.0)
    sales_volume: int = Field(default=0)
    shop_name: str = Field(default="", max_length=200)
    image_url: str = Field(default="", max_length=1000)
    image_local: str = Field(default="", max_length=500)
    detail_images: str = Field(default="")
    description: str = Field(default="")
    score: float = Field(default=0.0)


class ProductUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    short_title: str | None = Field(default=None, max_length=200)
    cps_link: str | None = None
    tao_password: str | None = None
    coupon_price: float | None = None
    score: float | None = None
    status: str | None = None


class ProductRead(ProductCreate):
    id: int
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}
