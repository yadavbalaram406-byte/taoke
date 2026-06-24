import datetime
from sqlalchemy import String, Float, Integer, DateTime, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), comment="商品来源: dataoke/taobao/jd/pdd")
    source_id: Mapped[str] = mapped_column(String(100), comment="来源平台商品ID(大淘客ID)")
    goods_id: Mapped[str] = mapped_column(String(100), default="", comment="淘宝商品ID(转链用)")
    title: Mapped[str] = mapped_column(String(500), comment="商品标题")
    short_title: Mapped[str] = mapped_column(String(200), default="", comment="短标题")
    price: Mapped[float] = mapped_column(Float, default=0.0, comment="原价")
    coupon_price: Mapped[float] = mapped_column(Float, default=0.0, comment="券后价")
    coupon_amount: Mapped[float] = mapped_column(Float, default=0.0, comment="优惠券金额")
    coupon_link: Mapped[str] = mapped_column(String(1000), default="", comment="优惠券链接")
    cps_link: Mapped[str] = mapped_column(String(1000), default="", comment="转链后CPS推广链接")
    tao_password: Mapped[str] = mapped_column(String(500), default="", comment="淘口令")
    commission_rate: Mapped[float] = mapped_column(Float, default=0.0, comment="佣金比例")
    commission: Mapped[float] = mapped_column(Float, default=0.0, comment="预估佣金")
    sales_volume: Mapped[int] = mapped_column(Integer, default=0, comment="销量")
    shop_name: Mapped[str] = mapped_column(String(200), default="", comment="店铺名称")
    image_url: Mapped[str] = mapped_column(String(1000), default="", comment="主图URL")
    image_local: Mapped[str] = mapped_column(String(500), default="", comment="本地图片路径")
    detail_images: Mapped[str] = mapped_column(Text, default="", comment="详情图JSON数组")
    description: Mapped[str] = mapped_column(Text, default="", comment="商品描述")
    score: Mapped[float] = mapped_column(Float, default=0.0, comment="综合评分")
    status: Mapped[str] = mapped_column(String(20), default="active", comment="状态: active/archived")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
