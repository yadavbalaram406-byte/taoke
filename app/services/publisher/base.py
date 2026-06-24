from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PublishResult:
    success: bool
    external_id: str = ""
    external_url: str = ""
    error_message: str = ""


class BasePublisher(ABC):
    platform: str = ""

    @abstractmethod
    async def publish(self, content: str, images: list[str] = None) -> PublishResult:
        """发布内容，返回发布结果"""
        ...

    @abstractmethod
    async def check_token(self) -> bool:
        """检查 access_token 是否有效"""
        ...
