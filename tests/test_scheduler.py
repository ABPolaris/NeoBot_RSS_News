"""调度器：并发拉取、增量更新、发送窗口、限额、失败阈值与跳过。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.errors import DeliveryError
from NeoBot_RSS_News.models import FeedEntry, FeedResult
from NeoBot_RSS_News.scheduler import RssScheduler
from NeoBot_RSS_News.storage import RssNewsStore

NOW = 1_800_000_000


class FakeFetcher:
    def __init__(self, entries: Sequence[FeedEntry] = (), delay: float = 0.0) -> None:
        self.entries = list(entries)
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
            return FeedResult("源", tuple(self.entries))
        finally:
            self.active -= 1


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.attempts = 0
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, destination_id: int, text: str) -> None:
        self.attempts += 1
        if self.fail:
            raise DeliveryError("synthetic send failure")
        self.sent.append((destination_id, text))


def scheduler(
    store: RssNewsStore,
    config: Config,
    sender: FakeSender | None = None,
    fetcher: FakeFetcher | None = None,
    now: int = NOW,
) -> RssScheduler:
    async def no_sleep(_delay: float) -> None:
        return None

    return RssScheduler(
        store,
        fetcher or FakeFetcher(),
        sender or FakeSender(),
        config,
        clock=lambda: float(now),
        sleep=no_sleep,
    )


def entry(guid: str, title: str, pubdate: int) -> FeedEntry:
    return FeedEntry(
        guid=guid,
        title=title,
        link=f"https://example.com/{guid}",
        body=f"{title}正文",
        summary=f"{title}开头",
        pubdate=pubdate,
    )


async def add_source(
    store: RssNewsStore,
    name: str,
    url: str,
    weight: int,
    entries: Sequence[FeedEntry],
    created_at: int = NOW - 1000,
) -> int:
    subscription = await store.add_subscription(name, url, weight, created_at)
    if entries:
        await store.insert_articles(subscription.id, entries, NOW)
    return subscription.id


async def test_pull_concurrency_is_capped_at_four(store: RssNewsStore) -> None:
    for index in range(6):
        await store.add_subscription(
            f"S{index}", f"https://s{index}.example/rss", index + 1, NOW - 1000
        )
    fetcher = FakeFetcher([entry("g", "T", NOW)], delay=0.01)
    report = await scheduler(store, Config(), fetcher=fetcher).pull_round(NOW)
    assert report.due == 6
    assert report.succeeded == 6
    assert 2 <= fetcher.max_active <= 4
    assert len(fetcher.calls) == 6


async def test_pull_is_incremental_and_updates_pull_at(store: RssNewsStore) -> None:
    await store.add_subscription("A", "https://a.example/rss", 1, NOW - 1000)
    fetcher = FakeFetcher([entry("g1", "T1", NOW), entry("g2", "T2", NOW)])
    instance = scheduler(store, Config(), fetcher=fetcher)
    first = await instance.pull_round(NOW)
    assert first.inserted == 2
    assert await store.article_count() == 2
    second = await instance.pull_round(NOW + 1)
    assert second.due == 0
    assert second.inserted == 0
    await store.set_pull_at(1, NOW - 7200)
    third = await instance.pull_round(NOW)
    assert third.due == 1
    assert third.inserted == 0
    assert await store.article_count() == 2


async def test_send_per_article_and_no_duplicate(store: RssNewsStore) -> None:
    source = await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "A1", NOW - 1)]
    )
    assert source == 1
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender()
    instance = scheduler(store, Config(send_enabled=True, send_format="per_article"), sender)
    report = await instance.send_due_round(NOW)
    assert report.messages == 1
    assert report.sent == 1
    assert sender.sent[0][0] == 10001
    destination = await store.get_destination(10001)
    assert destination is not None
    assert destination.last_sent == NOW
    assert destination.next_send is not None and destination.next_send > NOW
    second = await instance.send_due_round(NOW + 1)
    assert second.destinations == 0
    assert sender.attempts == 1


async def test_send_ignores_articles_older_than_subscription(store: RssNewsStore) -> None:
    await add_source(
        store,
        "A",
        "https://a.example/rss",
        1,
        [entry("old", "旧文", NOW - 500)],
        created_at=NOW,
    )
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender()
    report = await scheduler(store, Config(send_enabled=True), sender).send_due_round(NOW)
    assert report.sent == 0
    assert sender.attempts == 0


async def test_limits_follow_weight_order(store: RssNewsStore) -> None:
    await add_source(
        store,
        "高权重",
        "https://a.example/rss",
        1,
        [
            entry("a3", "A3", NOW - 1),
            entry("a2", "A2", NOW - 2),
            entry("a1", "A1", NOW - 3),
        ],
    )
    await add_source(
        store,
        "低权重",
        "https://b.example/rss",
        2,
        [
            entry("b2", "B2", NOW - 1),
            entry("b1", "B1", NOW - 2),
            entry("b0", "B0", NOW - 3),
        ],
    )
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender()
    config = Config(
        send_enabled=True,
        send_format="per_article",
        content_format="title",
        per_source_limit=2,
        per_destination_limit=3,
    )
    report = await scheduler(store, config, sender).send_due_round(NOW)
    assert report.sent == 3
    titles = [text for _destination, text in sender.sent]
    assert titles == ["A3", "A2", "B2"]


async def test_five_failures_in_one_round_disable_destination(store: RssNewsStore) -> None:
    entries = [entry(f"g{index}", f"T{index}", NOW - index) for index in range(6)]
    await add_source(store, "A", "https://a.example/rss", 1, entries)
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender(fail=True)
    config = Config(send_enabled=True, send_format="per_article")
    report = await scheduler(store, config, sender).send_due_round(NOW)
    assert report.failed == 5
    assert report.disabled == (10001,)
    assert sender.attempts == 5
    destination = await store.get_destination(10001)
    assert destination is not None
    assert destination.enabled is False
    assert "5 篇" in (destination.reason or "")


async def test_three_all_failed_rounds_disable_destination(store: RssNewsStore) -> None:
    await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "T", NOW - 1)]
    )
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender(fail=True)
    instance = scheduler(store, Config(send_enabled=True, send_format="per_article"), sender)
    for _ in range(3):
        await store.set_next_send(10001, NOW - 5)
        await instance.send_due_round(NOW)
    destination = await store.get_destination(10001)
    assert destination is not None
    assert destination.enabled is False
    assert "连续 3 轮" in (destination.reason or "")
    assert destination.consecutive_failed_rounds == 3


async def test_failed_three_times_is_skipped(store: RssNewsStore) -> None:
    await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "T", NOW - 1)]
    )
    await store.add_destination(10001, NOW - 10, NOW)
    article = (await store.list_candidates(10001, 1, NOW - 1000, 10))[0]
    for index in range(3):
        await store.record_failure(10001, article, NOW + index)
    sender = FakeSender()
    report = await scheduler(store, Config(send_enabled=True), sender).send_due_round(NOW)
    assert report.sent == 0
    assert sender.attempts == 0
    assert await store.mark_skipped(10001) == 0


async def test_missed_window_fast_forwards_without_sending(store: RssNewsStore) -> None:
    await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "T", NOW - 1)]
    )
    await store.add_destination(10001, NOW - 301, NOW)
    sender = FakeSender()
    report = await scheduler(store, Config(send_enabled=True), sender).send_due_round(NOW)
    assert report.sent == 0
    assert sender.attempts == 0
    destination = await store.get_destination(10001)
    assert destination is not None
    assert destination.next_send is not None and destination.next_send > NOW


async def test_within_window_sends(store: RssNewsStore) -> None:
    await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "T", NOW - 1)]
    )
    await store.add_destination(10001, NOW - 299, NOW)
    sender = FakeSender()
    report = await scheduler(store, Config(send_enabled=True), sender).send_due_round(NOW)
    assert report.sent == 1


async def test_send_disabled_does_nothing(store: RssNewsStore) -> None:
    await add_source(
        store, "A", "https://a.example/rss", 1, [entry("g1", "T", NOW - 1)]
    )
    await store.add_destination(10001, NOW - 10, NOW)
    sender = FakeSender()
    report = await scheduler(store, Config(send_enabled=False), sender).send_due_round(NOW)
    assert report.destinations == 0
    assert sender.attempts == 0


async def test_config_change_recomputes_next_send(store: RssNewsStore) -> None:
    await store.add_destination(10001, NOW + 1000, NOW)
    instance = scheduler(store, Config())
    await instance.update_config(Config(send_time="09:30"))
    destination = await store.get_destination(10001)
    assert destination is not None
    assert destination.next_send is not None
    assert destination.next_send > NOW + 30 * 60


async def test_prepare_destinations_fast_forwards_missed(store: RssNewsStore) -> None:
    await store.add_destination(10001, None, NOW)
    await store.add_destination(10002, NOW - 301, NOW)
    instance = scheduler(store, Config())
    await instance.prepare_destinations(NOW)
    first = await store.get_destination(10001)
    second = await store.get_destination(10002)
    assert first is not None and first.next_send is not None
    assert second is not None and second.next_send is not None
    assert first.next_send > NOW
    assert second.next_send > NOW


async def test_message_spacing_uses_configured_interval() -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    instance = RssScheduler(
        None,  # type: ignore[arg-type]
        FakeFetcher(),
        FakeSender(),
        Config(message_interval_seconds=3),
        monotonic=lambda: 101.0,
        sleep=fake_sleep,
    )
    await instance._space_out()
    assert sleeps == []
    instance._last_send_monotonic = 100.0
    await instance._space_out()
    assert sleeps == [2.0]


async def test_send_round_skips_destination_read_when_not_due(
    store: RssNewsStore, monkeypatch
) -> None:
    await store.add_destination(10001, NOW + 3600, NOW)
    instance = scheduler(store, Config(send_enabled=True))

    async def unexpected_read(_destination_id: int):
        raise AssertionError("未到发送时间不应按 id 读取接收者")

    monkeypatch.setattr(store, "get_destination", unexpected_read)
    report = await instance.send_due_round(NOW)
    assert report.destinations == 0
    assert report.messages == 0
