"""消息组装：内容选项、同订阅文章合并、3950 字与空行规则。"""

from __future__ import annotations

from NeoBot_RSS_News.composer import (
    MAX_MESSAGE_CHARS,
    pack_messages,
    render_article,
    starvation_warning,
)
from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.models import Article


def article(
    guid: str = "g",
    *,
    title: str = "标题",
    link: str = "https://example.com/a",
    body: str = "正文",
    summary: str = "开头",
) -> Article:
    return Article(
        source_id=1,
        guid=guid,
        title=title,
        link=link,
        body=body,
        summary=summary,
        pubdate=1,
    )


def test_content_formats() -> None:
    item = article()
    assert render_article(item, "title") == "标题"
    assert render_article(item, "title_link") == "标题\nhttps://example.com/a"
    assert render_article(item, "title_lead") == "标题\n开头"
    assert render_article(item, "title_body") == "标题\n正文"
    assert render_article(item, "title_link_lead") == "标题\nhttps://example.com/a\n开头"


def test_content_formats_degrade_without_link_or_body() -> None:
    item = article(title="只有标题", link="", body="", summary="")
    for content_format in (
        "title",
        "title_link",
        "title_lead",
        "title_body",
        "title_link_lead",
    ):
        assert render_article(item, content_format) == "只有标题"


def test_pack_joins_with_blank_line() -> None:
    first = article("a", title="A", link="", body="", summary="")
    second = article("b", title="B", link="", body="", summary="")
    messages = pack_messages([first, second], "title")
    assert len(messages) == 1
    assert messages[0].text == "A\n\nB"
    assert [item.guid for item in messages[0].articles] == ["a", "b"]


def test_long_article_is_truncated_to_limit() -> None:
    item = article(body="正" * 5000)
    messages = pack_messages([item], "title_body")
    assert len(messages[0].text) == MAX_MESSAGE_CHARS


def test_pack_respects_limit_and_keeps_article_refs() -> None:
    items = [article(str(index), body="正" * 2000) for index in range(3)]
    messages = pack_messages(items, "title_body")
    assert len(messages) == 3
    assert all(len(message.text) <= MAX_MESSAGE_CHARS for message in messages)
    assert all(len(message.articles) == 1 for message in messages)


def test_exact_limit_stays_single_message() -> None:
    title = "T"
    body = "正" * (MAX_MESSAGE_CHARS - len(title) - 1)
    messages = pack_messages([article(title=title, body=body)], "title_body")
    assert len(messages) == 1
    assert len(messages[0].text) == MAX_MESSAGE_CHARS


def test_starvation_warning() -> None:
    config = Config(per_source_limit=10, per_destination_limit=50)
    warning = starvation_warning(config, 6)
    assert warning is not None
    assert "60" in warning and "50" in warning
    assert starvation_warning(config, 5) is None
    assert starvation_warning(Config(), 0) is None
