"""测试夹具：真实执行插件生命周期、SQLite 与动作注册。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
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
    """只替代宿主适配器；动作、存储和调度仍是真实实现。"""

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


@dataclass
class PluginEnvironment:
    loader: FilesystemPluginLoader
    loaded: Any
    plugin: Any
    context: RuntimePluginContext
    adapter: FakeAdapter
    data_dir: Path

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


@pytest.fixture
async def plugin_env(tmp_path: Path) -> PluginEnvironment:
    loader = FilesystemPluginLoader(namespace="neobot_rss_news_pytest")
    loaded = loader.load_one(PROJECT)
    assert loaded is not None
    assert getattr(loaded, "plugin", None) is not None
    adapter = FakeAdapter()
    context = RuntimePluginContext(
        plugin_name="NeoBot_RSS_News",
        plugin_dir=PROJECT,
        data_dir=tmp_path,
        config={},
        logger=None,
        adapter=adapter,
        app_commands=None,
    )
    await loaded.plugin.on_load(context)
    environment = PluginEnvironment(
        loader=loader,
        loaded=loaded,
        plugin=loaded.plugin,
        context=context,
        adapter=adapter,
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
