"""feedparser 解析、guid 回退规则与 3 次重试行为。"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

import httpx
import pytest
from NeoBot_RSS_News.errors import FetchError, InvalidFeedError, UnsafeURLError
from NeoBot_RSS_News.fetch import RssFetcher, parse_feed

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>示例源</title>
    <link>https://example.com/</link>
    <item>
      <title>第一篇 &amp; 标题</title>
      <link>https://example.com/1</link>
      <guid>g-1</guid>
      <pubDate>Sun, 08 Nov 2026 10:00:00 GMT</pubDate>
      <description><![CDATA[<p>正文 <b>加粗</b><img src="x.png"><script>bad()</script></p>]]></description>
    </item>
    <item>
      <title>第二篇</title>
      <link>/relative/2</link>
      <description>短正文</description>
    </item>
    <item>
      <title>第三篇</title>
      <description>无链接正文</description>
    </item>
  </channel>
</rss>
""".encode()


def test_parse_feed_fields_and_guid_fallbacks() -> None:
    fetched_at = 1_800_000_000
    result = parse_feed(RSS, fetched_at=fetched_at)
    assert result.title == "示例源"
    assert len(result.entries) == 3
    first, second, third = result.entries
    assert first.guid == "g-1"
    assert first.title == "第一篇 & 标题"
    assert first.link == "https://example.com/1"
    assert "正文" in first.body and "加粗" in first.body
    assert "bad()" not in first.body and "x.png" not in first.body
    assert len(first.summary) <= 201
    expected = calendar.timegm(
        datetime(2026, 11, 8, 10, 0, tzinfo=UTC).utctimetuple()
    )
    assert first.pubdate == expected
    assert second.guid == "https://example.com/relative/2"
    assert second.link == "https://example.com/relative/2"
    assert second.pubdate == fetched_at
    assert len(third.guid) == 64
    assert second.body == "短正文"
    assert second.summary == "短正文"


def test_duplicate_guids_are_dropped_within_one_feed() -> None:
    replacement = (
        "<item><title>重复</title><guid>g-1</guid></item></channel>".encode()
    )
    data = RSS.replace(b"</channel>", replacement)
    result = parse_feed(data, fetched_at=1_800_000_000)
    assert len(result.entries) == 3


def test_invalid_feed_raises() -> None:
    with pytest.raises(InvalidFeedError):
        parse_feed(b"not a feed at all", fetched_at=1_800_000_000)


async def test_fetcher_retries_retryable_error() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200, content=RSS)

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    fetcher = RssFetcher(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_delay=0.0,
        sleep=fake_sleep,
        clock=lambda: 1_800_000_000.0,
    )
    result = await fetcher.fetch("https://93.184.216.34/feed")
    assert len(result.entries) == 3
    assert len(calls) == 2
    assert sleeps == [0.0]


async def test_fetcher_does_not_retry_404() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    fetcher = RssFetcher(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_delay=0.0,
    )
    with pytest.raises(FetchError) as info:
        await fetcher.fetch("https://93.184.216.34/feed")
    assert info.value.retryable is False
    assert len(calls) == 1


async def test_fetcher_stops_after_three_retries() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    fetcher = RssFetcher(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_delay=0.0,
        sleep=lambda _delay: _no_sleep(),
    )
    with pytest.raises(FetchError):
        await fetcher.fetch("https://93.184.216.34/feed")
    assert len(calls) == 4


async def test_fetcher_rejects_private_url_before_request() -> None:
    called: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        called.append(1)
        return httpx.Response(200, content=RSS)

    fetcher = RssFetcher(
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_delay=0.0,
    )
    with pytest.raises(UnsafeURLError):
        await fetcher.fetch("http://127.0.0.1/feed")
    assert called == []


async def _no_sleep() -> None:
    return None
