"""配置模型：默认值、时间/时区校验与范围约束。"""

from __future__ import annotations

import pytest
from NeoBot_RSS_News.config import Config, coerce_config
from pydantic import ValidationError


def test_defaults_match_requirements() -> None:
    config = Config()
    assert config.send_enabled is False
    assert config.send_every_days == 1
    assert config.send_time == "18:00"
    assert config.timezone == "Asia/Shanghai"
    assert config.send_format == "per_source"
    assert config.content_format == "title_link_lead"
    assert config.per_source_limit == 10
    assert config.per_destination_limit == 50
    assert config.pull_interval_minutes == 120
    assert config.cache_days == 10
    assert config.message_interval_seconds == 2
    assert config.send_hour_minute == (18, 0)
    assert config.pull_interval_seconds == 7200
    assert config.send_window_seconds == 300


@pytest.mark.parametrize("value", ["08:00", "00:00", "23:59", " 07:05 "])
def test_valid_send_time(value: str) -> None:
    assert Config(send_time=value).send_time == value.strip()


@pytest.mark.parametrize("value", ["8:00", "24:00", "18:60", "", "18", "18:00:00", "a:b"])
def test_invalid_send_time(value: str) -> None:
    with pytest.raises(ValidationError, match="发送时间"):
        Config(send_time=value)


def test_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="时区"):
        Config(timezone="Mars/Olympus")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("send_every_days", 0),
        ("send_every_days", 8),
        ("pull_interval_minutes", 14),
        ("pull_interval_minutes", 721),
        ("cache_days", 6),
        ("cache_days", 31),
        ("per_source_limit", 0),
        ("per_source_limit", 101),
        ("per_destination_limit", 0),
        ("per_destination_limit", 501),
        ("message_interval_seconds", 0),
        ("message_interval_seconds", 6),
    ],
)
def test_range_constraints(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Config(**{field: value})


def test_coerce_config_from_mapping_and_model() -> None:
    config = Config()
    assert coerce_config(None) == config
    assert coerce_config({"send_time": "09:30"}).send_time == "09:30"
    assert coerce_config(config.model_dump()).send_time == "18:00"
