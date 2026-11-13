"""测试夹具：真实执行插件生命周期、SQLite、动作与指令注册。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from neobot_modloader import ActionInvocation
from neobot_modloader.context import RuntimePluginContext
from neobot_modloader.loader import FilesystemPluginLoader

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from NeoBot_RSS_News.storage import RssNewsStore


class FakeAdapter:
    """只替代宿主适配器；动作、存储和指令仍是真实实现。"""

    def __init__(self) -> None:
        self.messages: list[tuple[int, Any]] = []
        self.fail = False

    async def send_private_msg(self, user_id: int, message: Any, **_kwargs: Any) -> Any:
        if self.fail:
            raise RuntimeError("synthetic transport down")
        self.messages.append((int(user_id), message))
        return {"status": "ok", "retcode": 0}

    async def send(self, conversation: Any, message: Any, **_kwargs: Any) -> Any:
        return {"status": "ok", "retcode": 0}


class FakeCommands:
    """只替代宿主命令表，保留真实 DSL 解析和权限声明。"""

    available = True

    def __init__(self) -> None:
        self.items: dict[str, Any] = {}

    def register(self, command: Any) -> None:
        if command.name in self.items:
            raise ValueError("命令已注册")
        self.items[command.name] = command

    def unregister(self, name: str) -> bool:
        return self.items.pop(name, None) is not None

    def unregister_all(self) -> None:
        self.items.clear()


@dataclass
class FakeCommandContext:
    kind: str
    user_id: int
    conv_id: str
    command: Any
    raw_args: str
    message: dict[str, Any]
    replies: list[str] = field(default_factory=list)

    async def reply(self, text: str) -> None:
        self.replies.append(text)

    async def reply_plain(self, text: str) -> None:
        self.replies.append(text)


@dataclass
class PluginEnvironment:
    loader: FilesystemPluginLoader
    loaded: Any
    plugin: Any
    context: RuntimePluginContext
    adapter: FakeAdapter
    commands: FakeCommands
    data_dir: Path

    @property
    def runtime(self) -> Any:
        return sys.modules[self.loaded.module_names[0]]._runtime

    async def invoke(
        self,
        action_id: str,
        payload: dict[str, Any] | None = None,
        permissions: tuple[str, ...] = ("read", "manage", "operate"),
    ) -> Any:
        invocation = ActionInvocation(
            identity="pytest", permissions=frozenset(permissions)
        )
        return await self.plugin.surface.invoke(
            self.plugin, action_id, payload or {}, invocation
        )

    async def run_command(
        self,
        name: str,
        raw_args: str = "",
        *,
        kind: str = "private",
        user_id: int = 10001,
        conv_id: str | None = None,
    ) -> FakeCommandContext:
        command = self.commands.items[name]
        conversation = conv_id or str(user_id)
        event: dict[str, Any] = {
            "post_type": "message",
            "message_type": kind,
            "user_id": user_id,
            "message": f"/{command.name} {raw_args}".strip(),
            "raw_message": f"/{command.name} {raw_args}".strip(),
        }
        if kind == "group":
            event["group_id"] = int(conversation)
        context = FakeCommandContext(
            kind=kind,
            user_id=user_id,
            conv_id=conversation,
            command=command,
            raw_args=raw_args,
            message=event,
        )
        await command.handler(context)
        return context


@pytest.fixture
async def plugin_env(tmp_path: Path) -> PluginEnvironment:
    loader = FilesystemPluginLoader(namespace="neobot_rss_news_pytest")
    loaded = loader.load_one(PROJECT)
    assert loaded is not None
    assert getattr(loaded, "plugin", None) is not None
    adapter = FakeAdapter()
    commands = FakeCommands()
    context = RuntimePluginContext(
        plugin_name="NeoBot_RSS_News",
        plugin_dir=PROJECT,
        data_dir=tmp_path,
        config={},
        logger=None,
        adapter=adapter,
        app_commands=commands,
    )
    await loaded.plugin.on_load(context)
    environment = PluginEnvironment(
        loader=loader,
        loaded=loaded,
        plugin=loaded.plugin,
        context=context,
        adapter=adapter,
        commands=commands,
        data_dir=tmp_path,
    )
    try:
        yield environment
    finally:
        await loaded.plugin.on_stop()
        loader.clear_module_cache(loaded.module_names)


@pytest.fixture
async def store(tmp_path: Path) -> RssNewsStore:
    instance = RssNewsStore(tmp_path / "rss_news.db")
    await instance.open()
    try:
        yield instance
    finally:
        await instance.close()
