"""面板动作：订阅管理、状态、缓存文章与只读发送测试。"""

from __future__ import annotations

import time

from neobot_modloader import ActionError, Plugin

from . import payloads as p
from .composer import pack_messages, starvation_warning
from .errors import (
    DeliveryError,
    DuplicateValueError,
    FetchError,
    NotFoundError,
    RssNewsError,
    SubscriptionLimitError,
    UnsafeURLError,
)
from .models import Subscription
from .net import validate_feed_url
from .runtime import RssNewsRuntime
from .storage import MAX_SUBSCRIPTIONS, RssNewsStore, available_weights

_runtime: RssNewsRuntime | None = None


def set_runtime(runtime: RssNewsRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def _store() -> tuple[RssNewsRuntime, RssNewsStore]:
    runtime = _runtime
    if runtime is None or runtime.store is None:
        raise ActionError("not_ready", "插件尚未就绪，请稍后重试。")
    return runtime, runtime.store


def _sub_item(subscription: Subscription) -> p.SubscriptionItem:
    return p.SubscriptionItem(
        id=subscription.id,
        name=subscription.name,
        url=subscription.url,
        nickname=subscription.nickname,
        weight=subscription.weight,
        created_at=subscription.created_at,
        pull_at=subscription.pull_at,
        article_count=subscription.article_count,
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
        description="列出订阅、简称、权重与已缓存文章数。",
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
        description="添加一条 RSS 订阅；最多 7 条，URL 与昵称不能重复。",
        permission="manage",
        input_model=p.SubscriptionCreateInput,
        output_model=p.SubscriptionCreateOutput,
    )
    async def create_subscription(values, invocation):
        runtime, store = _store()
        try:
            url = await validate_feed_url(values.url, allow_unresolved=True)
            subscription = await store.add_subscription(
                values.name.strip(),
                url,
                values.nickname,
                values.weight,
                int(time.time()),
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
        description="修改名称、简称、URL 或权重；修改 URL 会清空该订阅的缓存。",
        permission="manage",
        input_model=p.SubscriptionUpdateInput,
        output_model=p.SubscriptionUpdateOutput,
    )
    async def update_subscription(values, invocation):
        runtime, store = _store()
        if (
            values.name is None
            and values.url is None
            and values.nickname is None
            and values.weight is None
        ):
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
                nickname=values.nickname,
                nickname_changed=values.nickname is not None,
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
        description="删除订阅，并级联删除其文章缓存。",
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
                )
                for item in items
            ]
        )

    @plugin.action(
        "status.get",
        title="查看运行状态",
        description="查看发送开关、缓存计数与最近拉取时间。",
        permission="read",
        output_model=p.StatusOutput,
    )
    async def get_status(values, invocation):
        runtime, store = _store()
        config = runtime.config
        subscriptions = await store.list_subscriptions()
        return p.StatusOutput(
            server_time=int(time.time()),
            send_enabled=config.send_enabled,
            content_format=config.content_format,
            per_source_limit=config.per_source_limit,
            per_destination_limit=config.per_destination_limit,
            pull_interval_minutes=config.pull_interval_minutes,
            cache_days=config.cache_days,
            message_interval_seconds=config.message_interval_seconds,
            subscription_count=len(subscriptions),
            article_count=await store.article_count(),
            latest_pull_at=await store.latest_pull_at(),
            warning=starvation_warning(config, len(subscriptions)),
            supports_send_test=True,
        )

    @plugin.action(
        "system.send_test",
        title="发送测试",
        description=(
            "随机读取一篇缓存文章，按指定内容发送到私聊 QQ。"
            "只读数据库与配置，不写入任何记录，也不修改配置。"
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
                "缓存中没有可发送的文章，请先请求一次新闻后再试。",
            )
        article, source_name = picked
        sender = runtime.sender
        if sender is None:
            raise ActionError("not_ready", "插件尚未就绪，请稍后重试。")
        bundles = pack_messages([article], values.content_format)
        if not bundles:
            raise ActionError("empty_message", "这篇文章没有可发送的内容。")
        text = bundles[0].text
        try:
            await sender.send_text(int(values.qq), text)
        except DeliveryError as exc:
            raise ActionError("send_failed", f"测试消息发送失败：{exc}") from None
        except Exception:  # noqa: BLE001 - 适配器可抛任意异常
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
