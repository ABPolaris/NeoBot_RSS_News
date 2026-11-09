# ruff: noqa: N999 - 目录名由插件 ID NeoBot_RSS_News 决定
"""NeoBot_RSS_News：定期从 RSS 源获取文章并私聊发送给接收者。

模块分工：
- ``config`` 面板配置；``storage`` SQLite 存储；``net``/``fetch`` 抓取；
- ``scheduler`` 拉取与发送循环；``composer`` 消息组装；
- ``actions`` 面板动作；``web/`` 管理页面。
"""

from __future__ import annotations

from typing import Any

from neobot_modloader import Plugin

from . import actions
from .config import Config
from .runtime import RssNewsRuntime

plugin = Plugin(
    "NeoBot_RSS_News",
    version="0.1.0",
    description="定期从 RSS 源获取新闻并私聊发送给接收者",
    author="AkiPolaris",
    config=Config,
    hot_reload=True,
    config_hot_reload=True,
)

actions.register_surface(plugin)

plugin.view(
    "management",
    title="RSS 订阅管理",
    entry="web/index.html",
    anchors=[
        "status",
        "subscriptions",
        "subscription-form",
        "destinations",
        "destination-form",
        "send-test",
        "articles",
    ],
)

_DEFAULT_EXAMPLE = Config().model_dump()


def _example(**overrides: Any) -> dict[str, Any]:
    return {**_DEFAULT_EXAMPLE, **overrides}


plugin.guide(
    "first-use",
    title="从零配置 RSS 新闻",
    steps=[
        {
            "target": "config",
            "form": "start",
            "chapter": "打开配置",
            "text": (
                "在插件管理选择 NeoBot_RSS_News，打开配置表单。"
                "这里从公开默认值开始演示，真实配置不会被修改。"
                "订阅与接收者在“插件页面”中管理。"
            ),
            "example": _example(),
        },
        {
            "target": "config.send_enabled",
            "form": "edit",
            "chapter": "开启定期发送",
            "text": (
                "默认不发送。打开“启用定期发送”后，插件才会按发送周期和时间"
                "向启用的接收者发送文章；接收者仍需在插件页面添加。"
            ),
            "example": _example(send_enabled=True),
        },
        {
            "target": "config.send_every_days",
            "form": "edit",
            "chapter": "设置发送周期",
            "text": (
                "填写每几天发送一次，范围 1 至 7 天。例如 1 表示每天，3 表示每三天。"
                "发送窗口从下方发送时间起 5 分钟。"
            ),
            "example": _example(send_enabled=True, send_every_days=3),
        },
        {
            "target": "config.send_time",
            "form": "edit",
            "chapter": "设置发送时间",
            "text": (
                "使用 24 小时制 HH:MM，例如 08:00 或 18:30。"
                "保存配置后，插件会按新时间重算每个接收者的下次发送时间。"
            ),
            "example": _example(
                send_enabled=True, send_every_days=3, send_time="08:00"
            ),
        },
        {
            "target": "config.send_format",
            "form": "edit",
            "chapter": "选择发送格式",
            "text": (
                "“消息-分订阅发送”为默认值：每个订阅的文章合成一组消息。"
                "也可以选择尽可能合并或分条发送；单条消息最多 3950 字。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
            ),
        },
        {
            "target": "config.content_format",
            "form": "edit",
            "chapter": "选择发送内容",
            "text": (
                "默认发送标题、链接和 200 字开头。若源文章没有正文，"
                "涉及开头和正文的选项会退化为仅标题。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
                content_format="title_body",
            ),
        },
        {
            "target": "config.per_source_limit",
            "form": "edit",
            "chapter": "设置数量上限",
            "text": (
                "每订阅发送文章数默认 10 篇，每接收者文章总数默认 50 篇。"
                "若“订阅数 × 每订阅上限”大于接收总数，后面的订阅可能长期发不出文章，"
                "插件页面会显示警告。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
            ),
        },
        {
            "target": "config",
            "form": "validate",
            "chapter": "核对配置",
            "text": (
                "核对发送周期、时间、格式与数量上限。这里只演示本地检查；"
                "真实保存仍由服务端校验，错误时按字段提示修正。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
            ),
        },
        {
            "target": "config",
            "form": "save",
            "chapter": "保存配置",
            "text": (
                "填写完后使用底部保存。保存后配置原地生效，不必重启插件；"
                "这一阶段只演示保存位置，不会发送真实保存请求。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
            ),
        },
        {
            "target": "config",
            "form": "saved",
            "chapter": "查看生效结果",
            "text": (
                "真实保存后，打开“插件页面”添加 RSS 订阅与接收者 QQ。"
                "插件会立即拉取一次，之后按拉取间隔增量更新；"
                "首次使用请确认接收者 QQ 能收到私聊。"
            ),
            "example": _example(
                send_enabled=True,
                send_every_days=3,
                send_time="08:00",
                send_format="merged",
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
            ),
        },
    ],
    demo={
        "config": _example(
            send_enabled=True,
            send_every_days=1,
            send_time="18:00",
            per_source_limit=10,
            per_destination_limit=50,
        )
    },
)

_runtime: RssNewsRuntime | None = None


@plugin.on_load
async def _on_load(ctx: Any) -> None:
    global _runtime
    runtime = RssNewsRuntime.from_context(ctx)
    await runtime.load()
    _runtime = runtime
    actions.set_runtime(runtime)


@plugin.on_startup
async def _on_startup(ctx: Any) -> None:
    runtime = _runtime
    if runtime is not None:
        runtime.start()


@plugin.on_shutdown
async def _on_shutdown(ctx: Any) -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    actions.set_runtime(None)
    if runtime is not None:
        await runtime.stop()
