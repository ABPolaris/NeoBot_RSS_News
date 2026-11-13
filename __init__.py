# ruff: noqa: N999 - 目录名由插件 ID NeoBot_RSS_News 决定
"""NeoBot_RSS_News：按聊天指令拉取 RSS 并私聊发送文章。

模块分工：
- ``config`` 面板配置；``storage`` SQLite 存储与迁移；``net``/``fetch`` 抓取；
- ``delivery`` 按需拉取与发送；``commands`` 聊天指令；
- ``composer`` 消息组装；``actions`` 面板动作；``web/`` 管理页面。
"""

from __future__ import annotations

from typing import Any

from neobot_modloader import Plugin

from . import actions, commands
from .config import Config
from .runtime import RssNewsRuntime

plugin = Plugin(
    "NeoBot_RSS_News",
    version="0.1.0",
    description="按聊天指令拉取 RSS 新闻并私聊发送",
    author="AkiPolaris",
    config=Config,
    hot_reload=True,
    config_hot_reload=True,
)

actions.register_surface(plugin)
commands.register(plugin)

plugin.view(
    "management",
    title="RSS 订阅管理",
    entry="web/index.html",
    anchors=[
        "status",
        "subscriptions",
        "subscription-form",
        "send-test",
        "commands",
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
                "订阅、简称和发送测试在“插件页面”中管理。"
            ),
            "example": _example(),
        },
        {
            "target": "config.send_enabled",
            "form": "edit",
            "chapter": "设置静音开关",
            "text": (
                "关闭“允许发送新闻”后，用户请求新闻时不会拉取或发送文章；"
                "RSS 列表和昵称设置仍然可用。"
            ),
            "example": _example(send_enabled=False),
        },
        {
            "target": "config.pull_interval_minutes",
            "form": "edit",
            "chapter": "设置拉取间隔",
            "text": (
                "插件不再定时拉取，只在用户请求新闻时检查每个源的上次拉取时间。"
                "距上次拉取不足此间隔就直接读缓存。范围 15 分钟至 12 小时。"
            ),
            "example": _example(pull_interval_minutes=30),
        },
        {
            "target": "config.content_format",
            "form": "edit",
            "chapter": "选择发送内容",
            "text": (
                "默认发送标题、链接和 200 字开头。若源文章没有正文，"
                "涉及开头和正文的选项会退化为仅标题。"
            ),
            "example": _example(content_format="title_body"),
        },
        {
            "target": "config.per_source_limit",
            "form": "edit",
            "chapter": "设置每订阅篇数",
            "text": "每条订阅每次请求最多发送的文章数，默认 10 篇。",
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
            ),
        },
        {
            "target": "config.per_destination_limit",
            "form": "edit",
            "chapter": "设置用户接收上限",
            "text": (
                "单次“所有RSS新闻”请求最多接收的文章总数，默认 50 篇。"
                "若订阅数 × 每订阅上限超过此值，插件页面会提示饿死风险。"
            ),
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
            ),
        },
        {
            "target": "config.cache_days",
            "form": "edit",
            "chapter": "设置缓存天数",
            "text": "文章在本地保留的天数，超期文章会在下一次请求时立即删除。",
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
                cache_days=14,
            ),
        },
        {
            "target": "config.message_interval_seconds",
            "form": "edit",
            "chapter": "设置消息间隔",
            "text": "“所有RSS新闻”连续发送多个订阅消息时的间隔，范围 1 至 5 秒。",
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
                cache_days=14,
                message_interval_seconds=3,
            ),
        },
        {
            "target": "config",
            "form": "validate",
            "chapter": "核对配置",
            "text": (
                "核对开关、拉取间隔、内容与数量上限。这里只演示本地检查；"
                "真实保存仍由服务端校验，错误时按字段提示修正。"
            ),
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
                cache_days=14,
                message_interval_seconds=3,
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
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
                cache_days=14,
                message_interval_seconds=3,
            ),
        },
        {
            "target": "config",
            "form": "saved",
            "chapter": "查看生效结果",
            "text": (
                "真实保存后，在私聊发送“RSS列表”查看订阅名称和简称，"
                "再发送“RSS新闻 简称”或“所有RSS新闻”获取文章。"
            ),
            "example": _example(
                content_format="title_body",
                per_source_limit=5,
                per_destination_limit=80,
                cache_days=14,
                message_interval_seconds=3,
            ),
        },
        {
            "target": "config",
            "chapter": "发送第一条指令",
            "text": (
                "在私聊发送“RSS新闻 中新网”。插件会检查该源是否到期拉取，"
                "然后读取缓存并发送最近文章；群聊中请求新闻只会得到仅支持私聊的提示。"
            ),
            "command": {
                "text": "/RSS新闻 中新网",
                "context": "在与机器人的私聊中发送",
                "reply": "（按配置发送最近文章）",
            },
        },
        {
            "target": "config",
            "chapter": "查看订阅列表",
            "text": "“RSS列表”在私聊和群聊都可以使用，返回“源名称（源简称）”。",
            "command": {
                "text": "/RSS列表",
                "context": "私聊或群聊中发送",
                "reply": "中新网要闻导读（中新网）\nIT之家（之家）",
            },
        },
    ],
    demo={
        "config": _example(
            send_enabled=True,
            content_format="title_link_lead",
            per_source_limit=10,
            per_destination_limit=50,
            pull_interval_minutes=120,
            cache_days=10,
            message_interval_seconds=2,
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
    commands.set_runtime(runtime)


@plugin.on_shutdown
async def _on_shutdown(ctx: Any) -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    actions.set_runtime(None)
    commands.set_runtime(None)
    if runtime is not None:
        await runtime.stop()
