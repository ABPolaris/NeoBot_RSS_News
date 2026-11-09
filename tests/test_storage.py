"""SQLite 层：预设、上限、级联删除、增量缓存与 60 天清理。"""

from __future__ import annotations

import pytest
from NeoBot_RSS_News.errors import DuplicateValueError, SubscriptionLimitError
from NeoBot_RSS_News.models import Article, FeedEntry
from NeoBot_RSS_News.storage import (
    DELETED_RETENTION_SECONDS,
    MAX_SUBSCRIPTIONS,
    RssNewsStore,
    available_weights,
)

PRESETS = (("A", "https://a.example/rss", 1), ("B", "https://b.example/rss", 2))


async def add_entry(
    store: RssNewsStore,
    source_id: int,
    guid: str,
    pubdate: int,
    *,
    title: str = "标题",
    body: str = "正文",
    summary: str = "开头",
) -> None:
    await store.insert_articles(
        source_id,
        [FeedEntry(guid, title, "https://example.com/a", body, summary, pubdate)],
        pubdate,
    )


async def test_presets_only_initialized_once(store: RssNewsStore) -> None:
    now = 1_800_000_000
    assert await store.init_presets(PRESETS, now) is True
    assert [item.weight for item in await store.list_subscriptions()] == [1, 2]
    for item in await store.list_subscriptions():
        assert await store.delete_subscription(item.id)
    assert await store.list_subscriptions() == []
    assert await store.init_presets(PRESETS, now) is False
    assert await store.list_subscriptions() == []


async def test_subscription_limit_and_auto_weight(store: RssNewsStore) -> None:
    now = 1_800_000_000
    await store.init_presets(PRESETS, now)
    for index in range(MAX_SUBSCRIPTIONS - 2):
        item = await store.add_subscription(
            f"C{index}", f"https://c{index}.example/rss", None, now
        )
        assert item.weight == 3 + index
    with pytest.raises(SubscriptionLimitError):
        await store.add_subscription("overflow", "https://overflow.example/rss", None, now)


async def test_duplicate_url_and_weight(store: RssNewsStore) -> None:
    now = 1_800_000_000
    await store.init_presets(PRESETS, now)
    with pytest.raises(DuplicateValueError):
        await store.add_subscription("dup", "https://a.example/rss", None, now)
    with pytest.raises(DuplicateValueError):
        await store.add_subscription("weight", "https://new.example/rss", 1, now)
    item = await store.add_subscription("five", "https://new.example/rss", 5, now)
    assert item.weight == 5
    auto = await store.add_subscription("auto", "https://auto.example/rss", None, now)
    assert auto.weight == 3
    assert available_weights(await store.list_subscriptions()) == [4, 6, 7, 8, 9, 10]


async def test_update_url_clears_articles_and_sent_records(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "g1", now + 10)
    destination = await store.add_destination(10001, now, now)
    article = (await store.list_candidates(10001, subscription.id, now, 10))[0]
    await store.record_sent(10001, article, now)
    assert destination.destination_id == 10001

    renamed, url_changed = await store.update_subscription(
        subscription.id,
        name="A2",
        url=None,
        weight=None,
        now=now + 100,
    )
    assert url_changed is False
    assert renamed.name == "A2"
    assert await store.article_count() == 1
    assert await store.count_sent() == 1

    moved, url_changed = await store.update_subscription(
        subscription.id,
        name=None,
        url="https://new.example/rss",
        weight=None,
        now=now + 200,
    )
    assert url_changed is True
    assert moved.url == "https://new.example/rss"
    assert moved.created_at == now + 200
    assert moved.pull_at is None
    assert await store.article_count() == 0
    assert await store.count_sent() == 0


async def test_delete_subscription_cascades(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "g1", now + 10)
    await store.add_destination(10001, now, now)
    article = (await store.list_candidates(10001, subscription.id, now, 10))[0]
    await store.record_sent(10001, article, now)
    assert await store.delete_subscription(subscription.id)
    assert await store.article_count() == 0
    assert await store.count_sent() == 0
    assert not await store.delete_subscription(subscription.id)


async def test_delete_destination_cascades(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "g1", now + 10)
    await store.add_destination(10001, now, now)
    article = (await store.list_candidates(10001, subscription.id, now, 10))[0]
    await store.record_sent(10001, article, now)
    assert await store.delete_destination(10001)
    assert await store.count_sent() == 0
    assert await store.list_destinations() == []


async def test_cleanup_marks_then_purges_after_sixty_days(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(
        store,
        subscription.id,
        "old",
        now - 11 * 86400,
        title="旧标题",
        body="旧正文",
        summary="旧开头",
    )
    await store.add_destination(10001, now, now)
    article = (
        await store.list_candidates(10001, subscription.id, now - 30 * 86400, 10)
    )[0]
    await store.record_sent(10001, article, now)

    marked, purged = await store.cleanup_articles(now, 10)
    assert (marked, purged) == (1, 0)
    assert await store.article_count() == 0
    assert await store.count_sent() == 1
    assert await store.list_recent_articles() == []

    await store.cleanup_articles(now + DELETED_RETENTION_SECONDS - 1, 10)
    assert await store.count_sent() == 1

    marked, purged = await store.cleanup_articles(
        now + DELETED_RETENTION_SECONDS + 1, 10
    )
    assert (marked, purged) == (0, 1)
    assert await store.count_sent() == 0


async def test_candidates_respect_pubdate_and_failure_counts(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "too-old", now - 1)
    await add_entry(store, subscription.id, "ok", now + 1)
    await add_entry(store, subscription.id, "failed-twice", now + 2)
    await add_entry(store, subscription.id, "failed-thrice", now + 3)
    await add_entry(store, subscription.id, "success", now + 4)
    await store.add_destination(10001, now, now)

    articles = await store.list_candidates(10001, subscription.id, now, 10)
    by_guid = {item.guid: item for item in articles}
    assert "too-old" not in by_guid
    assert set(by_guid) == {"ok", "failed-twice", "failed-thrice", "success"}

    for _ in range(2):
        await store.record_failure(10001, by_guid["failed-twice"], now)
    for _ in range(3):
        await store.record_failure(10001, by_guid["failed-thrice"], now)
    await store.record_sent(10001, by_guid["success"], now)

    remaining = {
        item.guid for item in await store.list_candidates(10001, subscription.id, now, 10)
    }
    assert remaining == {"ok", "failed-twice"}
    assert await store.mark_skipped(10001) == 1
    assert await store.mark_skipped(10001) == 0


async def test_due_subscriptions_by_pull_at(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    assert [item.id for item in await store.list_due_subscriptions(now, 100)] == [
        subscription.id
    ]
    await store.set_pull_at(subscription.id, now)
    assert await store.list_due_subscriptions(now + 99, 100) == []
    assert len(await store.list_due_subscriptions(now + 100, 100)) == 1


async def test_destination_duplicate_and_reason_clearing(store: RssNewsStore) -> None:
    now = 1_800_000_000
    created = await store.add_destination(10001, now + 100, now)
    assert created.enabled is True
    with pytest.raises(DuplicateValueError):
        await store.add_destination(10001, now + 100, now)

    await store.disable_destination(10001, "连续失败")
    paused = await store.get_destination(10001)
    assert paused is not None and paused.enabled is False
    assert paused.reason == "连续失败"

    resumed = await store.set_destination_enabled(10001, True, None)
    await store.set_failed_rounds(10001, 0)
    assert resumed.enabled is True
    assert resumed.reason is None
    assert (await store.get_destination(10001)).consecutive_failed_rounds == 0


async def test_sent_records_survive_repeated_failures(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "g1", now + 1)
    await store.add_destination(10001, now, now)
    article = (
        await store.list_candidates(10001, subscription.id, now, 10)
    )[0]
    for index in range(4):
        count = await store.record_failure(10001, article, now + index)
        assert count == index + 1
    await store.record_sent(10001, article, now + 10)
    remaining = await store.list_candidates(10001, subscription.id, now, 10)
    assert remaining == []
    assert isinstance(article, Article)


async def test_random_article_reads_only_active_cache(store: RssNewsStore) -> None:
    now = 1_800_000_000
    assert await store.random_article() is None
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, now
    )
    await add_entry(store, subscription.id, "g1", now)
    await add_entry(store, subscription.id, "g2", now + 1)

    picks: set[str] = set()
    for _ in range(20):
        picked = await store.random_article()
        assert picked is not None
        article, source_name = picked
        assert source_name == "A"
        picks.add(article.guid)
    assert picks <= {"g1", "g2"}
    assert await store.article_count() == 2

    await store.cleanup_articles(now + 8 * 86400, 7)
    assert await store.random_article() is None
