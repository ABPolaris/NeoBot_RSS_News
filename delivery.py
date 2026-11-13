"""按用户请求拉取并发送新闻；不启动任何定时器。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .composer import MessageBundle, pack_messages
from .config import Config
from .models import Article, Subscription
from .sender import PrivateMessageSender
from .storage import RssNewsStore

PULL_CONCURRENCY = 4
NO_ARTICLES = "暂时没有可发送的文章。"
NO_SUBSCRIPTIONS = "当前没有 RSS 订阅。"
SOURCE_MISSING = "订阅不存在或已被删除。"


class FeedFetcher(Protocol):
    async def fetch(self, url: str) -> Any: ...


class NewsDelivery:
    """指令驱动的拉取与私聊发送。"""

    def __init__(
        self,
        store: RssNewsStore,
        fetcher: FeedFetcher,
        sender: PrivateMessageSender,
        config_provider: Callable[[], Config],
        *,
        logger: Any | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.sender = sender
        self._config = config_provider
        self.logger = logger
        self._clock = clock
        self._sleep = sleep
        self._pull_lock = asyncio.Lock()

    async def deliver_one(self, source_id: int, user_id: int) -> None:
        """拉取指定源（若到期），然后把最近文章作为一组消息发送。"""

        source = await self.store.get_subscription(source_id)
        if source is None:
            await self.sender.send_text(user_id, SOURCE_MISSING)
            return
        now = int(self._clock())
        await self._refresh([source_id], now)
        await self._cleanup(now)
        config = self._config()
        articles = await self.store.recent_articles(
            source_id, config.per_source_limit
        )
        if not articles:
            await self.sender.send_text(user_id, NO_ARTICLES)
            return
        await self._send_bundles(
            user_id, pack_messages(articles, config.content_format)
        )

    async def deliver_all(self, user_id: int) -> None:
        """拉取所有到期源，按权重顺序逐订阅发送。"""

        subscriptions = await self.store.list_subscriptions()
        if not subscriptions:
            await self.sender.send_text(user_id, NO_SUBSCRIPTIONS)
            return
        now = int(self._clock())
        await self._refresh(None, now)
        await self._cleanup(now)
        config = self._config()
        subscriptions = await self.store.list_subscriptions()
        groups: list[tuple[Subscription, list[Article]]] = []
        total = 0
        for subscription in subscriptions:
            remaining = config.per_destination_limit - total
            if remaining <= 0:
                break
            limit = min(config.per_source_limit, remaining)
            articles = await self.store.recent_articles(subscription.id, limit)
            if articles:
                groups.append((subscription, articles))
                total += len(articles)
        if not groups:
            await self.sender.send_text(user_id, NO_ARTICLES)
            return
        bundles: list[MessageBundle] = []
        for _subscription, articles in groups:
            bundles.extend(pack_messages(articles, config.content_format))
        await self._send_bundles(user_id, bundles)

    async def _send_bundles(self, user_id: int, bundles: list[MessageBundle]) -> None:
        config = self._config()
        for index, bundle in enumerate(bundles):
            if index > 0:
                await self._sleep(float(config.message_interval_seconds))
            try:
                await self.sender.send_text(user_id, bundle.text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 适配器可抛任意异常
                self._log(
                    "warning",
                    f"向 {user_id} 发送新闻失败：{type(exc).__name__}",
                )
                break

    async def _cleanup(self, now: int) -> None:
        try:
            await self.store.cleanup_articles(now, self._config().cache_days)
        except Exception as exc:  # noqa: BLE001 - 清理失败不阻断本次发送
            self._log("warning", f"清理过期文章失败：{type(exc).__name__}")

    async def _refresh(self, source_ids: list[int] | None, now: int) -> None:
        """在锁内检查每个源的最小拉取间隔，只拉取到期源。"""

        async with self._pull_lock:
            if source_ids is None:
                sources = await self.store.list_subscriptions()
            else:
                sources = []
                for source_id in source_ids:
                    source = await self.store.get_subscription(source_id)
                    if source is not None:
                        sources.append(source)
            interval = self._config().pull_interval_seconds
            due = [
                source
                for source in sources
                if source.pull_at is None or now - source.pull_at >= interval
            ]
            if not due:
                return
            semaphore = asyncio.Semaphore(PULL_CONCURRENCY)

            async def fetch_one(source: Subscription) -> bool:
                async with semaphore:
                    try:
                        result = await self.fetcher.fetch(source.url)
                        await self.store.insert_articles(
                            source.id, result.entries, int(self._clock())
                        )
                        return True
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - 单个源失败隔离
                        self._log(
                            "warning",
                            f"拉取订阅「{source.name}」失败：{type(exc).__name__}",
                        )
                        return False
                    finally:
                        try:
                            await self.store.set_pull_at(
                                source.id, int(self._clock())
                            )
                        except Exception:  # noqa: BLE001, S110
                            pass

            tasks = [asyncio.create_task(fetch_one(source)) for source in due]
            budget = max(5.0, interval * 0.75 - (self._clock() - now))
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=budget
                )
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                results = await asyncio.gather(*tasks, return_exceptions=True)
            succeeded = sum(1 for item in results if item is True)
            self._log(
                "info",
                f"按请求拉取完成：{succeeded}/{len(due)} 个源成功",
            )

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message[:500])
        except Exception:  # noqa: BLE001, S110 - 日志失败不能影响业务
            pass


__all__ = [
    "NO_ARTICLES",
    "NO_SUBSCRIPTIONS",
    "PULL_CONCURRENCY",
    "SOURCE_MISSING",
    "NewsDelivery",
]
