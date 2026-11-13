"""聊天指令：私聊限制、源匹配、昵称、静音与按需拉取。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from NeoBot_RSS_News.commands import (
    GROUP_ONLY,
    MULTIPLE_SOURCES,
    MUTED,
    NOT_FOUND,
)
from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.models import FeedEntry, FeedResult
from NeoBot_RSS_News.storage import RssNewsStore


class FakeFetcher:
    def __init__(self, entries=()):
        self.entries = list(entries)
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FeedResult:
        self.calls.append(url)
        return FeedResult("源", tuple(self.entries))


def entry(guid: str, title: str, pubdate: int | None = None) -> FeedEntry:
    stamp = int(time.time()) if pubdate is None else pubdate
    return FeedEntry(guid, title, f"https://example.com/{guid}", f"{title}正文", f"{title}开头", stamp)


async def seed(plugin_env: Any, entries_by_weight: dict[int, list[FeedEntry]]) -> None:
    store = RssNewsStore(plugin_env.data_dir / "databases" / "rss_news.db")
    await store.open()
    try:
        subscriptions = await store.list_subscriptions()
        now = int(time.time())
        for subscription in subscriptions:
            entries = entries_by_weight.get(subscription.weight)
            if entries:
                await store.insert_articles(subscription.id, entries, now)
            await store.set_pull_at(subscription.id, now)
    finally:
        await store.close()


async def set_config(plugin_env: Any, **values: Any) -> None:
    await plugin_env.runtime.update_config(Config(**values))
    plugin_env.runtime.delivery._sleep = _no_sleep


async def _no_sleep(_delay: float) -> None:
    return None


async def settle() -> None:
    await asyncio.sleep(0.05)


def test_command_permissions(plugin_env: Any) -> None:
    commands = plugin_env.commands.items
    assert commands["RSS新闻"].permission == 0
    assert commands["所有RSS新闻"].permission == 0
    assert commands["RSS列表"].permission == 0
    assert commands["设置昵称"].permission == 1


async def test_group_news_requests_only_reply_private_only(plugin_env: Any) -> None:
    await set_config(plugin_env, content_format="title")
    for name in ("RSS新闻", "所有RSS新闻"):
        context = await plugin_env.run_command(
            name, "中新网", kind="group", conv_id="983752344"
        )
        assert context.replies == [GROUP_ONLY]
    assert plugin_env.adapter.messages == []


async def test_private_default_source_is_lowest_weight(plugin_env: Any) -> None:
    await seed(
        plugin_env,
        {
            1: [entry("one", "中新网文章")],
            2: [entry("two", "IT之家文章")],
        },
    )
    await set_config(plugin_env, content_format="title")
    context = await plugin_env.run_command("RSS新闻", user_id=123456)
    await settle()
    assert context.replies == []
    assert plugin_env.adapter.messages == [(123456, "中新网文章")]


async def test_private_source_by_nickname_or_full_name(plugin_env: Any) -> None:
    await seed(
        plugin_env,
        {
            1: [entry("one", "中新网文章")],
            2: [entry("two", "IT之家文章")],
        },
    )
    await set_config(plugin_env, content_format="title")
    await plugin_env.run_command("RSS新闻", "之家", user_id=123456)
    await settle()
    await plugin_env.run_command("RSS新闻", "IT之家", user_id=123456)
    await settle()
    assert plugin_env.adapter.messages == [
        (123456, "IT之家文章"),
        (123456, "IT之家文章"),
    ]


async def test_private_source_rejects_multiple_and_unknown(plugin_env: Any) -> None:
    await seed(plugin_env, {1: [entry("one", "中新网文章")]})
    await set_config(plugin_env, content_format="title")
    multiple = await plugin_env.run_command("RSS新闻", "中新网 之家", user_id=123456)
    assert multiple.replies == [MULTIPLE_SOURCES]
    missing = await plugin_env.run_command("RSS新闻", "不存在", user_id=123456)
    assert missing.replies == [NOT_FOUND.format("不存在")]
    await settle()
    assert plugin_env.adapter.messages == []


async def test_muted_does_not_pull_or_send(plugin_env: Any) -> None:
    await seed(plugin_env, {1: [entry("one", "中新网文章")]})
    await set_config(plugin_env, send_enabled=False, content_format="title")
    context = await plugin_env.run_command("RSS新闻", user_id=123456)
    await settle()
    assert context.replies == [MUTED]
    assert plugin_env.adapter.messages == []

    listed = await plugin_env.run_command("RSS列表")
    assert "中新网要闻导读（中新网）" in listed.replies[0]


async def test_all_news_order_limits_and_interval(plugin_env: Any) -> None:
    await seed(
        plugin_env,
        {
            1: [
                entry("a1", "A1", int(time.time()) - 1),
                entry("a2", "A2", int(time.time()) - 2),
                entry("a3", "A3", int(time.time()) - 3),
            ],
            2: [
                entry("b1", "B1", int(time.time()) - 1),
                entry("b2", "B2", int(time.time()) - 2),
            ],
        },
    )
    await set_config(
        plugin_env,
        content_format="title",
        per_source_limit=2,
        per_destination_limit=3,
        message_interval_seconds=2,
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    plugin_env.runtime.delivery._sleep = fake_sleep
    await plugin_env.run_command("所有RSS新闻", user_id=123456)
    await settle()
    assert plugin_env.adapter.messages == [
        (123456, "A1\n\nA2"),
        (123456, "B1"),
    ]
    assert sleeps == [2.0]


async def test_all_news_pulls_due_source(plugin_env: Any) -> None:
    store = RssNewsStore(plugin_env.data_dir / "databases" / "rss_news.db")
    await store.open()
    try:
        subscriptions = await store.list_subscriptions()
        await store.set_pull_at(subscriptions[0].id, int(time.time()) - 7200)
        await store.set_pull_at(subscriptions[1].id, int(time.time()))
    finally:
        await store.close()
    fetcher = FakeFetcher([entry("fresh", "拉取的新文章")])
    plugin_env.runtime.delivery.fetcher = fetcher
    await set_config(plugin_env, content_format="title")
    await plugin_env.run_command("RSS新闻", "中新网", user_id=123456)
    await settle()
    assert fetcher.calls == ["https://www.chinanews.com.cn/rss/importnews.xml"]
    assert plugin_env.adapter.messages == [(123456, "拉取的新文章")]


async def test_rss_list_private_and_group(plugin_env: Any) -> None:
    private = await plugin_env.run_command("RSS列表", user_id=123456)
    group = await plugin_env.run_command("RSS列表", kind="group", conv_id="983752344")
    for context in (private, group):
        assert "中新网要闻导读（中新网）" in context.replies[0]
        assert "IT之家（之家）" in context.replies[0]


async def test_set_nickname_set_duplicate_clear(plugin_env: Any) -> None:
    context = await plugin_env.run_command(
        "设置昵称", "中新网要闻导读 新简称", user_id=123456
    )
    assert "新简称" in context.replies[0]
    listed = await plugin_env.run_command("RSS列表")
    assert "中新网要闻导读（新简称）" in listed.replies[0]

    duplicate = await plugin_env.run_command(
        "设置昵称", "IT之家 新简称", user_id=123456
    )
    assert "已被订阅" in duplicate.replies[0]
    listed = await plugin_env.run_command("RSS列表")
    assert "IT之家（之家）" in listed.replies[0]

    cleared = await plugin_env.run_command(
        "设置昵称", "中新网要闻导读", user_id=123456
    )
    assert "清空" in cleared.replies[0]
    listed = await plugin_env.run_command("RSS列表")
    assert "中新网要闻导读\n" in listed.replies[0] + "\n"


async def test_set_nickname_in_group_and_not_found(plugin_env: Any) -> None:
    context = await plugin_env.run_command(
        "设置昵称", "IT之家 之家简称", kind="group", conv_id="983752344"
    )
    assert "之家简称" in context.replies[0]
    missing = await plugin_env.run_command("设置昵称", "不存在 昵称")
    assert "未找到订阅全称" in missing.replies[0]
