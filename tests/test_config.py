"""配置模型：指令驱动默认值、范围与静音开关。"""

from __future__ import annotations

import pytest
from NeoBot_RSS_News.config import Config, coerce_config
from pydantic import ValidationError


def test_defaults() -> None:
    config = Config()
    assert config.send_enabled is True
    assert config.content_format == "title_link_lead"
    assert config.per_source_limit == 10
    assert config.per_destination_limit == 50
    assert config.pull_interval_minutes == 120
    assert config.pull_interval_seconds == 7200
    assert config.cache_days == 10
    assert config.message_interval_seconds == 2


def test_removed_timer_fields_are_ignored_for_upgrade() -> None:
    # 旧 config.toml 可能仍带定时字段；忽略它们，避免升级后插件无法加载。
    config = Config(
        send_every_days=3,
        send_time="18:00",
        timezone="Asia/Shanghai",
        send_format="per_source",
    )
    assert config.send_enabled is True
    assert not hasattr(config, "send_time")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("per_source_limit", 0),
        ("per_source_limit", 101),
        ("per_destination_limit", 0),
        ("per_destination_limit", 501),
        ("pull_interval_minutes", 14),
        ("pull_interval_minutes", 721),
        ("cache_days", 6),
        ("cache_days", 31),
        ("message_interval_seconds", 0),
        ("message_interval_seconds", 6),
    ],
)
def test_range_constraints(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Config(**{field: value})


def test_coerce_config() -> None:
    config = Config(send_enabled=False)
    assert coerce_config(None) == Config()
    assert coerce_config({"content_format": "title"}).content_format == "title"
    assert coerce_config(config.model_dump()).send_enabled is False
