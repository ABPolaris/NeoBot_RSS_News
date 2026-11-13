"""把同一订阅的文章组装成待发送消息。

规则：
- 单条消息最多 3950 个 Unicode 字符；
- 同一订阅的文章之间空一行，超出上限立即切分；
- 文章自身超过上限时截断。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .config import Config, ContentFormat
from .models import Article

MAX_MESSAGE_CHARS = 3950


@dataclass(frozen=True, slots=True)
class MessageBundle:
    """一条待发送消息及其包含的文章。"""

    text: str
    articles: tuple[Article, ...]


def render_article(article: Article, content_format: ContentFormat) -> str:
    title = article.display_title
    link = article.link.strip()
    lead = article.summary.strip()
    body = article.body.strip()
    if content_format == "title":
        return title
    if content_format == "title_link":
        return f"{title}\n{link}" if link else title
    if content_format == "title_lead":
        return f"{title}\n{lead}" if lead else title
    if content_format == "title_body":
        return f"{title}\n{body}" if body else title
    parts = [title]
    if link:
        parts.append(link)
    if lead:
        parts.append(lead)
    return "\n".join(parts)


def pack_messages(
    articles: Sequence[Article],
    content_format: ContentFormat,
    *,
    limit: int = MAX_MESSAGE_CHARS,
    prefix: str = "",
) -> list[MessageBundle]:
    """按文章顺序尽量合并，单条消息不超过 limit。

    ``prefix`` 会加在每条消息最前面（例如“源名称（源简称）：\n”），
    并占用该条消息的 3950 字额度。
    """

    header = prefix.strip()
    header_line = f"{header}\n" if header else ""
    if len(header_line) >= limit:
        header_line = header_line[: max(0, limit - 1)]
    content_limit = max(1, limit - len(header_line))

    def bundle(texts: list[str], included: list[Article]) -> MessageBundle:
        return MessageBundle(header_line + "\n\n".join(texts), tuple(included))

    bundles: list[MessageBundle] = []
    texts: list[str] = []
    included: list[Article] = []
    current_length = 0
    for article in articles:
        text = render_article(article, content_format)[:content_limit]
        if not text:
            continue
        addition = len(text) + (2 if texts else 0)
        if texts and current_length + addition > content_limit:
            bundles.append(bundle(texts, included))
            texts = []
            included = []
            current_length = 0
        texts.append(text)
        included.append(article)
        current_length += len(text) + (2 if len(texts) > 1 else 0)
    if texts:
        bundles.append(bundle(texts, included))
    return bundles


def starvation_warning(config: Config, subscription_count: int) -> str | None:
    """订阅数 × 每订阅上限超过用户接收总数时，提示饿死风险。"""

    capacity = subscription_count * config.per_source_limit
    if subscription_count <= 0 or capacity <= config.per_destination_limit:
        return None
    return (
        f"当前 {subscription_count} 个订阅 × 每订阅 {config.per_source_limit} 篇 = "
        f"{capacity} 篇，超过单次请求最多接收 {config.per_destination_limit} 篇的上限；"
        "排在后面的订阅可能长期发不出文章。建议提高“用户接收文章最大数量”或降低“每订阅发送文章数”。"
    )


__all__ = [
    "MAX_MESSAGE_CHARS",
    "MessageBundle",
    "pack_messages",
    "render_article",
    "starvation_warning",
]
