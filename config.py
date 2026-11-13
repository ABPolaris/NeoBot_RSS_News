"""插件配置模型：面板表单、保存校验与运行期读取共用同一份定义。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ContentFormat = Literal["title", "title_link", "title_lead", "title_body", "title_link_lead"]

CONTENT_FORMAT_LABELS: dict[str, str] = {
    "title": "仅标题",
    "title_link": "标题和链接",
    "title_lead": "标题和开头",
    "title_body": "标题和正文",
    "title_link_lead": "标题、链接和开头",
}


class Config(BaseModel):
    """指令驱动模式下的全部用户可调项。"""

    send_enabled: bool = Field(
        default=True,
        title="允许发送新闻",
        description=(
            "关闭后相当于静音：用户请求新闻时不拉取、不发送文章；"
            "RSS 列表和昵称设置仍然可用。"
        ),
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
        description="每条订阅每次请求最多发送的文章数，默认 10 篇。",
    )
    per_destination_limit: int = Field(
        default=50,
        ge=1,
        le=500,
        title="用户接收文章最大数量",
        description=(
            "单次请求最多接收的文章总数，默认 50 篇。"
            "低于“订阅数 × 每订阅上限”时，排在后面的订阅可能长期发不出文章。"
        ),
    )
    pull_interval_minutes: int = Field(
        default=120,
        ge=15,
        le=720,
        title="两次拉取最小间隔（分钟）",
        description=(
            "仅在用户请求新闻时尝试拉取；距该源上次拉取不足此间隔时直接读取缓存。"
            "范围 15 分钟至 12 小时，默认 120 分钟。"
        ),
    )
    cache_days: int = Field(
        default=10,
        ge=7,
        le=30,
        title="缓存保留天数",
        description="文章在本地保留的天数，超期文章在下一次请求时立即删除。范围 7 至 30 天。",
    )
    message_interval_seconds: int = Field(
        default=2,
        ge=1,
        le=5,
        title="多条消息间隔（秒）",
        description="“所有RSS新闻”按订阅连续发送多条消息时的间隔，范围 1 至 5 秒。",
    )

    @property
    def pull_interval_seconds(self) -> int:
        return self.pull_interval_minutes * 60


def coerce_config(value: Any) -> Config:
    """把宿主注入的映射或已校验模型统一成 Config。"""

    if isinstance(value, Config):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return Config.model_validate(value or {})


__all__ = [
    "CONTENT_FORMAT_LABELS",
    "Config",
    "ContentFormat",
    "coerce_config",
]
