from app.services.fetcher.base import BaseFetcher, FetchResult
from app.services.fetcher.dataoke import DataokeFetcher
from app.services.fetcher.taobao import TaobaoFetcher
from app.services.fetcher.jd import JdFetcher
from app.services.fetcher.pdd import PddFetcher

PLATFORM_FETCHERS = {
    "dataoke": DataokeFetcher,
    "taobao": TaobaoFetcher,
    "jd": JdFetcher,
    "pdd": PddFetcher,
}


def get_fetcher(platform: str, app_key: str = "", app_secret: str = "") -> BaseFetcher:
    fetcher_cls = PLATFORM_FETCHERS.get(platform)
    if not fetcher_cls:
        raise ValueError(f"不支持的平台: {platform}")
    return fetcher_cls(app_key=app_key, app_secret=app_secret)


__all__ = [
    "BaseFetcher", "FetchResult",
    "DataokeFetcher", "TaobaoFetcher", "JdFetcher", "PddFetcher",
    "get_fetcher", "PLATFORM_FETCHERS",
]
