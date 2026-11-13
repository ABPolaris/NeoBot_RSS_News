"""插件动作的输入/输出模型；面板与 iframe 页面共用这些结构。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .config import ContentFormat

QQ_PATTERN = r"^[1-9]\d{4,12}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubscriptionItem(StrictModel):
    id: int
    name: str
    url: str
    nickname: str = ""
    weight: int
    created_at: int
    pull_at: int | None = None
    article_count: int = 0


class SubscriptionCreateInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    nickname: str = Field(default="", max_length=64)
    weight: int | None = Field(default=None, ge=1, le=10)


class SubscriptionUpdateInput(StrictModel):
    id: int = Field(gt=0)
    name: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2048)
    nickname: str | None = Field(default=None, max_length=64)
    weight: int | None = Field(default=None, ge=1, le=10)


class SubscriptionRemoveInput(StrictModel):
    id: int = Field(gt=0)


class SubscriptionListOutput(StrictModel):
    items: list[SubscriptionItem]
    max_count: int
    available_weights: list[int]
    warning: str | None = None


class SubscriptionCreateOutput(StrictModel):
    item: SubscriptionItem
    warning: str | None = None


class SubscriptionUpdateOutput(StrictModel):
    item: SubscriptionItem
    url_changed: bool
    warning: str | None = None


class RemovedOutput(StrictModel):
    removed: bool


class ArticleItem(StrictModel):
    source_id: int
    source_name: str
    guid: str
    title: str
    link: str
    pubdate: int
    has_body: bool


class ArticleListInput(StrictModel):
    limit: int = Field(default=30, ge=1, le=200)


class ArticleListOutput(StrictModel):
    items: list[ArticleItem]


class StatusOutput(StrictModel):
    server_time: int
    send_enabled: bool
    content_format: str
    per_source_limit: int
    per_destination_limit: int
    pull_interval_minutes: int
    cache_days: int
    message_interval_seconds: int
    subscription_count: int
    article_count: int
    latest_pull_at: int | None = None
    warning: str | None = None
    supports_send_test: bool = True


class SendTestInput(StrictModel):
    qq: str = Field(pattern=QQ_PATTERN)
    content_format: ContentFormat


class SendTestOutput(StrictModel):
    sent: bool
    destination_id: int
    source_id: int
    source_name: str
    title: str
    pubdate: int
    message_chars: int


__all__ = [
    "QQ_PATTERN",
    "ArticleItem",
    "ArticleListInput",
    "ArticleListOutput",
    "RemovedOutput",
    "SendTestInput",
    "SendTestOutput",
    "StatusOutput",
    "SubscriptionCreateInput",
    "SubscriptionCreateOutput",
    "SubscriptionItem",
    "SubscriptionListOutput",
    "SubscriptionRemoveInput",
    "SubscriptionUpdateInput",
    "SubscriptionUpdateOutput",
]
