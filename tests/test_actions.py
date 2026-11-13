"""面板动作：订阅/昵称 CRUD、状态与只读发送测试。"""

from __future__ import annotations

import time
from typing import Any

import pytest
from neobot_modloader import ActionError
from NeoBot_RSS_News.models import FeedEntry
from NeoBot_RSS_News.storage import RssNewsStore


async def _seed_article(
    plugin_env: Any,
    *,
    guid: str = "test-guid",
    title: str = "测试标题",
    body: str = "测试正文",
    summary: str = "测试开头",
) -> None:
    store = RssNewsStore(plugin_env.data_dir / "databases" / "rss_news.db")
    await store.open()
    try:
        subscriptions = await store.list_subscriptions()
        now = int(time.time())
        await store.insert_articles(
            subscriptions[0].id,
            [
                FeedEntry(
                    guid,
                    title,
                    "https://example.com/test",
                    body,
                    summary,
                    now,
                )
            ],
            now,
        )
    finally:
        await store.close()


async def _snapshot(plugin_env: Any) -> dict[str, int]:
    store = RssNewsStore(plugin_env.data_dir / "databases" / "rss_news.db")
    await store.open()
    try:
        return {
            "subscriptions": await store.count_subscriptions(),
            "articles": await store.article_count(),
        }
    finally:
        await store.close()


async def test_lifecycle_sets_up_presets(plugin_env: Any) -> None:
    status = await plugin_env.invoke("status.get")
    assert status["subscription_count"] == 2
    assert status["send_enabled"] is True
    assert status["supports_send_test"] is True
    assert (plugin_env.data_dir / "databases" / "rss_news.db").is_file()
    listed = await plugin_env.invoke("subscriptions.list")
    assert [(item["name"], item["nickname"]) for item in listed["items"]] == [
        ("中新网要闻导读", "中新网"),
        ("IT之家", "之家"),
    ]


async def test_subscription_crud_with_nickname(plugin_env: Any) -> None:
    listed = await plugin_env.invoke("subscriptions.list")
    assert listed["max_count"] == 7
    assert listed["available_weights"] == [3, 4, 5, 6, 7, 8, 9, 10]

    created = await plugin_env.invoke(
        "subscriptions.create",
        {
            "name": "Example",
            "nickname": "示例",
            "url": "https://93.184.216.34/rss",
        },
    )
    item = created["item"]
    assert item["weight"] == 3
    assert item["nickname"] == "示例"

    updated = await plugin_env.invoke(
        "subscriptions.update",
        {"id": item["id"], "name": "Example 2", "nickname": "新示例", "weight": 5},
    )
    assert updated["item"]["name"] == "Example 2"
    assert updated["item"]["nickname"] == "新示例"
    assert updated["url_changed"] is False

    cleared = await plugin_env.invoke(
        "subscriptions.update", {"id": item["id"], "nickname": ""}
    )
    assert cleared["item"]["nickname"] == ""

    moved = await plugin_env.invoke(
        "subscriptions.update",
        {"id": item["id"], "url": "https://93.184.216.35/rss"},
    )
    assert moved["url_changed"] is True
    assert moved["item"]["url"] == "https://93.184.216.35/rss"

    removed = await plugin_env.invoke("subscriptions.remove", {"id": item["id"]})
    assert removed["removed"] is True
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke("subscriptions.remove", {"id": item["id"]})
    assert info.value.code == "not_found"


async def test_subscription_limit_and_warning(plugin_env: Any) -> None:
    for index in range(5):
        response = await plugin_env.invoke(
            "subscriptions.create",
            {
                "name": f"S{index}",
                "nickname": f"简{index}",
                "url": f"https://93.184.216.{40 + index}/rss",
            },
        )
        assert response["item"]["weight"] == 3 + index
    status = await plugin_env.invoke("status.get")
    assert status["subscription_count"] == 7
    assert status["warning"]
    assert "70" in status["warning"] and "50" in status["warning"]

    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "subscriptions.create",
            {"name": "overflow", "url": "https://93.184.216.50/rss"},
        )
    assert info.value.code == "limit_reached"


async def test_duplicate_nickname_and_url(plugin_env: Any) -> None:
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "subscriptions.create",
            {"name": "重复简称", "nickname": "中新网", "url": "https://93.184.216.36/rss"},
        )
    assert info.value.code == "duplicate"

    created = await plugin_env.invoke(
        "subscriptions.create",
        {"name": "地址测试", "nickname": "地址简称", "url": "https://93.184.216.36/rss"},
    )
    assert created["item"]["url"] == "https://93.184.216.36/rss"
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "subscriptions.create",
            {
                "name": "重复地址",
                "nickname": "另一个简称",
                "url": "https://93.184.216.36/rss",
            },
        )
    assert info.value.code == "duplicate"


async def test_unsafe_url(plugin_env: Any) -> None:
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "subscriptions.create",
            {"name": "Local", "url": "http://127.0.0.1/rss"},
        )
    assert info.value.code == "unsafe_url"


async def test_write_actions_require_manage(plugin_env: Any) -> None:
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "subscriptions.create",
            {"name": "Nope", "url": "https://93.184.216.34/rss"},
            permissions=("read",),
        )
    assert info.value.code == "permission_denied"

    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "system.send_test",
            {"qq": "123456789", "content_format": "title"},
            permissions=("read",),
        )
    assert info.value.code == "permission_denied"


async def test_empty_article_list(plugin_env: Any) -> None:
    result = await plugin_env.invoke("articles.list", {"limit": 20})
    assert result["items"] == []


async def test_send_test_reads_cache_without_writing(plugin_env: Any) -> None:
    await _seed_article(plugin_env)
    before = await _snapshot(plugin_env)
    status_before = await plugin_env.invoke("status.get")

    result = await plugin_env.invoke(
        "system.send_test",
        {"qq": "123456789", "content_format": "title"},
    )
    assert result["sent"] is True
    assert result["destination_id"] == 123456789
    assert result["source_name"] == "中新网要闻导读"
    assert result["title"] == "测试标题"
    assert result["message_chars"] == len("测试标题")
    assert plugin_env.adapter.messages == [(123456789, "测试标题")]

    result = await plugin_env.invoke(
        "system.send_test",
        {"qq": "987654321", "content_format": "title_link_lead"},
    )
    assert len(plugin_env.adapter.messages) == 2
    _, message = plugin_env.adapter.messages[-1]
    assert "测试标题" in message
    assert "https://example.com/test" in message
    assert "测试开头" in message

    assert await _snapshot(plugin_env) == before
    status_after = await plugin_env.invoke("status.get")
    assert status_after["article_count"] == status_before["article_count"]
    assert status_after["content_format"] == status_before["content_format"]
    assert plugin_env.context.config == {}


async def test_send_test_without_cached_article(plugin_env: Any) -> None:
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "system.send_test",
            {"qq": "123456789", "content_format": "title"},
        )
    assert info.value.code == "no_articles"
    assert plugin_env.adapter.messages == []


async def test_send_test_validates_qq(plugin_env: Any) -> None:
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "system.send_test",
            {"qq": "012345", "content_format": "title"},
        )
    assert info.value.code == "invalid_input"
    assert plugin_env.adapter.messages == []


async def test_send_test_failure_is_reported_without_writes(plugin_env: Any) -> None:
    await _seed_article(plugin_env)
    before = await _snapshot(plugin_env)
    plugin_env.adapter.fail = True
    with pytest.raises(ActionError) as info:
        await plugin_env.invoke(
            "system.send_test",
            {"qq": "123456789", "content_format": "title"},
        )
    assert info.value.code == "send_failed"
    assert await _snapshot(plugin_env) == before
