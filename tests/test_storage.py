"""SQLite 层：预设昵称、上限、级联删除、增量缓存与立即清理。"""

from __future__ import annotations

import pytest
from NeoBot_RSS_News.errors import DuplicateValueError, SubscriptionLimitError
from NeoBot_RSS_News.models import FeedEntry
from NeoBot_RSS_News.storage import (
    MAX_SUBSCRIPTIONS,
    RssNewsStore,
    available_weights,
)

PRESETS = (
    ("A", "https://a.example/rss", 1, "甲"),
    ("B", "https://b.example/rss", 2, "乙"),
)


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
    subscriptions = await store.list_subscriptions()
    assert [(item.name, item.nickname) for item in subscriptions] == [
        ("A", "甲"),
        ("B", "乙"),
    ]
    for item in subscriptions:
        assert await store.delete_subscription(item.id)
    assert await store.init_presets(PRESETS, now) is False
    assert await store.list_subscriptions() == []


async def test_subscription_limit_and_auto_weight(store: RssNewsStore) -> None:
    now = 1_800_000_000
    await store.init_presets(PRESETS, now)
    for index in range(MAX_SUBSCRIPTIONS - 2):
        item = await store.add_subscription(
            f"C{index}", f"https://c{index}.example/rss", None, None, now
        )
        assert item.weight == 3 + index
        assert item.nickname == ""
    with pytest.raises(SubscriptionLimitError):
        await store.add_subscription(
            "overflow", "https://overflow.example/rss", None, None, now
        )


async def test_duplicate_url_and_nickname(store: RssNewsStore) -> None:
    now = 1_800_000_000
    await store.init_presets(PRESETS, now)
    with pytest.raises(DuplicateValueError):
        await store.add_subscription("dup", "https://a.example/rss", None, None, now)
    with pytest.raises(DuplicateValueError):
        await store.add_subscription("nick", "https://new.example/rss", "甲", None, now)
    item = await store.add_subscription(
        "five", "https://new.example/rss", "新昵称", 5, now
    )
    assert item.weight == 5
    auto = await store.add_subscription(
        "auto", "https://auto.example/rss", "", None, now
    )
    assert auto.weight == 3
    assert auto.nickname == ""
    assert available_weights(await store.list_subscriptions()) == [4, 6, 7, 8, 9, 10]


async def test_set_and_clear_nickname(store: RssNewsStore) -> None:
    now = 1_800_000_000
    await store.init_presets(PRESETS, now)
    subscriptions = await store.list_subscriptions()
    first, second = subscriptions
    updated = await store.set_nickname(first.id, " 新甲 ")
    assert updated.nickname == "新甲"
    with pytest.raises(DuplicateValueError):
        await store.set_nickname(second.id, "新甲")
    cleared = await store.set_nickname(second.id, "  ")
    assert cleared.nickname == ""
    # 空昵称可以重复：再清空第一个也不应报错。
    assert (await store.set_nickname(first.id, None)).nickname == ""


async def test_update_url_clears_articles_but_name_nickname_weight_keep_them(
    store: RssNewsStore,
) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", "甲", None, now
    )
    await add_entry(store, subscription.id, "g1", now + 10)
    renamed, url_changed = await store.update_subscription(
        subscription.id,
        name="A2",
        url=None,
        nickname="新甲",
        nickname_changed=True,
        weight=None,
        now=now + 100,
    )
    assert url_changed is False
    assert renamed.name == "A2"
    assert renamed.nickname == "新甲"
    assert await store.article_count() == 1

    moved, url_changed = await store.update_subscription(
        subscription.id,
        name=None,
        url="https://new.example/rss",
        nickname=None,
        nickname_changed=False,
        weight=None,
        now=now + 200,
    )
    assert url_changed is True
    assert moved.url == "https://new.example/rss"
    assert moved.created_at == now + 200
    assert moved.pull_at is None
    assert await store.article_count() == 0


async def test_delete_subscription_cascades(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, None, now
    )
    await add_entry(store, subscription.id, "g1", now + 10)
    assert await store.delete_subscription(subscription.id)
    assert await store.article_count() == 0
    assert not await store.delete_subscription(subscription.id)


async def test_cleanup_deletes_expired_articles_immediately(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, None, now
    )
    await add_entry(store, subscription.id, "old", now - 11 * 86400)
    await add_entry(store, subscription.id, "fresh", now - 1)
    assert await store.cleanup_articles(now, 10) == 1
    assert await store.article_count() == 1
    assert [item.guid for item in await store.recent_articles(subscription.id, 10)] == [
        "fresh"
    ]


async def test_recent_and_random_articles(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, None, now
    )
    await add_entry(store, subscription.id, "g1", now - 1, title="第一篇")
    await add_entry(store, subscription.id, "g2", now, title="第二篇")
    recent = await store.recent_articles(subscription.id, 10)
    assert [item.guid for item in recent] == ["g2", "g1"]
    assert await store.recent_articles(subscription.id, 1) == [recent[0]]

    picks = set()
    for _ in range(20):
        picked = await store.random_article()
        assert picked is not None
        article, source_name = picked
        assert source_name == "A"
        picks.add(article.guid)
    assert picks <= {"g1", "g2"}


async def test_pull_at_and_latest_pull(store: RssNewsStore) -> None:
    now = 1_800_000_000
    subscription = await store.add_subscription(
        "A", "https://a.example/rss", None, None, now
    )
    assert await store.latest_pull_at() is None
    await store.set_pull_at(subscription.id, now)
    assert await store.latest_pull_at() == now
    refreshed = await store.get_subscription(subscription.id)
    assert refreshed is not None and refreshed.pull_at == now
