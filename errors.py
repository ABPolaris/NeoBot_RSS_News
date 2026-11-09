"""业务层使用的中文错误；动作层再转换成可公开的 ActionError。"""

from __future__ import annotations


class RssNewsError(Exception):
    """插件业务错误的基类。"""


class InvalidInputError(RssNewsError):
    """用户输入不合法，消息可直接展示给用户。"""


class SubscriptionLimitError(InvalidInputError):
    """订阅数量达到上限。"""


class DuplicateValueError(InvalidInputError):
    """URL 或权重与已有订阅重复。"""


class NotFoundError(RssNewsError):
    """记录不存在或已被删除。"""


class FetchError(RssNewsError):
    """抓取 RSS 失败。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class UnsafeURLError(FetchError):
    """URL 未通过 SSRF 防护。"""


class InvalidFeedError(FetchError):
    """返回内容不是可解析的 RSS/Atom。"""


class DeliveryError(RssNewsError):
    """向接收者发送消息失败；消息文本不包含文章内容。"""


__all__ = [
    "DeliveryError",
    "DuplicateValueError",
    "FetchError",
    "InvalidFeedError",
    "InvalidInputError",
    "NotFoundError",
    "RssNewsError",
    "SubscriptionLimitError",
    "UnsafeURLError",
]
