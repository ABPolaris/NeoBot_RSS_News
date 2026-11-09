"""正文清洗：丢弃 HTML/图片/脚本，开头按 Unicode 字符截断。"""

from __future__ import annotations

from NeoBot_RSS_News.textutils import clean_html_text, clean_title, make_lead


def test_removes_markup_and_resources() -> None:
    raw = (
        "<div><h2>标题</h2><script>alert(1)</script>"
        "<style>.x{}</style><img src='a.png' alt='图'>"
        "<p>第一段 &amp; 内容</p><p>第二段</p></div>"
    )
    text = clean_html_text(raw)
    assert "alert" not in text
    assert "a.png" not in text
    assert "第一段 & 内容" in text
    assert "第二段" in text
    assert text.startswith("标题")


def test_br_becomes_newline() -> None:
    assert clean_html_text("<p>Hello<br>world</p>") == "Hello\nworld"


def test_plain_text_is_collapsed() -> None:
    assert clean_html_text("  a   b\n\n\nc  ") == "a b\n\nc"


def test_empty_and_none() -> None:
    assert clean_html_text(None) == ""
    assert clean_html_text("") == ""


def test_clean_title_flattens_lines() -> None:
    assert clean_title("<b>标题</b>\n  第二行 ") == "标题 第二行"


def test_lead_shorter_than_limit_is_unchanged() -> None:
    body = "字" * 200
    assert make_lead(body) == body


def test_lead_truncates_and_adds_ellipsis() -> None:
    lead = make_lead("字" * 201)
    assert len(lead) == 201
    assert lead.endswith("…")
    assert lead[:-1] == "字" * 200


def test_lead_counts_cjk_and_ascii_by_character() -> None:
    body = "中" * 100 + "a" * 100
    assert make_lead(body) == body
    assert make_lead(body + "b").endswith("…")
