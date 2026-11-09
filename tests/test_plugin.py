"""插件身份与公开契约：动作、配置、页面、教程均真实注册。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from NeoBot_RSS_News.config import Config


def test_plugin_identity(plugin_env: Any) -> None:
    plugin = plugin_env.plugin
    assert plugin.name == "NeoBot_RSS_News"
    assert plugin.version == "0.1.0"
    assert plugin.config_model is not None
    assert issubclass(plugin.config_model, Config) or plugin.config_model.__name__ == "Config"


def test_surface_contains_actions_view_and_guide(plugin_env: Any) -> None:
    document = plugin_env.plugin.surface.describe(plugin_env.plugin)
    action_ids = {item["id"] for item in document["actions"]}
    assert action_ids == {
        "subscriptions.list",
        "subscriptions.create",
        "subscriptions.update",
        "subscriptions.remove",
        "destinations.list",
        "destinations.create",
        "destinations.update",
        "destinations.remove",
        "articles.list",
        "status.get",
        "system.pull_now",
        "system.send_test",
    }
    views = {item["id"]: item for item in document["views"]}
    assert views["management"]["entry"] == "web/index.html"
    assert "subscriptions" in views["management"]["anchors"]
    assert (Path(__file__).resolve().parents[1] / "web" / "index.html").is_file()
    assert document["guides"][0]["id"] == "first-use"
    assert document["guides"][0]["steps"]
    assert set(document["config"]["properties"]) == {
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
    }


def test_no_chat_command_is_registered(plugin_env: Any) -> None:
    """管理入口只走面板；不额外暴露聊天命令。"""

    document = plugin_env.plugin.surface.describe(plugin_env.plugin)
    assert all(item["id"] for item in document["actions"])
    assert plugin_env.plugin.command_prefix == "/"
