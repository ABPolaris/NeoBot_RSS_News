"""聊天指令：RSS新闻 / 所有RSS新闻 / RSS列表 / 设置昵称。"""

from __future__ import annotations

from neobot_modloader import Plugin

from .errors import DuplicateValueError, NotFoundError
from .models import Subscription
from .runtime import RssNewsRuntime

GROUP_ONLY = "新闻仅支持在私聊中查看。"
MUTED = "新闻发送当前已关闭。"
NO_SUBSCRIPTIONS = "当前没有 RSS 订阅。"
MULTIPLE_SOURCES = "一次只能查询一个订阅。"
NOT_FOUND = "未找到订阅：{}"
AMBIGUOUS = "存在多个匹配的订阅，请使用更精确的名称。"
NOT_READY = "插件尚未就绪，请稍后重试。"

_runtime: RssNewsRuntime | None = None


def set_runtime(runtime: RssNewsRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def _current() -> RssNewsRuntime | None:
    return _runtime


def _find_source(
    subscriptions: list[Subscription], query: str
) -> tuple[Subscription | None, str | None]:
    """按名称或昵称精确匹配；多段参数视为一次查询多个源。"""

    text = query.strip()
    matches = [
        subscription
        for subscription in subscriptions
        if subscription.name == text
        or (subscription.nickname and subscription.nickname == text)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, AMBIGUOUS
    if len(text.split()) > 1:
        return None, MULTIPLE_SOURCES
    return None, NOT_FOUND.format(text)


def _find_by_full_name(
    subscriptions: list[Subscription], args: str
) -> tuple[Subscription | None, str]:
    """把“订阅全称 昵称”拆开；订阅全称允许包含空格。"""

    matches = [
        subscription
        for subscription in subscriptions
        if args == subscription.name
        or args.startswith(subscription.name + " ")
    ]
    if not matches:
        return None, ""
    longest = max(len(subscription.name) for subscription in matches)
    candidates = [
        subscription for subscription in matches if len(subscription.name) == longest
    ]
    if len(candidates) != 1:
        return None, ""
    target = candidates[0]
    return target, args[len(target.name) :].strip()


def _format_sources(subscriptions: list[Subscription]) -> str:
    return "\n".join(subscription.display_name for subscription in subscriptions)


def register(plugin: Plugin) -> None:
    @plugin.command(
        "RSS新闻 [source:rest]",
        permission=0,
        description="获取指定 RSS 源（或默认优先级最高的源）的最近文章",
    )
    async def rss_news(source: str | None, command_context, ctx) -> None:
        if command_context.kind == "group":
            await command_context.reply_plain(GROUP_ONLY)
            return
        runtime = _current()
        if runtime is None or runtime.store is None:
            await command_context.reply_plain(NOT_READY)
            return
        if not runtime.config.send_enabled:
            await command_context.reply_plain(MUTED)
            return
        subscriptions = await runtime.store.list_subscriptions()
        if not subscriptions:
            await command_context.reply_plain(NO_SUBSCRIPTIONS)
            return
        query = (source or "").strip()
        if query:
            target, error = _find_source(subscriptions, query)
            if error is not None or target is None:
                await command_context.reply_plain(error or NOT_FOUND.format(query))
                return
        else:
            target = min(subscriptions, key=lambda item: item.weight)
        try:
            ctx.create_task(
                runtime.delivery.deliver_one(target.id, command_context.user_id),
                name=f"rss-news-{target.id}",
            )
        except Exception:  # noqa: BLE001 - 插件正在停止等边界
            await command_context.reply_plain("插件正在停止，请稍后重试。")

    @plugin.command(
        "所有RSS新闻",
        permission=0,
        description="按优先级发送所有 RSS 源的最近文章",
    )
    async def all_rss_news(command_context, ctx) -> None:
        if command_context.kind == "group":
            await command_context.reply_plain(GROUP_ONLY)
            return
        runtime = _current()
        if runtime is None or runtime.store is None:
            await command_context.reply_plain(NOT_READY)
            return
        if not runtime.config.send_enabled:
            await command_context.reply_plain(MUTED)
            return
        subscriptions = await runtime.store.list_subscriptions()
        if not subscriptions:
            await command_context.reply_plain(NO_SUBSCRIPTIONS)
            return
        try:
            ctx.create_task(
                runtime.delivery.deliver_all(command_context.user_id),
                name="rss-news-all",
            )
        except Exception:  # noqa: BLE001 - 插件正在停止等边界
            await command_context.reply_plain("插件正在停止，请稍后重试。")

    @plugin.command(
        "RSS列表",
        permission=0,
        description="查看当前 RSS 订阅的名称与简称",
    )
    async def rss_list(command_context) -> None:
        runtime = _current()
        if runtime is None or runtime.store is None:
            await command_context.reply_plain(NOT_READY)
            return
        subscriptions = await runtime.store.list_subscriptions()
        if not subscriptions:
            await command_context.reply_plain(NO_SUBSCRIPTIONS)
            return
        await command_context.reply_plain(_format_sources(subscriptions))

    @plugin.command(
        "设置昵称 <args:rest>",
        permission=1,
        description="设置订阅简称：设置昵称 订阅全称 [订阅昵称]",
    )
    async def set_nickname(args: str, command_context) -> None:
        runtime = _current()
        if runtime is None or runtime.store is None:
            await command_context.reply_plain(NOT_READY)
            return
        text = (args or "").strip()
        subscriptions = await runtime.store.list_subscriptions()
        if not subscriptions:
            await command_context.reply_plain(NO_SUBSCRIPTIONS)
            return
        target, nickname = _find_by_full_name(subscriptions, text)
        if target is None:
            await command_context.reply_plain(f"未找到订阅全称：{text}")
            return
        try:
            updated = await runtime.store.set_nickname(target.id, nickname)
        except DuplicateValueError as exc:
            await command_context.reply_plain(str(exc))
            return
        except NotFoundError:
            await command_context.reply_plain("订阅不存在或已被删除。")
            return
        except ValueError as exc:
            await command_context.reply_plain(str(exc))
            return
        if updated.nickname:
            await command_context.reply_plain(
                f"已将“{updated.name}”的简称设置为“{updated.nickname}”。"
            )
        else:
            await command_context.reply_plain(f"已清空“{updated.name}”的简称。")


__all__ = [
    "AMBIGUOUS",
    "GROUP_ONLY",
    "MULTIPLE_SOURCES",
    "MUTED",
    "NOT_FOUND",
    "NOT_READY",
    "NO_SUBSCRIPTIONS",
    "register",
    "set_runtime",
]
