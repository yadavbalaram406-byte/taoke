from app.services.fetcher.base import BaseFetcher, FetchResult


class JdFetcher(BaseFetcher):
    """
    京东联盟API抓取器
    https://union.jd.com/
    """
    platform = "jd"

    def __init__(self, app_key: str = "", app_secret: str = ""):
        self.app_key = app_key
        self.app_secret = app_secret

    async def fetch_products(self, page: int = 1, page_size: int = 20) -> FetchResult:
        return FetchResult(success=True, products=[], total_count=0,
                           error_message="京东联盟Fetcher待实现")

    async def fetch_product_detail(self, source_id: str) -> dict | None:
        return None
