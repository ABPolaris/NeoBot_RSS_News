"""插件配置模型：面板表单、保存校验与运行期读取共用同一份定义。"""

from __future__ import annotations

import re
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

#: 发送时间必须是 24 小时制的 HH:MM。
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

SendFormat = Literal["merged", "per_source", "per_article"]
ContentFormat = Literal["title", "title_link", "title_lead", "title_body", "title_link_lead"]
Timezone = str

SEND_FORMAT_LABELS: dict[str, str] = {
    "merged": "消息-尽可能合并发送",
    "per_source": "消息-分订阅发送",
    "per_article": "消息-分条发送",
}

CONTENT_FORMAT_LABELS: dict[str, str] = {
    "title": "仅标题",
    "title_link": "标题和链接",
    "title_lead": "标题和开头",
    "title_body": "标题和正文",
    "title_link_lead": "标题、链接和开头",
}


class Config(BaseModel):
    """所有用户可调项。

    说明文字会出现在面板表单中；范围约束由宿主在保存前校验，
    运行期还会用同一模型再校验一次已存配置。
    """

    send_enabled: bool = Field(
        default=False,
        title="启用定期发送",
        description="默认为不发送。开启后按发送周期、发送时间和时区向接收者发送文章。",
    )
    send_every_days: int = Field(
        default=1,
        ge=1,
        le=7,
        title="发送周期（天）",
        description="每若干天发送一次，最长 7 天。例如填 1 表示每天，填 3 表示每三天。",
    )
    send_time: str = Field(
        default="18:00",
        title="发送时间",
        description="24 小时制 HH:MM，例如 08:00、18:30。发送窗口为该时间起 5 分钟。",
        json_schema_extra={"single_line": True, "placeholder": "18:00"},
    )
    timezone: Timezone = Field(
        default="Asia/Shanghai",
        title="发送时区",
        description="IANA 时区名，默认 Asia/Shanghai，例如 Europe/London。",
        json_schema_extra={"single_line": True, "placeholder": "Asia/Shanghai"},
    )
    send_format: SendFormat = Field(
        default="per_source",
        title="发送格式",
        description="决定一次发送里文章如何合并成消息；单条消息最多 3950 字。",
        json_schema_extra={"option_labels": SEND_FORMAT_LABELS},
    )
    content_format: ContentFormat = Field(
        default="title_link_lead",
        title="发送内容",
        description="每篇文章携带的内容。若源文章没有正文，涉及开头和正文的选项会退化为仅标题。",
        json_schema_extra={"option_labels": CONTENT_FORMAT_LABELS},
    )
    per_source_limit: int = Field(
        default=10,
        ge=1,
        le=100,
        title="每订阅发送文章数",
        description="每条订阅每轮最多发送的文章数，默认 10 篇。订阅数 × 本值超过每接收者总数时，后面的订阅可能长期发不出文章。",
    )
    per_destination_limit: int = Field(
        default=50,
        ge=1,
        le=500,
        title="每接收者文章总数",
        description="每个接收者每轮最多接收的文章总数，默认 50 篇。低于订阅数 × 每订阅上限时会提示饿死风险。",
    )
    pull_interval_minutes: int = Field(
        default=120,
        ge=15,
        le=720,
        title="拉取间隔（分钟）",
        description="定时拉取 RSS 的间隔，范围 15 分钟至 12 小时，默认 120 分钟。",
    )
    cache_days: int = Field(
        default=10,
        ge=7,
        le=30,
        title="缓存保留天数",
        description="文章在本地保留的天数，范围 7 至 30 天，默认 10 天。",
    )
    message_interval_seconds: int = Field(
        default=2,
        ge=1,
        le=5,
        title="多条消息间隔（秒）",
        description="连续发送多条消息时的间隔，范围 1 至 5 秒，默认 2 秒。",
    )

    @field_validator("send_time")
    @classmethod
    def _validate_send_time(cls, value: str) -> str:
        text = value.strip()
        if not TIME_PATTERN.fullmatch(text):
            raise ValueError("发送时间必须是 24 小时制的 HH:MM")
        return text

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        text = value.strip()
        try:
            ZoneInfo(text)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("时区名无效，请填写 IANA 时区，例如 Asia/Shanghai") from None
        return text

    @property
    def send_hour_minute(self) -> tuple[int, int]:
        hour, minute = self.send_time.split(":")
        return int(hour), int(minute)

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def pull_interval_seconds(self) -> int:
        return self.pull_interval_minutes * 60

    @property
    def send_window_seconds(self) -> int:
        return 5 * 60


def coerce_config(value: Any) -> Config:
    """把宿主注入的映射或已校验模型统一成 Config。"""

    if isinstance(value, Config):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return Config.model_validate(value or {})


__all__ = [
    "CONTENT_FORMAT_LABELS",
    "SEND_FORMAT_LABELS",
    "TIME_PATTERN",
    "Config",
    "ContentFormat",
    "SendFormat",
    "coerce_config",
]
