"""数据库行与业务对象的轻量数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    name: str
    url: str
    weight: int
    created_at: int
    pull_at: int | None
    article_count: int = 0


@dataclass(frozen=True, slots=True)
class Article:
    source_id: int
    guid: str
    title: str
    link: str
    body: str
    summary: str
    pubdate: int

    @property
    def display_title(self) -> str:
        return self.title or "（无标题）"


@dataclass(frozen=True, slots=True)
class Destination:
    destination_id: int
    last_sent: int | None
    next_send: int | None
    enabled: bool
    reason: str | None
    consecutive_failed_rounds: int = 0


@dataclass(frozen=True, slots=True)
class ArticlePreview:
    source_id: int
    source_name: str
    guid: str
    title: str
    link: str
    pubdate: int
    has_body: bool
    deleted: bool


@dataclass(frozen=True, slots=True)
class FeedEntry:
    guid: str
    title: str
    link: str
    body: str
    summary: str
    pubdate: int


@dataclass(frozen=True, slots=True)
class FeedResult:
    title: str
    entries: tuple[FeedEntry, ...]


@dataclass(frozen=True, slots=True)
class PullReport:
    due: int = 0
    succeeded: int = 0
    failed: int = 0
    inserted: int = 0
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class SendReport:
    destinations: int = 0
    messages: int = 0
    sent: int = 0
    failed: int = 0
    disabled: tuple[int, ...] = ()


__all__ = [
    "Article",
    "ArticlePreview",
    "Destination",
    "FeedEntry",
    "FeedResult",
    "PullReport",
    "SendReport",
    "Subscription",
]
