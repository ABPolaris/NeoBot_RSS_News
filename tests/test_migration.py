"""旧版本数据库迁移：删接收者/已发送表、去 deleted_at、加 nickname。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from NeoBot_RSS_News.presets import PRESETS
from NeoBot_RSS_News.storage import SCHEMA_VERSION, RssNewsStore

NOW = 1_800_000_000


def create_old_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta (key, value) VALUES ('presets_initialized', '1');

            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                weight INTEGER NOT NULL UNIQUE,
                created_at INTEGER NOT NULL,
                pull_at INTEGER
            );
            CREATE TABLE articles (
                source_id INTEGER NOT NULL,
                guid TEXT NOT NULL,
                title TEXT,
                link TEXT,
                body TEXT,
                summary TEXT,
                pubdate INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL,
                deleted_at INTEGER,
                PRIMARY KEY (source_id, guid)
            );
            CREATE INDEX idx_articles_source_pubdate
                ON articles (source_id, pubdate DESC);
            CREATE TABLE destinations (
                destination_id INTEGER PRIMARY KEY,
                last_sent INTEGER,
                next_send INTEGER,
                enabled INTEGER NOT NULL DEFAULT 1,
                reason TEXT,
                consecutive_failed_rounds INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE sent (
                destination_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                article_guid TEXT NOT NULL,
                failed_cnt INTEGER NOT NULL DEFAULT 0,
                last_attempt_at INTEGER,
                skipped INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (destination_id, source_id, article_guid)
            );
            """
        )
        for index, (_name, url, weight, _nickname) in enumerate(PRESETS, start=1):
            connection.execute(
                "INSERT INTO subscriptions "
                "(name, url, weight, created_at, pull_at) VALUES (?, ?, ?, ?, ?)",
                (_name, url, weight, NOW, NOW),
            )
        connection.execute(
            "INSERT INTO subscriptions "
            "(name, url, weight, created_at, pull_at) VALUES (?, ?, ?, ?, ?)",
            ("自定义", "https://custom.example/rss", 3, NOW, None),
        )
        connection.execute(
            "INSERT INTO articles VALUES (1, 'active', '保留', '', '', '', ?, ?, NULL)",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO articles VALUES (1, 'deleted', '删除', '', '', '', ?, ?, ?)",
            (NOW, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO destinations (destination_id, enabled) VALUES (10001, 1)"
        )
        connection.execute(
            "INSERT INTO sent VALUES (10001, 1, 'active', 0, ?, 0)", (NOW,)
        )
        connection.commit()
    finally:
        connection.close()


def table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()


def table_columns(path: Path, table: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    finally:
        connection.close()


async def test_old_schema_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "rss_news.db"
    create_old_database(path)
    store = RssNewsStore(path)
    await store.open()
    try:
        subscriptions = await store.list_subscriptions()
        assert [(item.name, item.nickname, item.weight) for item in subscriptions] == [
            ("中新网要闻导读", "中新网", 1),
            ("IT之家", "之家", 2),
            ("自定义", "", 3),
        ]
        assert [item.guid for item in await store.recent_articles(1, 10)] == [
            "active"
        ]
        assert await store.init_presets(PRESETS, NOW) is False
    finally:
        await store.close()

    names = table_names(path)
    assert "destinations" not in names
    assert "sent" not in names
    assert "articles" in names
    assert "deleted_at" not in table_columns(path, "articles")
    assert "nickname" in table_columns(path, "subscriptions")

    connection = sqlite3.connect(path)
    try:
        version = connection.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        assert version == (str(SCHEMA_VERSION),)
    finally:
        connection.close()


async def test_cleared_nickname_is_not_restored_on_reopen(tmp_path: Path) -> None:
    path = tmp_path / "rss_news.db"
    create_old_database(path)
    store = RssNewsStore(path)
    await store.open()
    try:
        preset = (await store.list_subscriptions())[0]
        assert preset.nickname == "中新网"
        await store.set_nickname(preset.id, None)
    finally:
        await store.close()

    reopened = RssNewsStore(path)
    await reopened.open()
    try:
        preset = (await reopened.list_subscriptions())[0]
        assert preset.nickname == ""
    finally:
        await reopened.close()

