"""运行时容器：配置消费者、按需投递对象与关闭顺序。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.models import FeedEntry
from NeoBot_RSS_News.runtime import CONFIG_PATHS, RssNewsRuntime


class FakeControl:
    def __init__(self) -> None:
        self.consumers: dict[str, Any] = {}
        self.unregistered: list[str] = []

    def register_config_consumer(self, name: str, consumer: Any) -> None:
        self.consumers[name] = consumer

    def unregister_config_consumer(self, name: str) -> None:
        self.unregistered.append(name)
        self.consumers.pop(name, None)


def context(tmp_path: Any, control: Any | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        data_dir=tmp_path,
        config={},
        logger=None,
        plugin_control=control,
        plugin_name="NeoBot_RSS_News",
    )


async def test_config_consumer_registers_and_applies(tmp_path) -> None:
    control = FakeControl()
    runtime = RssNewsRuntime.from_context(context(tmp_path, control))
    await runtime.load()
    try:
        assert "NeoBot_RSS_News" in control.consumers
        consumer = control.consumers["NeoBot_RSS_News"]
        assert consumer.config_paths == ("plugin.NeoBot_RSS_News",)
        policies = consumer.hot_reload_policies
        assert {rule.path for rule in policies} == {
            f"plugin.NeoBot_RSS_News.{field}" for field in CONFIG_PATHS
        }
        assert all(rule.hot_reload for rule in policies)

        await consumer.apply_config(
            {"send_enabled": False, "content_format": "title_body"}
        )
        assert runtime.config.send_enabled is False
        assert runtime.config.content_format == "title_body"
        assert runtime.delivery is not None
    finally:
        await runtime.stop()
        await runtime.stop()
    assert control.unregistered == ["NeoBot_RSS_News"]
    assert runtime.store is None
    assert runtime.delivery is None


async def test_update_config_cleans_expired_articles(tmp_path) -> None:
    runtime = RssNewsRuntime.from_context(context(tmp_path))
    await runtime.load()
    try:
        assert runtime.store is not None
        subscriptions = await runtime.store.list_subscriptions()
        now = int(time.time())
        await runtime.store.insert_articles(
            subscriptions[0].id,
            [
                FeedEntry("old", "旧", "", "", "", now - 20 * 86400),
                FeedEntry("fresh", "新", "", "", "", now - 1),
            ],
            now,
        )
        assert await runtime.store.article_count() == 2
        await runtime.update_config(Config(cache_days=7))
        assert await runtime.store.article_count() == 1
        assert runtime.config.cache_days == 7
    finally:
        await runtime.stop()


async def test_load_creates_presets_with_nicknames(tmp_path) -> None:
    runtime = RssNewsRuntime.from_context(context(tmp_path))
    await runtime.load()
    try:
        assert runtime.store is not None
        subscriptions = await runtime.store.list_subscriptions()
        assert [(item.name, item.nickname) for item in subscriptions] == [
            ("中新网要闻导读", "中新网"),
            ("IT之家", "之家"),
        ]
        assert runtime.fetcher is not None
        assert runtime.sender is not None
        assert runtime.delivery is not None
    finally:
        await runtime.stop()
