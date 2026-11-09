"""插件运行时容器：配置、数据库、抓取器、调度器与配置热重载。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config, coerce_config
from .fetch import RssFetcher
from .presets import PRESETS
from .scheduler import RssScheduler
from .sender import PrivateMessageSender
from .storage import RssNewsStore

DATABASE_NAME = "rss_news.db"
CONFIG_PATHS = (
    "send_enabled",
    "send_every_days",
    "send_time",
    "timezone",
    "send_format",
    "content_format",
    "per_source_limit",
    "per_destination_limit",
    "pull_interval_minutes",
    "cache_days",
    "message_interval_seconds",
)


@dataclass(frozen=True, slots=True)
class PluginHotReloadRule:
    """与宿主 HotReloadRule 同形的轻量声明，避免依赖 app 内部模块。"""

    path: str
    hot_reload: bool
    reason: str = ""


class RssNewsConfigConsumer:
    """配置保存后在原插件实例上生效，不必重载整个插件。"""

    def __init__(self, runtime: RssNewsRuntime, plugin_name: str) -> None:
        self._runtime = runtime
        self.name = plugin_name
        self.config_paths = (f"plugin.{plugin_name}",)

    @property
    def hot_reload_policies(self) -> tuple[PluginHotReloadRule, ...]:
        prefix = f"plugin.{self.name}"
        return tuple(
            PluginHotReloadRule(
                f"{prefix}.{field}",
                True,
                "保存后由调度器读取新配置，下一轮即生效",
            )
            for field in CONFIG_PATHS
        )

    async def apply_config(self, config: Any) -> None:
        await self._runtime.update_config(coerce_config(config))


class RssNewsRuntime:
    def __init__(
        self,
        config: Config,
        data_dir: Path,
        *,
        ctx: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self.config = config
        self.data_dir = Path(data_dir)
        self.ctx = ctx
        self.logger = logger
        self.store: RssNewsStore | None = None
        self.fetcher: RssFetcher | None = None
        self.sender: PrivateMessageSender | None = None
        self.scheduler: RssScheduler | None = None
        self._consumer: RssNewsConfigConsumer | None = None
        self._consumer_control: Any | None = None

    @classmethod
    def from_context(cls, ctx: Any) -> RssNewsRuntime:
        runtime = cls(
            coerce_config(getattr(ctx, "config", None)),
            Path(getattr(ctx, "data_dir", Path.cwd())),
            ctx=ctx,
            logger=getattr(ctx, "logger", None),
        )
        return runtime

    async def load(self) -> None:
        store = RssNewsStore(self.data_dir / "databases" / DATABASE_NAME)
        await store.open()
        try:
            await store.init_presets(PRESETS, int(time.time()))
            fetcher = RssFetcher()
            sender = PrivateMessageSender(self.ctx)
            scheduler = RssScheduler(
                store,
                fetcher,
                sender,
                self.config,
                logger=self.logger,
            )
        except BaseException:
            await store.close()
            raise
        self.store = store
        self.fetcher = fetcher
        self.sender = sender
        self.scheduler = scheduler
        self.register_config_consumer()

    def start(self) -> None:
        if self.scheduler is None:
            return
        create_task = getattr(self.ctx, "create_task", None)
        try:
            self.scheduler.start(create_task=create_task)
        except Exception as exc:
            self.log_warning(f"启动调度任务失败：{type(exc).__name__}")
            raise

    async def update_config(self, config: Config) -> None:
        old = self.config
        scheduler = self.scheduler
        if scheduler is None:
            self.config = config
            return
        try:
            await scheduler.update_config(config)
        except Exception:
            scheduler.config = old
            raise
        self.config = config

    async def stop(self) -> None:
        self.unregister_config_consumer()
        scheduler, self.scheduler = self.scheduler, None
        if scheduler is not None:
            await scheduler.stop()
        store, self.store = self.store, None
        if store is not None:
            await store.close()

    async def pull_now(self) -> bool:
        """在后台立即拉取一次；已有拉取在进行时返回 False。"""

        scheduler = self.scheduler
        if scheduler is None:
            return False
        if scheduler.pull_lock.locked():
            return False
        create_task = getattr(self.ctx, "create_task", None)
        coroutine = scheduler.pull_round()
        if callable(create_task):
            create_task(coroutine, name="rss-news-pull-manual")
        else:
            asyncio.create_task(coroutine)
        return True

    # ------------------------------------------------------------------ 配置热重载

    def register_config_consumer(self) -> None:
        control = getattr(self.ctx, "plugin_control", None)
        register = getattr(control, "register_config_consumer", None)
        if not callable(register):
            return
        plugin_name = str(getattr(self.ctx, "plugin_name", "NeoBot_RSS_News"))
        consumer = RssNewsConfigConsumer(self, plugin_name)
        try:
            register(plugin_name, consumer)
        except Exception as exc:  # noqa: BLE001 - 宿主接口异常不应阻断插件
            self.log_warning(f"注册配置热重载失败：{type(exc).__name__}")
            return
        self._consumer = consumer
        self._consumer_control = control

    def unregister_config_consumer(self) -> None:
        consumer, self._consumer = self._consumer, None
        control, self._consumer_control = self._consumer_control, None
        if consumer is None or control is None:
            return
        unregister = getattr(control, "unregister_config_consumer", None)
        if callable(unregister):
            try:
                unregister(consumer.name)
            except Exception as exc:  # noqa: BLE001 - 卸载阶段只记录日志
                self.log_warning(f"注销配置热重载失败：{type(exc).__name__}")

    def log_warning(self, message: str) -> None:
        if self.logger is None:
            return
        try:
            self.logger.warning(message[:500])
        except Exception:  # noqa: BLE001, S110 - 日志失败不能反过来影响业务
            pass


__all__ = [
    "CONFIG_PATHS",
    "DATABASE_NAME",
    "PluginHotReloadRule",
    "RssNewsConfigConsumer",
    "RssNewsRuntime",
]
