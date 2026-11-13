"""数据库行与业务对象的轻量数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Subscription:
    id: int
    name: str
    url: str
    nickname: str
    weight: int
    created_at: int
    pull_at: int | None
    article_count: int = 0

    @property
    def display_name(self) -> str:
        return f"{self.name}（{self.nickname}）" if self.nickname else self.name


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
class ArticlePreview:
    source_id: int
    source_name: str
    guid: str
    title: str
    link: str
    pubdate: int
    has_body: bool


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


__all__ = [
    "Article",
    "ArticlePreview",
    "FeedEntry",
    "FeedResult",
    "Subscription",
]
