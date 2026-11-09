"""运行时容器：配置消费者、热重载与关闭顺序。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.models import FeedEntry, FeedResult
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


class FakeFetcher:
    async def fetch(self, url: str) -> FeedResult:
        return FeedResult(
            "源",
            (FeedEntry("g1", "标题", "https://example.com/1", "正文", "开头", 1_800_000_000),),
        )


async def test_config_consumer_registers_and_applies(tmp_path) -> None:
    control = FakeControl()
    context = SimpleNamespace(
        data_dir=tmp_path,
        config={},
        logger=None,
        plugin_control=control,
        plugin_name="NeoBot_RSS_News",
    )
    runtime = RssNewsRuntime.from_context(context)
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

        destination = await runtime.store.add_destination(10001, 1_800_000_000, 1_800_000_000)
        assert destination.destination_id == 10001
        await consumer.apply_config(
            {"send_time": "09:30", "send_enabled": True, "send_format": "merged"}
        )
        assert runtime.config.send_time == "09:30"
        assert runtime.config.send_enabled is True
        assert runtime.scheduler is not None
        assert runtime.scheduler.config.send_time == "09:30"
        refreshed = await runtime.store.get_destination(10001)
        assert refreshed is not None
        assert refreshed.next_send is not None
    finally:
        await runtime.stop()
        await runtime.stop()
    assert control.unregistered == ["NeoBot_RSS_News"]
    assert runtime.store is None


async def test_pull_now_runs_in_background(tmp_path) -> None:
    context = SimpleNamespace(
        data_dir=tmp_path,
        config={},
        logger=None,
        plugin_control=None,
        plugin_name="NeoBot_RSS_News",
    )
    runtime = RssNewsRuntime.from_context(context)
    await runtime.load()
    try:
        await runtime.store.add_subscription("A", "https://a.example/rss", None, 1_700_000_000)
        assert runtime.scheduler is not None
        runtime.scheduler.fetcher = FakeFetcher()
        started = await runtime.pull_now()
        assert started is True
        await asyncio.sleep(0.05)
        assert await runtime.store.article_count() == 3
    finally:
        await runtime.stop()


async def test_update_config_rolls_back_on_failure(tmp_path) -> None:
    context = SimpleNamespace(
        data_dir=tmp_path,
        config={},
        logger=None,
        plugin_control=None,
        plugin_name="NeoBot_RSS_News",
    )
    runtime = RssNewsRuntime.from_context(context)
    await runtime.load()
    try:
        old = runtime.config
        broken = Config(send_time="08:00")
        assert runtime.scheduler is not None

        async def boom(_config: Config) -> None:
            raise RuntimeError("synthetic")

        original = runtime.scheduler.update_config
        runtime.scheduler.update_config = boom  # type: ignore[method-assign]
        try:
            try:
                await runtime.update_config(broken)
            except RuntimeError:
                pass
            else:  # pragma: no cover - 必须抛出
                raise AssertionError("update_config 应向上抛出")
            assert runtime.config is old
        finally:
            runtime.scheduler.update_config = original  # type: ignore[method-assign]
    finally:
        await runtime.stop()
