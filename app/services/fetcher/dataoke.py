import hashlib
import time
import httpx
from loguru import logger

from app.config import settings
from app.services.fetcher.base import BaseFetcher, FetchResult


class DataokeFetcher(BaseFetcher):
    """大淘客API抓取器 https://www.dataoke.com"""

    platform = "dataoke"
    BASE_URL = "https://openapi.dataoke.com/api"

    def __init__(self, app_key: str = "", app_secret: str = ""):
        self.app_key = app_key or settings.DATETOKE_APP_KEY
        self.app_secret = app_secret or settings.DATETOKE_APP_SECRET

    def _sign(self, params: dict) -> dict:
        """生成大淘客签名"""
        params["appKey"] = self.app_key
        params["version"] = "v1.2.4"
        keys = sorted(params.keys())
        sign_str = "&".join(f"{k}={params[k]}" for k in keys)
        sign_str = f"{sign_str}&key={self.app_secret}"
        params["sign"] = hashlib.md5(sign_str.encode()).hexdigest().upper()
        return params

    async def _request(self, path: str, params: dict) -> dict:
        url = f"{self.BASE_URL}{path}"
        params = self._sign(params)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                logger.error(f"大淘客API错误: code={data.get('code')}, msg={data.get('msg')}")
                return {"error": data.get("msg", "未知错误"), "data": []}
            return data.get("data", {})

    def _map_product(self, item: dict) -> dict:
        """统一字段映射：API返回 → 数据库字段"""
        actual_price = float(item.get("actualPrice", 0))
        commission_rate = float(item.get("commissionRate", 0))
        return {
            "source": "dataoke",
            "source_id": str(item.get("id", "")),
            "goods_id": str(item.get("goodsId", "")),
            "title": item.get("title", ""),
            "short_title": item.get("dtitle", ""),
            "price": float(item.get("originalPrice", 0)),
            "coupon_price": actual_price,
            "coupon_amount": float(item.get("couponPrice", 0)),
            "coupon_link": item.get("couponLink", ""),
            "commission_rate": commission_rate,
            "commission": round(actual_price * commission_rate / 100, 2),
            "sales_volume": int(item.get("monthSales", 0)),
            "shop_name": item.get("shopName", ""),
            "image_url": item.get("mainPic", ""),
            "detail_images": item.get("detailPics", "[]"),
            "description": item.get("desc", ""),
        }

    async def fetch_products(self, page: int = 1, page_size: int = 20) -> FetchResult:
        try:
            params = {
                "pageId": str(page),
                "pageSize": str(page_size),
                "sort": "0",
            }
            data = await self._request("/goods/get-goods-list", params)
            if "error" in data:
                return FetchResult(success=False, products=[], error_message=data["error"])

            raw_list = data.get("list", [])
            products = [self._map_product(item) for item in raw_list]

            return FetchResult(
                success=True,
                products=products,
                total_count=int(data.get("totalNum", 0)),
            )
        except Exception as e:
            logger.exception(f"大淘客抓取失败: {e}")
            return FetchResult(success=False, products=[], error_message=str(e))

    async def fetch_product_detail(self, source_id: str) -> dict | None:
        try:
            params = {"id": source_id}
            data = await self._request("/goods/get-goods-details", params)
            if "error" in data:
                return None
            return self._map_product(data)
        except Exception as e:
            logger.exception(f"大淘客商品详情获取失败: {e}")
            return None

    # ====== 转链 ======

    async def get_privilege_link(self, goods_id: str, pid: str = "") -> dict | None:
        """
        高效转链：生成带PID的CPS推广链接和淘口令
        API: /api/tb-service/get-privilege-link
        PID已在大淘客后台绑定，默认不传
        """
        try:
            params = {"goodsId": goods_id}
            if pid:
                params["pid"] = pid
            data = await self._request("/tb-service/get-privilege-link", params)

            if "error" in data:
                logger.error(f"转链失败: {data['error']}")
                return None

            return {
                "cps_link": data.get("couponClickUrl", ""),
                "tao_password": data.get("tpwd", ""),
                "long_tao_password": data.get("longTpwd", ""),
                "short_url": data.get("shortUrl", ""),
                "max_commission_rate": float(data.get("maxCommissionRate", 0)),
                "coupon_info": data.get("couponInfo", ""),
            }
        except Exception as e:
            logger.exception(f"转链异常: {e}")
            return None

    async def batch_convert_links(self, products: list[dict]) -> list[dict]:
        """批量转链：为商品列表生成CPS链接和淘口令"""
        for p in products:
            goods_id = p.get("goods_id", "")
            if not goods_id:
                logger.warning(f"商品 {p.get('short_title', '?')} 缺少 goods_id，跳过转链")
                continue

            # 最多重试3次
            for attempt in range(3):
                try:
                    link_data = await self.get_privilege_link(goods_id)
                    if link_data:
                        p["cps_link"] = link_data["cps_link"]
                        p["tao_password"] = link_data["tao_password"]
                        logger.info(f"转链成功: {p.get('short_title', '?')[:30]} → {link_data['cps_link'][:60]}...")
                        break
                    else:
                        if attempt < 2:
                            time.sleep(1.0)
                        else:
                            p["cps_link"] = ""
                            p["tao_password"] = ""
                except Exception as e:
                    if attempt < 2:
                        logger.warning(f"转链重试 {attempt + 1}: {e}")
                        time.sleep(1.5)
                    else:
                        logger.error(f"转链失败(已重试3次): {p.get('short_title', '?')}")
                        p["cps_link"] = ""
                        p["tao_password"] = ""

            # 避免频繁请求
            time.sleep(0.3)

        return products

    # ====== 订单/收益查询 ======

    async def get_order_details(
        self,
        start_time: str,
        end_time: str,
        order_scene: int = 1,
        query_type: int = 1,
        page_size: int = 100,
        page_no: int = 1,
        position_index: str = "",
    ) -> dict:
        """
        订单查询
        API: /api/tb-service/get-order-details
        start_time/end_time 间隔不超过3小时
        query_type: 1=付款时间, 2=结算时间, 3=创建时间
        order_scene: 1=常规, 2=渠道, 3=会员运营
        返回: orders列表 + 汇总
        """
        try:
            params = {
                "queryType": str(query_type),
                "startTime": start_time,
                "endTime": end_time,
                "pageSize": str(page_size),
                "pageNo": str(page_no),
                "orderScene": str(order_scene),
            }
            if position_index:
                params["positionIndex"] = position_index

            data = await self._request("/tb-service/get-order-details", params)

            if "error" in data:
                return {"success": False, "error": data["error"], "orders": []}

            raw_orders = data.get("results", []) or data.get("list", [])
            orders = []
            total_estimated = 0.0
            total_settled = 0.0

            for o in raw_orders:
                pay_fee = float(o.get("pub_share_pre_fee", 0))
                settle_fee = float(o.get("pub_share_fee", 0))
                total_estimated += pay_fee
                total_settled += settle_fee

                status_map = {12: "已付款", 13: "已失效", 14: "已收货", 3: "已结算"}
                orders.append({
                    "order_id": str(o.get("tb_trade_parent_id", o.get("trade_parent_id", ""))),
                    "item_title": o.get("item_title", ""),
                    "pay_fee": pay_fee,
                    "settle_fee": settle_fee,
                    "status": int(o.get("tk_status", 0)),
                    "status_label": status_map.get(int(o.get("tk_status", 0)), "未知"),
                    "create_time": o.get("tb_trade_create_time", ""),
                    "earning_time": o.get("earning_time", ""),
                    "refund_status": int(o.get("refund_status", 0)),
                })

            return {
                "success": True,
                "orders": orders,
                "summary": {
                    "total_estimated": round(total_estimated, 2),
                    "total_settled": round(total_settled, 2),
                    "order_count": len(orders),
                },
            }
        except Exception as e:
            logger.exception(f"订单查询失败: {e}")
            return {"success": False, "error": str(e), "orders": []}
