"""插件身份与公开契约：动作、指令、配置、页面、教程均真实注册。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from NeoBot_RSS_News.config import Config


def test_plugin_identity(plugin_env: Any) -> None:
    plugin = plugin_env.plugin
    assert plugin.name == "NeoBot_RSS_News"
    assert plugin.version == "0.3.0-a1"
    assert plugin.config_model is not None
    assert plugin.config_model.__name__ == "Config"


def test_surface_contains_passive_actions_and_view(plugin_env: Any) -> None:
    document = plugin_env.plugin.surface.describe(plugin_env.plugin)
    action_ids = {item["id"] for item in document["actions"]}
    assert action_ids == {
        "subscriptions.list",
        "subscriptions.create",
        "subscriptions.update",
        "subscriptions.remove",
        "articles.list",
        "status.get",
        "system.send_test",
    }
    views = {item["id"]: item for item in document["views"]}
    assert views["management"]["entry"] == "web/index.html"
    assert views["management"]["anchors"] == [
        "status",
        "subscriptions",
        "subscription-form",
        "send-test",
        "commands",
        "articles",
    ]
    assert (Path(__file__).resolve().parents[1] / "web" / "index.html").is_file()
    assert document["guides"][0]["id"] == "first-use"
    assert document["guides"][0]["steps"]
    assert set(document["config"]["properties"]) == {
        "send_enabled",
        "content_format",
        "per_source_limit",
        "per_destination_limit",
        "pull_interval_minutes",
        "cache_days",
        "message_interval_seconds",
    }


def test_chat_commands_are_registered(plugin_env: Any) -> None:
    commands = plugin_env.commands.items
    assert set(commands) == {
        "RSS新闻",
        "所有RSS新闻",
        "RSS列表",
        "设置昵称",
    }
    assert commands["RSS新闻"].permission == 0
    assert commands["所有RSS新闻"].permission == 0
    assert commands["RSS列表"].permission == 0
    assert commands["设置昵称"].permission == 1


def test_config_model_is_current(plugin_env: Any) -> None:
    assert issubclass(plugin_env.plugin.config_model, Config) or (
        plugin_env.plugin.config_model.__name__ == Config.__name__
    )
    assert plugin_env.plugin.config_model().send_enabled is True
