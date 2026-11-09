"""RSS 抓取与解析：feedparser + lxml 清洗，带 3 次重试。

并发由调度器控制（最多 4 个源同时抓取）；这里只负责单个源。
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

import feedparser
import httpx

from .errors import FetchError, InvalidFeedError
from .models import FeedEntry, FeedResult
from .net import (
    DEFAULT_TIMEOUT,
    MAX_BYTES,
    Resolver,
    fetch_feed_bytes,
)
from .textutils import clean_html_text, clean_title, make_lead

MAX_ENTRIES = 300
MAX_GUID_LENGTH = 400

ClientFactory = Callable[[], httpx.AsyncClient]
Sleep = Callable[[float], Awaitable[None]]


def _client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(DEFAULT_TIMEOUT),
    )


def _hash_guid(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _normalize_guid(value: str, title: str, body: str) -> str:
    guid = (value or "").strip()
    if not guid:
        guid = _hash_guid(title + body[:10])
    if len(guid) > MAX_GUID_LENGTH:
        guid = _hash_guid(guid)
    return guid


def _entry_body(entry: Any) -> str:
    """优先取完整正文；不同源把正文放在 content / summary / description。"""

    candidates: list[str] = []
    for content in entry.get("content") or ():
        value = content.get("value") if isinstance(content, dict) else None
        if value:
            candidates.append(str(value))
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            candidates.append(str(value))
    cleaned = [clean_html_text(candidate) for candidate in candidates]
    cleaned = [text for text in cleaned if text]
    if not cleaned:
        return ""
    return max(cleaned, key=len)


def _entry_pubdate(entry: Any, fetched_at: int) -> int:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return int(calendar.timegm(parsed))
        except (TypeError, ValueError, OverflowError):
            pass
    return fetched_at


def _entry_link(entry: Any, feed_link: str) -> str:
    link = str(entry.get("link") or "").strip()
    if link and feed_link:
        from urllib.parse import urljoin

        return urljoin(feed_link, link)
    return link


def parse_feed(data: bytes, *, fetched_at: int) -> FeedResult:
    """解析 RSS/Atom 字节，返回去重后的文章列表。"""

    parsed = feedparser.parse(data)
    feed = getattr(parsed, "feed", None) or {}
    entries = getattr(parsed, "entries", None) or []
    if not entries and not feed and not getattr(parsed, "version", ""):
        raise InvalidFeedError("返回内容不是有效的 RSS/Atom 源")
    feed_title = clean_title(feed.get("title", ""))
    feed_link = str(feed.get("link") or "").strip()
    seen: set[str] = set()
    result: list[FeedEntry] = []
    for entry in entries[:MAX_ENTRIES]:
        title = clean_title(entry.get("title", ""))
        body = _entry_body(entry)
        link = _entry_link(entry, feed_link)
        raw_guid = entry.get("id") or entry.get("guid") or link
        guid = _normalize_guid(str(raw_guid or ""), title, body)
        if guid in seen:
            continue
        seen.add(guid)
        result.append(
            FeedEntry(
                guid=guid,
                title=title,
                link=link,
                body=body,
                summary=make_lead(body),
                pubdate=_entry_pubdate(entry, fetched_at),
            )
        )
    return FeedResult(title=feed_title, entries=tuple(result))


class RssFetcher:
    """单个 RSS 源的下载与解析：失败后最多重试 3 次（共 4 次尝试）。"""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retry_delay: float = 30.0,
        max_attempts: int = 4,
        max_bytes: int = MAX_BYTES,
        client_factory: ClientFactory | None = None,
        resolver: Resolver | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.max_attempts = max(1, int(max_attempts))
        self.max_bytes = max_bytes
        self._client_factory = client_factory or _client_factory
        self._resolver = resolver
        self._sleep = sleep
        self._clock = clock

    async def fetch(self, url: str) -> FeedResult:
        last_error: FetchError | None = None
        async with self._client_factory() as client:
            for attempt in range(self.max_attempts):
                try:
                    data, _content_type, _final_url = await fetch_feed_bytes(
                        url,
                        client=client,
                        timeout=self.timeout,
                        max_bytes=self.max_bytes,
                        resolver=self._resolver,
                    )
                except FetchError as exc:
                    last_error = exc
                    if not exc.retryable or attempt >= self.max_attempts - 1:
                        raise
                    await self._sleep(self.retry_delay)
                    continue
                if not data:
                    raise InvalidFeedError("RSS 源返回了空内容")
                fetched_at = int(self._clock())
                return parse_feed(data, fetched_at=fetched_at)
        raise last_error or FetchError("RSS 抓取失败")


__all__ = ["MAX_ENTRIES", "RssFetcher", "parse_feed"]
