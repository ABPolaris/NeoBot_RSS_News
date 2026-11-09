"""面板动作：订阅、接收者、状态与手动拉取。

页面只通过声明动作访问业务；动作层负责把存储错误转换为可公开的中文
ActionError，未知异常仍由 SDK 隐藏为通用失败。
"""

from __future__ import annotations

import time

from neobot_modloader import ActionError, Plugin

from . import payloads as p
from .composer import compose_messages, starvation_warning
from .errors import (
    DeliveryError,
    DuplicateValueError,
    FetchError,
    NotFoundError,
    RssNewsError,
    SubscriptionLimitError,
    UnsafeURLError,
)
from .models import Destination, Subscription
from .net import validate_feed_url
from .runtime import RssNewsRuntime
from .schedule import compute_next_send
from .storage import MAX_SUBSCRIPTIONS, RssNewsStore, available_weights

_runtime: RssNewsRuntime | None = None


def set_runtime(runtime: RssNewsRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def _store() -> tuple[RssNewsRuntime, RssNewsStore]:
    runtime = _runtime
    if runtime is None or runtime.store is None or runtime.config is None:
        raise ActionError("not_ready", "插件尚未就绪，请稍后重试。")
    return runtime, runtime.store


def _sub_item(subscription: Subscription) -> p.SubscriptionItem:
    return p.SubscriptionItem(
        id=subscription.id,
        name=subscription.name,
        url=subscription.url,
        weight=subscription.weight,
        created_at=subscription.created_at,
        pull_at=subscription.pull_at,
        article_count=subscription.article_count,
    )


def _destination_item(destination: Destination) -> p.DestinationItem:
    return p.DestinationItem(
        destination_id=destination.destination_id,
        enabled=destination.enabled,
        last_sent=destination.last_sent,
        next_send=destination.next_send,
        reason=destination.reason,
        consecutive_failed_rounds=destination.consecutive_failed_rounds,
    )


def _business_error(exc: Exception) -> ActionError:
    if isinstance(exc, UnsafeURLError):
        return ActionError("unsafe_url", str(exc))
    if isinstance(exc, FetchError):
        return ActionError("invalid_url", str(exc))
    if isinstance(exc, SubscriptionLimitError):
        return ActionError("limit_reached", str(exc))
    if isinstance(exc, DuplicateValueError):
        return ActionError("duplicate", str(exc))
    if isinstance(exc, NotFoundError):
        return ActionError("not_found", str(exc))
    if isinstance(exc, ValueError):
        return ActionError("invalid_input", str(exc))
    return ActionError("action_failed", "操作未完成，请稍后重试。")


def register_surface(plugin: Plugin) -> None:
    @plugin.action(
        "subscriptions.list",
        title="查看订阅",
        description="列出订阅、权重与已缓存文章数。",
        permission="read",
        output_model=p.SubscriptionListOutput,
    )
    async def list_subscriptions(values, invocation):
        runtime, store = _store()
        subscriptions = await store.list_subscriptions()
        return p.SubscriptionListOutput(
            items=[_sub_item(item) for item in subscriptions],
            max_count=MAX_SUBSCRIPTIONS,
            available_weights=available_weights(subscriptions),
            warning=starvation_warning(runtime.config, len(subscriptions)),
        )

    @plugin.action(
        "subscriptions.create",
        title="添加订阅",
        description="添加一条 RSS 订阅；最多 7 条，URL 不能重复。",
        permission="manage",
        input_model=p.SubscriptionCreateInput,
        output_model=p.SubscriptionCreateOutput,
    )
    async def create_subscription(values, invocation):
        runtime, store = _store()
        try:
            url = await validate_feed_url(values.url, allow_unresolved=True)
            subscription = await store.add_subscription(
                values.name.strip(), url, values.weight, int(time.time())
            )
        except (RssNewsError, ValueError) as exc:
            raise _business_error(exc) from None
        subscriptions = await store.list_subscriptions()
        return p.SubscriptionCreateOutput(
            item=_sub_item(subscription),
            warning=starvation_warning(runtime.config, len(subscriptions)),
        )

    @plugin.action(
        "subscriptions.update",
        title="修改订阅",
        description="修改名称、URL 或权重；修改 URL 会清空该订阅的缓存与已发送记录。",
        permission="manage",
        input_model=p.SubscriptionUpdateInput,
        output_model=p.SubscriptionUpdateOutput,
    )
    async def update_subscription(values, invocation):
        runtime, store = _store()
        if values.name is None and values.url is None and values.weight is None:
            raise ActionError("invalid_input", "请至少修改一项内容。")
        try:
            url = (
                await validate_feed_url(values.url, allow_unresolved=True)
                if values.url is not None
                else None
            )
            subscription, url_changed = await store.update_subscription(
                values.id,
                name=values.name.strip() if values.name is not None else None,
                url=url,
                weight=values.weight,
                now=int(time.time()),
            )
        except (RssNewsError, ValueError) as exc:
            raise _business_error(exc) from None
        subscriptions = await store.list_subscriptions()
        return p.SubscriptionUpdateOutput(
            item=_sub_item(subscription),
            url_changed=url_changed,
            warning=starvation_warning(runtime.config, len(subscriptions)),
        )

    @plugin.action(
        "subscriptions.remove",
        title="删除订阅",
        description="删除订阅，并级联删除其文章与已发送记录。",
        permission="manage",
        input_model=p.SubscriptionRemoveInput,
        output_model=p.RemovedOutput,
    )
    async def remove_subscription(values, invocation):
        _runtime, store = _store()
        try:
            removed = await store.delete_subscription(values.id)
        except (RssNewsError, ValueError) as exc:
            raise _business_error(exc) from None
        if not removed:
            raise ActionError("not_found", "订阅不存在或已被删除。")
        return p.RemovedOutput(removed=True)

    @plugin.action(
        "destinations.list",
        title="查看接收者",
        description="列出接收者 QQ、启停状态、发送时间与失败原因。",
        permission="read",
        output_model=p.DestinationListOutput,
    )
    async def list_destinations(values, invocation):
        runtime, store = _store()
        return p.DestinationListOutput(
            items=[_destination_item(item) for item in await store.list_destinations()],
            send_enabled=runtime.config.send_enabled,
        )

    @plugin.action(
        "destinations.create",
        title="添加接收者",
        description="添加一个私聊 QQ 接收者；QQ 号格式由插件检查，有效性由用户自行验证。",
        permission="manage",
        input_model=p.DestinationCreateInput,
        output_model=p.DestinationOutput,
    )
    async def create_destination(values, invocation):
        runtime, store = _store()
        now = int(time.time())
        next_send = compute_next_send(None, now, runtime.config)
        try:
            destination = await store.add_destination(int(values.qq), next_send, now)
        except (RssNewsError, ValueError) as exc:
            raise _business_error(exc) from None
        return p.DestinationOutput(item=_destination_item(destination))

    @plugin.action(
        "destinations.update",
        title="启停接收者",
        description="继续发送时清空失败原因与连续失败计数。",
        permission="manage",
        input_model=p.DestinationUpdateInput,
        output_model=p.DestinationOutput,
    )
    async def update_destination(values, invocation):
        _runtime, store = _store()
        if values.enabled is None:
            raise ActionError("invalid_input", "请指定是否继续发送。")
        destination_id = int(values.qq)
        existing = await store.get_destination(destination_id)
        if existing is None:
            raise ActionError("not_found", "接收者不存在或已被删除。")
        if values.enabled:
            destination = await store.set_destination_enabled(
                destination_id, True, None
            )
            await store.set_failed_rounds(destination_id, 0)
        else:
            destination = await store.set_destination_enabled(
                destination_id, False, existing.reason
            )
        return p.DestinationOutput(item=_destination_item(destination))

    @plugin.action(
        "destinations.remove",
        title="删除接收者",
        description="删除接收者并级联删除其已发送记录。",
        permission="manage",
        input_model=p.DestinationRemoveInput,
        output_model=p.RemovedOutput,
    )
    async def remove_destination(values, invocation):
        _runtime, store = _store()
        removed = await store.delete_destination(int(values.qq))
        if not removed:
            raise ActionError("not_found", "接收者不存在或已被删除。")
        return p.RemovedOutput(removed=True)

    @plugin.action(
        "articles.list",
        title="查看缓存文章",
        description="查看最近缓存文章，用于确认拉取是否正常。",
        permission="read",
        input_model=p.ArticleListInput,
        output_model=p.ArticleListOutput,
    )
    async def list_articles(values, invocation):
        _runtime, store = _store()
        items = await store.list_recent_articles(values.limit)
        return p.ArticleListOutput(
            items=[
                p.ArticleItem(
                    source_id=item.source_id,
                    source_name=item.source_name,
                    guid=item.guid,
                    title=item.title,
                    link=item.link,
                    pubdate=item.pubdate,
                    has_body=item.has_body,
                    deleted=item.deleted,
                )
                for item in items
            ]
        )

    @plugin.action(
        "status.get",
        title="查看运行状态",
        description="查看发送配置、缓存计数与下一次发送时间。",
        permission="read",
        output_model=p.StatusOutput,
    )
    async def get_status(values, invocation):
        runtime, store = _store()
        config = runtime.config
        destinations = await store.list_destinations()
        subscriptions = await store.list_subscriptions()
        upcoming = [
            item.next_send
            for item in destinations
            if item.enabled and item.next_send is not None
        ]
        scheduler = runtime.scheduler
        return p.StatusOutput(
            server_time=int(time.time()),
            running=bool(scheduler is not None and scheduler.running),
            send_enabled=config.send_enabled,
            send_every_days=config.send_every_days,
            send_time=config.send_time,
            timezone=config.timezone,
            send_format=config.send_format,
            content_format=config.content_format,
            per_source_limit=config.per_source_limit,
            per_destination_limit=config.per_destination_limit,
            pull_interval_minutes=config.pull_interval_minutes,
            cache_days=config.cache_days,
            message_interval_seconds=config.message_interval_seconds,
            subscription_count=len(subscriptions),
            destination_count=len(destinations),
            article_count=await store.article_count(),
            sent_count=await store.count_sent(),
            latest_pull_at=await store.latest_pull_at(),
            next_send=min(upcoming) if upcoming else None,
            warning=starvation_warning(config, len(subscriptions)),
            supports_send_test=True,
        )

    @plugin.action(
        "system.pull_now",
        title="立即拉取",
        description="在后台立即检查并拉取所有到期的 RSS 源。",
        permission="operate",
        output_model=p.PullNowOutput,
    )
    async def pull_now(values, invocation):
        runtime = _runtime
        if runtime is None:
            raise ActionError("not_ready", "插件尚未就绪，请稍后重试。")
        return p.PullNowOutput(started=await runtime.pull_now())

    @plugin.action(
        "system.send_test",
        title="发送测试",
        description=(
            "随机读取一篇缓存文章，按指定格式发送到私聊 QQ。"
            "只读数据库与配置，不写入已发送记录，也不修改接收者。"
        ),
        permission="manage",
        input_model=p.SendTestInput,
        output_model=p.SendTestOutput,
    )
    async def send_test(values, invocation):
        runtime, store = _store()
        picked = await store.random_article()
        if picked is None:
            raise ActionError(
                "no_articles",
                "缓存中没有可发送的文章，请先等待拉取完成后再试。",
            )
        article, source_name = picked
        sender = runtime.sender
        if sender is None:
            raise ActionError("not_ready", "插件尚未就绪，请稍后重试。")
        # 只改本次内存中的副本，不保存配置、不触碰接收者或已发送记录。
        config = runtime.config.model_copy(
            update={
                "send_format": values.send_format,
                "content_format": values.content_format,
            }
        )
        bundles = compose_messages([(article.source_id, [article])], config)
        if not bundles:
            raise ActionError("empty_message", "这篇文章没有可发送的内容。")
        text = bundles[0].text
        try:
            await sender.send_text(int(values.qq), text)
        except DeliveryError as exc:
            raise ActionError("send_failed", f"测试消息发送失败：{exc}") from None
        except Exception:  # noqa: BLE001 - 适配器可抛任意异常，统一转成业务失败
            raise ActionError(
                "send_failed", "测试消息发送失败，请检查机器人连接。"
            ) from None
        return p.SendTestOutput(
            sent=True,
            destination_id=int(values.qq),
            source_id=article.source_id,
            source_name=source_name,
            title=article.display_title,
            pubdate=article.pubdate,
            message_chars=len(text),
        )


__all__ = ["register_surface", "set_runtime"]
