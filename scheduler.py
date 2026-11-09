"""后台调度：并发拉取 RSS、清理缓存、按时窗口串行发送。

拉取和发送分别是两个托管任务；发送任务本身串行处理所有接收者，
因此不会出现同一接收者被并发写入已发送记录的情况。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .composer import compose_messages
from .config import Config
from .errors import DeliveryError
from .models import Article, Destination, FeedResult, PullReport, SendReport
from .schedule import compute_next_send, in_send_window, missed_window
from .storage import RssNewsStore

PULL_POLL_SECONDS = 20
SEND_POLL_SECONDS = 15
MAX_CONCURRENT_FETCHES = 4
FAILURE_DISABLE_THRESHOLD = 5
FAILURE_ROUNDS_THRESHOLD = 3


class FeedFetcher(Protocol):
    async def fetch(self, url: str) -> FeedResult: ...


class TextSender(Protocol):
    async def send_text(self, destination_id: int, text: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _DeliveryOutcome:
    messages: int = 0
    sent: int = 0
    failed: int = 0
    disabled: bool = False


class RssScheduler:
    def __init__(
        self,
        store: RssNewsStore,
        fetcher: FeedFetcher,
        sender: TextSender,
        config: Config,
        *,
        logger: Any | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.sender = sender
        self.config = config
        self.logger = logger
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._tasks: list[asyncio.Task[Any]] = []
        self._stop: asyncio.Event | None = None
        self._last_send_monotonic = 0.0
        self.pull_lock = asyncio.Lock()

    # ------------------------------------------------------------------ 生命周期

    @property
    def running(self) -> bool:
        return self._stop is not None and not self._stop.is_set()

    def start(self, create_task: Callable[..., asyncio.Task[Any]] | None = None) -> None:
        if self._stop is not None:
            return
        self._stop = asyncio.Event()
        spawn = create_task or asyncio.create_task
        self._tasks = [
            spawn(self._pull_loop(), name="rss-news-pull"),
            spawn(self._send_loop(), name="rss-news-send"),
        ]

    async def stop(self) -> None:
        stop = self._stop
        if stop is None:
            return
        stop.set()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._stop = None

    async def _wait(self, seconds: float) -> None:
        stop = self._stop
        if stop is None:
            await self._sleep(seconds)
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    # ------------------------------------------------------------------ 配置

    async def update_config(self, config: Config) -> None:
        old = self.config
        self.config = config
        changed = (
            old.send_enabled != config.send_enabled
            or old.send_every_days != config.send_every_days
            or old.send_time != config.send_time
            or old.timezone != config.timezone
        )
        if not changed:
            return
        now = int(self._clock())
        for destination in await self.store.list_destinations():
            next_send = compute_next_send(destination.last_sent, now, config)
            await self.store.set_next_send(destination.destination_id, next_send)
        self._log("info", "发送周期/时间/时区已变化，已按新配置重算全部接收者的下次发送时间")

    # ------------------------------------------------------------------ 拉取

    async def _pull_loop(self) -> None:
        while self._stop is not None and not self._stop.is_set():
            try:
                await self.pull_round()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不能让后台循环退出
                self._log("error", f"RSS 拉取轮次失败：{type(exc).__name__}")
            await self._wait(PULL_POLL_SECONDS)

    async def pull_round(self, now: int | None = None) -> PullReport:
        async with self.pull_lock:
            return await self._pull_round(now)

    async def _pull_round(self, now: int | None = None) -> PullReport:
        current = int(self._clock()) if now is None else int(now)
        config = self.config
        due = await self.store.list_due_subscriptions(current, config.pull_interval_seconds)
        if not due:
            return PullReport()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

        async def fetch_one(subscription_id: int, name: str, url: str) -> tuple[bool, int]:
            async with semaphore:
                try:
                    result = await self.fetcher.fetch(url)
                    inserted = await self.store.insert_articles(
                        subscription_id, result.entries, int(self._clock())
                    )
                    return True, inserted
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单个源失败需要隔离
                    self._log(
                        "warning",
                        f"拉取订阅「{name}」失败：{type(exc).__name__}",
                    )
                    return False, 0
                finally:
                    try:
                        await self.store.set_pull_at(subscription_id, int(self._clock()))
                    except Exception:  # noqa: BLE001, S110 - 记录 pull_at 失败不阻断本轮
                        pass

        tasks = [
            asyncio.create_task(fetch_one(sub.id, sub.name, sub.url))
            for sub in due
        ]
        budget = max(5.0, config.pull_interval_seconds * 0.75 - (self._clock() - current))
        timed_out = False
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=budget
            )
        except TimeoutError:
            timed_out = True
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        succeeded = failed = inserted = 0
        for item in results:
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], bool)
                and isinstance(item[1], int)
            ):
                if item[0]:
                    succeeded += 1
                    inserted += item[1]
                else:
                    failed += 1
            else:
                failed += 1
        try:
            await self.store.cleanup_articles(int(self._clock()), config.cache_days)
        except Exception as exc:  # noqa: BLE001 - 清理失败只记录日志
            self._log("warning", f"清理文章缓存失败：{type(exc).__name__}")
        report = PullReport(
            due=len(due),
            succeeded=succeeded,
            failed=failed,
            inserted=inserted,
            timed_out=timed_out,
        )
        self._log(
            "info",
            f"RSS 拉取完成：{succeeded}/{len(due)} 个源成功，新增 {inserted} 篇"
            + ("（整轮超时，已取消未完成源）" if timed_out else ""),
        )
        return report

    # ------------------------------------------------------------------ 发送

    async def _send_loop(self) -> None:
        try:
            await self.prepare_destinations()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 初始化失败后仍继续轮询
            self._log("warning", f"初始化发送时间失败：{type(exc).__name__}")
        while self._stop is not None and not self._stop.is_set():
            try:
                await self.send_due_round()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不能让后台循环退出
                self._log("error", f"发送轮次失败：{type(exc).__name__}")
            await self._wait(SEND_POLL_SECONDS)

    async def prepare_destinations(self, now: int | None = None) -> None:
        """启动时补齐 next_send；错过的窗口直接快进，不补发。"""

        current = int(self._clock()) if now is None else int(now)
        config = self.config
        for destination in await self.store.list_destinations():
            if destination.next_send is None:
                next_send = compute_next_send(destination.last_sent, current, config)
                await self.store.set_next_send(destination.destination_id, next_send)
                continue
            if missed_window(
                destination.next_send, current, config.send_window_seconds
            ):
                next_send = compute_next_send(destination.last_sent, current, config)
                await self.store.set_next_send(destination.destination_id, next_send)
                self._log(
                    "info",
                    f"接收者 {destination.destination_id} 错过发送窗口，"
                    "已快进到下一次发送时间（不补发）",
                )

    async def send_due_round(self, now: int | None = None) -> SendReport:
        config = self.config
        if not config.send_enabled:
            return SendReport()
        current = int(self._clock()) if now is None else int(now)
        destinations = 0
        messages = 0
        sent = 0
        failed = 0
        disabled: list[int] = []
        # 每轮只读一次接收者列表；只有真正进入发送窗口的接收者才再读一次，
        # 避免在 5 分钟窗口外反复按 id 查询。
        for destination in await self.store.list_destinations():
            if not destination.enabled:
                continue
            if destination.next_send is None:
                await self.store.set_next_send(
                    destination.destination_id,
                    compute_next_send(destination.last_sent, current, config),
                )
                continue
            if current < destination.next_send:
                continue
            if missed_window(destination.next_send, current, config.send_window_seconds):
                await self.store.set_next_send(
                    destination.destination_id,
                    compute_next_send(destination.last_sent, current, config),
                )
                self._log(
                    "info",
                    f"接收者 {destination.destination_id} 错过发送窗口，已快进（不补发）",
                )
                continue
            if not in_send_window(
                destination.next_send, current, config.send_window_seconds
            ):
                continue
            outcome = await self._send_to_destination(destination, current)
            destinations += 1
            messages += outcome.messages
            sent += outcome.sent
            failed += outcome.failed
            if outcome.disabled:
                disabled.append(destination.destination_id)
            current = int(self._clock())
        if destinations:
            self._log(
                "info",
                f"本轮发送：{destinations} 个接收者，{messages} 条消息，"
                f"成功 {sent} 篇，失败 {failed} 篇",
            )
        return SendReport(
            destinations=destinations,
            messages=messages,
            sent=sent,
            failed=failed,
            disabled=tuple(disabled),
        )

    async def _send_to_destination(
        self, destination: Destination, now: int
    ) -> _DeliveryOutcome:
        config = self.config
        # 进入窗口后再确认启停，防止列表读取后用户刚把他暂停。
        latest = await self.store.get_destination(destination.destination_id)
        if latest is None or not latest.enabled:
            return _DeliveryOutcome()
        destination = latest
        subscriptions = await self.store.list_subscriptions()
        skipped = await self.store.mark_skipped(destination.destination_id)
        if skipped:
            self._log(
                "info",
                f"接收者 {destination.destination_id}：{skipped} 篇文章发送失败次数过多，跳过发送",
            )
        groups: list[tuple[int, list[Article]]] = []
        total = 0
        for subscription in subscriptions:
            remaining = config.per_destination_limit - total
            if remaining <= 0:
                break
            limit = min(config.per_source_limit, remaining)
            articles = await self.store.list_candidates(
                destination.destination_id,
                subscription.id,
                subscription.created_at,
                limit,
            )
            if articles:
                groups.append((subscription.id, articles))
                total += len(articles)
        next_send = compute_next_send(now, now, config)
        # 开始发送就推进 next_send，避免重启或边界情况下重复发送。
        await self.store.mark_send_started(
            destination.destination_id, now, next_send
        )
        if not groups:
            self._log(
                "info", f"接收者 {destination.destination_id} 本轮没有待发送文章"
            )
            return _DeliveryOutcome()
        bundles = compose_messages(groups, config)
        sent = failed = 0
        last_error = ""
        stopped_by_user = False
        disabled = False
        for bundle in bundles:
            if not self.config.send_enabled:
                stopped_by_user = True
                break
            latest = await self.store.get_destination(destination.destination_id)
            if latest is None or not latest.enabled:
                stopped_by_user = True
                break
            await self._space_out()
            try:
                await self.sender.send_text(destination.destination_id, bundle.text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 适配器可抛任意异常，统一记账
                last_error = (
                    str(exc)[:120] if isinstance(exc, DeliveryError) else type(exc).__name__
                )
                failed += len(bundle.articles)
                for article in bundle.articles:
                    await self.store.record_failure(
                        destination.destination_id, article, int(self._clock())
                    )
                self._log(
                    "warning",
                    f"向接收者 {destination.destination_id} 发送失败"
                    f"（{len(bundle.articles)} 篇）：{type(exc).__name__}",
                )
                if failed >= FAILURE_DISABLE_THRESHOLD:
                    reason = (
                        f"本轮发送失败 {failed} 篇（达到 {FAILURE_DISABLE_THRESHOLD} 篇），"
                        "已自动暂停发送；请检查机器人连接后重新启用。"
                        f"最近错误：{last_error}"
                    )
                    await self.store.disable_destination(
                        destination.destination_id, reason
                    )
                    disabled = True
                    self._log(
                        "warning",
                        f"接收者 {destination.destination_id} 本轮失败达到上限，已暂停发送",
                    )
                    break
            else:
                sent += len(bundle.articles)
                for article in bundle.articles:
                    await self.store.record_sent(
                        destination.destination_id, article, int(self._clock())
                    )
            finally:
                self._last_send_monotonic = self._monotonic()
        attempted = sent + failed
        if not disabled and not stopped_by_user and attempted:
            if failed == attempted:
                rounds = destination.consecutive_failed_rounds + 1
                await self.store.set_failed_rounds(
                    destination.destination_id, rounds
                )
                if rounds >= FAILURE_ROUNDS_THRESHOLD:
                    reason = (
                        f"连续 {rounds} 轮发送全部失败，已自动暂停发送；"
                        "请检查机器人连接后重新启用。"
                        f"最近错误：{last_error}"
                    )
                    await self.store.disable_destination(
                        destination.destination_id, reason
                    )
                    disabled = True
                    self._log(
                        "warning",
                        f"接收者 {destination.destination_id} 已连续 {rounds} 轮全部失败，已暂停发送",
                    )
            else:
                await self.store.set_failed_rounds(destination.destination_id, 0)
        return _DeliveryOutcome(
            messages=len(bundles),
            sent=sent,
            failed=failed,
            disabled=disabled,
        )

    async def _space_out(self) -> None:
        if self._last_send_monotonic <= 0:
            return
        delay = (
            self._last_send_monotonic
            + float(self.config.message_interval_seconds)
            - self._monotonic()
        )
        if delay > 0:
            await self._sleep(delay)

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message[:500])
        except Exception:  # noqa: BLE001, S110 - 日志失败不能反过来影响业务
            pass


__all__ = [
    "FAILURE_DISABLE_THRESHOLD",
    "FAILURE_ROUNDS_THRESHOLD",
    "MAX_CONCURRENT_FETCHES",
    "RssScheduler",
]
