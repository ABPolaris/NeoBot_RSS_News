"""把 RSS 正文清洗为纯文本，并固化 200 字开头。

正文只用 lxml 保留文字：脚本、样式、图片、视频等资源全部丢弃；
块级元素与换行在文本中保留为空行，发送时再按格式拼接。
"""

from __future__ import annotations

import re
from html import unescape as html_unescape

from lxml import etree, html

LEAD_LIMIT = 200

_REMOVE_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    "object",
    "embed",
    "svg",
    "img",
    "picture",
    "video",
    "audio",
    "source",
    "track",
    "canvas",
    "map",
    "form",
    "input",
    "button",
)
_REMOVE_XPATH = "|".join(f".//{tag}" for tag in _REMOVE_TAGS)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags_fallback(raw: str) -> str:
    text = _TAG_RE.sub("\n", raw)
    return _collapse(html_unescape(text))


def _collapse(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_html_text(raw: str | None) -> str:
    """返回不含 HTML 标签的正文；解析失败时退化为正则去标签。"""

    if not raw:
        return ""
    if "<" not in raw and "&" not in raw:
        return _collapse(raw)
    try:
        document = html.fromstring(
            f"<div>{raw}</div>",
            parser=html.HTMLParser(recover=True, encoding="utf-8"),
        )
    except (etree.ParserError, ValueError, UnicodeError):
        return _strip_tags_fallback(raw)
    if document is None:
        return ""
    for node in document.xpath(_REMOVE_XPATH):
        node.drop_tree()
    for comment in document.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    for node in document.iter():
        if not isinstance(node.tag, str):
            continue
        if node.tag in _BLOCK_TAGS:
            node.tail = "\n" + (node.tail or "")
    return _collapse(document.text_content())


def clean_title(raw: str | None) -> str:
    """标题压成单行，避免标题里的 HTML 或换行破坏消息排版。"""

    return re.sub(r"\s+", " ", clean_html_text(raw)).strip()


def make_lead(body: str, limit: int = LEAD_LIMIT) -> str:
    """截取正文开头；超过 limit 个 Unicode 字符时追加省略号。"""

    flat = re.sub(r"\s+", " ", body).strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


__all__ = ["LEAD_LIMIT", "clean_html_text", "clean_title", "make_lead"]
