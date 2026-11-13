"""指令驱动的拉取与发送。"""

from __future__ import annotations

import asyncio

from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.delivery import (
    NO_ARTICLES,
    NO_SUBSCRIPTIONS,
    SOURCE_MISSING,
    NewsDelivery,
)
from NeoBot_RSS_News.models import FeedEntry, FeedResult
from NeoBot_RSS_News.storage import RssNewsStore

NOW = 1_800_000_000


class FakeFetcher:
    def __init__(self, entries=(), *, fail: bool = False, delay: float = 0.0):
        self.entries = list(entries)
        self.fail = fail
        self.delay = delay
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    async def fetch(self, url: str) -> FeedResult:
        self.calls.append(url)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("synthetic fetch failure")
            return FeedResult("源", tuple(self.entries))
        finally:
            self.active -= 1


class FakeSender:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_text(self, user_id: int, text: str) -> None:
        self.messages.append((int(user_id), text))


def entry(guid: str, title: str, pubdate: int = NOW) -> FeedEntry:
    return FeedEntry(
        guid=guid,
        title=title,
        link=f"https://example.com/{guid}",
        body=f"{title}正文",
        summary=f"{title}开头",
        pubdate=pubdate,
    )


def delivery(
    store: RssNewsStore,
    config: Config,
    *,
    fetcher: FakeFetcher | None = None,
    sender: FakeSender | None = None,
    sleeps: list[float] | None = None,
) -> tuple[NewsDelivery, FakeFetcher, FakeSender]:
    actual_fetcher = fetcher or FakeFetcher()
    actual_sender = sender or FakeSender()
    sleep_log = sleeps if sleeps is not None else []

    async def fake_sleep(delay: float) -> None:
        sleep_log.append(delay)

    return (
        NewsDelivery(
            store,
            actual_fetcher,
            actual_sender,  # type: ignore[arg-type]
            lambda: config,
            clock=lambda: float(NOW),
            sleep=fake_sleep,
        ),
        actual_fetcher,
        actual_sender,
    )


async def seed(
    store: RssNewsStore,
    name: str,
    url: str,
    weight: int,
    entries: list[FeedEntry],
    *,
    pull_at: int | None,
    nickname: str = "",
) -> int:
    subscription = await store.add_subscription(
        name, url, nickname, weight, NOW - 1000
    )
    if entries:
        await store.insert_articles(subscription.id, entries, NOW)
    if pull_at is not None:
        await store.set_pull_at(subscription.id, pull_at)
    return subscription.id


async def test_deliver_one_reads_cache_without_pull(store: RssNewsStore) -> None:
    source_id = await seed(
        store,
        "A",
        "https://a.example/rss",
        1,
        [entry("g1", "第一篇")],
        pull_at=NOW,
    )
    instance, fetcher, sender = delivery(
        store, Config(content_format="title")
    )
    await instance.deliver_one(source_id, 10001)
    assert fetcher.calls == []
    assert sender.messages == [(10001, "第一篇")]


async def test_deliver_one_pulls_when_due(store: RssNewsStore) -> None:
    source_id = await seed(
        store, "A", "https://a.example/rss", 1, [], pull_at=None
    )
    fetcher = FakeFetcher([entry("g1", "拉取文章")])
    instance, _, sender = delivery(
        store, Config(content_format="title"), fetcher=fetcher
    )
    await instance.deliver_one(source_id, 10001)
    assert fetcher.calls == ["https://a.example/rss"]
    assert sender.messages == [(10001, "拉取文章")]
    subscription = await store.get_subscription(source_id)
    assert subscription is not None and subscription.pull_at == NOW


async def test_deliver_one_pull_failure_still_sends_cache(store: RssNewsStore) -> None:
    source_id = await seed(
        store,
        "A",
        "https://a.example/rss",
        1,
        [entry("g1", "缓存文章")],
        pull_at=NOW - 7200,
    )
    fetcher = FakeFetcher(fail=True)
    instance, _, sender = delivery(
        store, Config(content_format="title"), fetcher=fetcher
    )
    await instance.deliver_one(source_id, 10001)
    assert fetcher.calls == ["https://a.example/rss"]
    assert sender.messages == [(10001, "缓存文章")]


async def test_deliver_one_without_articles_or_source(store: RssNewsStore) -> None:
    source_id = await seed(
        store, "A", "https://a.example/rss", 1, [], pull_at=NOW
    )
    instance, _, sender = delivery(store, Config())
    await instance.deliver_one(source_id, 10001)
    assert sender.messages == [(10001, NO_ARTICLES)]

    await instance.deliver_one(9999, 10002)
    assert sender.messages[-1] == (10002, SOURCE_MISSING)


async def test_deliver_all_order_and_limits(store: RssNewsStore) -> None:
    await seed(
        store,
        "A",
        "https://a.example/rss",
        1,
        [entry("a2", "A2", NOW), entry("a1", "A1", NOW - 1), entry("a0", "A0", NOW - 2)],
        pull_at=NOW,
    )
    await seed(
        store,
        "B",
        "https://b.example/rss",
        2,
        [entry("b2", "B2", NOW), entry("b1", "B1", NOW - 1)],
        pull_at=NOW,
    )
    sleeps: list[float] = []
    instance, _, sender = delivery(
        store,
        Config(
            content_format="title",
            per_source_limit=2,
            per_destination_limit=3,
            message_interval_seconds=2,
        ),
        sleeps=sleeps,
    )
    await instance.deliver_all(10001)
    assert sender.messages == [
        (10001, "A2\n\nA1"),
        (10001, "B2"),
    ]
    assert sleeps == [2.0]


async def test_deliver_all_no_subscriptions(store: RssNewsStore) -> None:
    instance, _, sender = delivery(store, Config())
    await instance.deliver_all(10001)
    assert sender.messages == [(10001, NO_SUBSCRIPTIONS)]


async def test_deliver_all_pulls_only_due_sources(store: RssNewsStore) -> None:
    await seed(
        store,
        "最近",
        "https://recent.example/rss",
        1,
        [entry("r1", "最近缓存")],
        pull_at=NOW,
    )
    await seed(
        store,
        "到期",
        "https://due.example/rss",
        2,
        [],
        pull_at=NOW - 7200,
    )
    fetcher = FakeFetcher([entry("d1", "到期文章")])
    instance, _, sender = delivery(
        store, Config(content_format="title"), fetcher=fetcher
    )
    await instance.deliver_all(10001)
    assert fetcher.calls == ["https://due.example/rss"]
    assert sender.messages == [
        (10001, "最近缓存"),
        (10001, "到期文章"),
    ]


async def test_deliver_all_cleanup_removes_expired(store: RssNewsStore) -> None:
    await seed(
        store,
        "A",
        "https://a.example/rss",
        1,
        [
            entry("old", "旧文章", NOW - 11 * 86400),
            entry("fresh", "新文章", NOW - 1),
        ],
        pull_at=NOW,
    )
    instance, _, sender = delivery(store, Config(content_format="title"))
    await instance.deliver_all(10001)
    assert sender.messages == [(10001, "新文章")]
    assert await store.article_count() == 1


async def test_pull_concurrency_is_capped(store: RssNewsStore) -> None:
    for index in range(6):
        await seed(
            store,
            f"S{index}",
            f"https://s{index}.example/rss",
            index + 1,
            [],
            pull_at=None,
        )
    fetcher = FakeFetcher([entry("g", "T")], delay=0.01)
    instance, _, _ = delivery(store, Config(), fetcher=fetcher)
    await instance.deliver_all(10001)
    assert 2 <= fetcher.max_active <= 4
