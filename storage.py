"""SQLite 存储层：订阅、文章缓存、接收者与已发送记录。

所有公开方法都在同一把异步锁内执行，避免后台抓取、发送和面板动作
在同一个连接上交错事务。时间统一使用 Unix 秒。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

from .errors import (
    DuplicateValueError,
    NotFoundError,
    SubscriptionLimitError,
)
from .models import (
    Article,
    ArticlePreview,
    Destination,
    FeedEntry,
    Subscription,
)

MAX_SUBSCRIPTIONS = 7
MIN_WEIGHT = 1
MAX_WEIGHT = 10
DELETED_RETENTION_SECONDS = 60 * 24 * 60 * 60

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        weight INTEGER NOT NULL UNIQUE,
        created_at INTEGER NOT NULL,
        pull_at INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS articles (
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
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_articles_source_pubdate
        ON articles (source_id, pubdate DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS destinations (
        destination_id INTEGER PRIMARY KEY,
        last_sent INTEGER,
        next_send INTEGER,
        enabled INTEGER NOT NULL DEFAULT 1,
        reason TEXT,
        consecutive_failed_rounds INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sent (
        destination_id INTEGER NOT NULL,
        source_id INTEGER NOT NULL,
        article_guid TEXT NOT NULL,
        failed_cnt INTEGER NOT NULL DEFAULT 0,
        last_attempt_at INTEGER,
        skipped INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (destination_id, source_id, article_guid)
    )
    """,
)


def _row_to_subscription(row: aiosqlite.Row) -> Subscription:
    keys = row.keys()
    return Subscription(
        id=int(row["id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        weight=int(row["weight"]),
        created_at=int(row["created_at"]),
        pull_at=int(row["pull_at"]) if row["pull_at"] is not None else None,
        article_count=int(row["article_count"]) if "article_count" in keys else 0,
    )


def _row_to_destination(row: aiosqlite.Row) -> Destination:
    keys = row.keys()
    return Destination(
        destination_id=int(row["destination_id"]),
        last_sent=int(row["last_sent"]) if row["last_sent"] is not None else None,
        next_send=int(row["next_send"]) if row["next_send"] is not None else None,
        enabled=bool(row["enabled"]),
        reason=str(row["reason"]) if row["reason"] else None,
        consecutive_failed_rounds=(
            int(row["consecutive_failed_rounds"]) if "consecutive_failed_rounds" in keys else 0
        ),
    )


def _row_to_article(row: aiosqlite.Row) -> Article:
    return Article(
        source_id=int(row["source_id"]),
        guid=str(row["guid"]),
        title=str(row["title"] or ""),
        link=str(row["link"] or ""),
        body=str(row["body"] or ""),
        summary=str(row["summary"] or ""),
        pubdate=int(row["pubdate"]),
    )


class RssNewsStore:
    """一个插件数据目录内的 SQLite 数据库。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._db is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        try:
            for statement in _SCHEMA:
                await self._db.execute(statement)
            await self._db.commit()
        except BaseException:
            await self._db.close()
            self._db = None
            raise

    async def close(self) -> None:
        connection, self._db = self._db, None
        if connection is not None:
            await connection.close()

    @property
    def is_open(self) -> bool:
        return self._db is not None

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("数据库尚未打开")
        return self._db

    @asynccontextmanager
    async def _session(self, *, write: bool = False):
        async with self._lock:
            db = self._require()
            try:
                yield db
                if write:
                    await db.commit()
            except BaseException:
                if write:
                    await db.rollback()
                raise

    # ------------------------------------------------------------------ 预设

    async def init_presets(self, presets: Sequence[tuple[str, str, int]], now: int) -> bool:
        """首次初始化写入预设；用户删改后不再补回。"""

        async with self._session(write=True) as db:
            cursor = await db.execute("SELECT value FROM meta WHERE key = 'presets_initialized'")
            if await cursor.fetchone() is not None:
                return False
            for name, url, weight in presets:
                await db.execute(
                    "INSERT OR IGNORE INTO subscriptions (name, url, weight, created_at, pull_at) "
                    "VALUES (?, ?, ?, ?, NULL)",
                    (name, url, weight, now),
                )
            await db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('presets_initialized', '1')"
            )
            return True

    # ------------------------------------------------------------------ 订阅

    async def list_subscriptions(self) -> list[Subscription]:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id AND a.deleted_at IS NULL) AS article_count
                FROM subscriptions s
                ORDER BY s.weight ASC
                """
            )
            return [_row_to_subscription(row) for row in await cursor.fetchall()]

    async def get_subscription(self, subscription_id: int) -> Subscription | None:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id AND a.deleted_at IS NULL) AS article_count
                FROM subscriptions s WHERE s.id = ?
                """,
                (subscription_id,),
            )
            row = await cursor.fetchone()
            return _row_to_subscription(row) if row is not None else None

    async def count_subscriptions(self) -> int:
        async with self._session() as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM subscriptions")
            row = await cursor.fetchone()
            return int(row["total"]) if row is not None else 0

    async def _used_weights(self, db: aiosqlite.Connection, exclude_id: int | None) -> set[int]:
        if exclude_id is None:
            cursor = await db.execute("SELECT weight FROM subscriptions")
        else:
            cursor = await db.execute("SELECT weight FROM subscriptions WHERE id != ?", (exclude_id,))
        return {int(row["weight"]) for row in await cursor.fetchall()}

    async def _next_weight(self, db: aiosqlite.Connection) -> int:
        used = await self._used_weights(db, None)
        for weight in range(MIN_WEIGHT, MAX_WEIGHT + 1):
            if weight not in used:
                return weight
        raise SubscriptionLimitError("权重 1~10 已全部占用，无法再添加订阅")

    async def add_subscription(
        self,
        name: str,
        url: str,
        weight: int | None,
        now: int,
    ) -> Subscription:
        clean_name = _require_text(name, "订阅名称", 100)
        clean_url = _require_text(url, "RSS 地址", 2048)
        async with self._session(write=True) as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM subscriptions")
            row = await cursor.fetchone()
            if row is not None and int(row["total"]) >= MAX_SUBSCRIPTIONS:
                raise SubscriptionLimitError(
                    f"订阅列表最多 {MAX_SUBSCRIPTIONS} 条，请先删除一条再添加"
                )
            cursor = await db.execute("SELECT id FROM subscriptions WHERE url = ?", (clean_url,))
            if await cursor.fetchone() is not None:
                raise DuplicateValueError("这个 RSS 地址已经在订阅列表中")
            used = await self._used_weights(db, None)
            chosen = await self._next_weight(db) if weight is None else weight
            if not MIN_WEIGHT <= chosen <= MAX_WEIGHT:
                raise DuplicateValueError("权重必须在 1~10 之间")
            if chosen in used:
                raise DuplicateValueError(f"权重 {chosen} 已被占用，请选择其它数值")
            cursor = await db.execute(
                "INSERT INTO subscriptions (name, url, weight, created_at, pull_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (clean_name, clean_url, chosen, now),
            )
            subscription_id = int(cursor.lastrowid or 0)
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id AND a.deleted_at IS NULL) AS article_count
                FROM subscriptions s WHERE s.id = ?
                """,
                (subscription_id,),
            )
            created = await cursor.fetchone()
            if created is None:
                raise NotFoundError("订阅创建失败")
            return _row_to_subscription(created)

    async def update_subscription(
        self,
        subscription_id: int,
        *,
        name: str | None,
        url: str | None,
        weight: int | None,
        now: int,
    ) -> tuple[Subscription, bool]:
        """返回（更新后的订阅，URL 是否变化）。

        URL 变化等价于删除后重建：清空该订阅的文章与已发送记录，
        并重置 created_at / pull_at，让新源立即重新拉取。
        """

        async with self._session(write=True) as db:
            cursor = await db.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
            existing = await cursor.fetchone()
            if existing is None:
                raise NotFoundError("订阅不存在或已被删除")
            new_name = _require_text(name, "订阅名称", 100) if name is not None else str(existing["name"])
            new_url = _require_text(url, "RSS 地址", 2048) if url is not None else str(existing["url"])
            new_weight = int(existing["weight"]) if weight is None else weight
            if not MIN_WEIGHT <= new_weight <= MAX_WEIGHT:
                raise DuplicateValueError("权重必须在 1~10 之间")
            url_changed = new_url != str(existing["url"])
            if url_changed:
                cursor = await db.execute(
                    "SELECT id FROM subscriptions WHERE url = ? AND id != ?",
                    (new_url, subscription_id),
                )
                if await cursor.fetchone() is not None:
                    raise DuplicateValueError("这个 RSS 地址已经在订阅列表中")
            if new_weight != int(existing["weight"]):
                cursor = await db.execute(
                    "SELECT id FROM subscriptions WHERE weight = ? AND id != ?",
                    (new_weight, subscription_id),
                )
                if await cursor.fetchone() is not None:
                    raise DuplicateValueError(f"权重 {new_weight} 已被占用，请选择其它数值")
            if url_changed:
                await db.execute("DELETE FROM sent WHERE source_id = ?", (subscription_id,))
                await db.execute("DELETE FROM articles WHERE source_id = ?", (subscription_id,))
                await db.execute(
                    "UPDATE subscriptions SET name = ?, url = ?, weight = ?, created_at = ?, "
                    "pull_at = NULL WHERE id = ?",
                    (new_name, new_url, new_weight, now, subscription_id),
                )
            else:
                await db.execute(
                    "UPDATE subscriptions SET name = ?, weight = ? WHERE id = ?",
                    (new_name, new_weight, subscription_id),
                )
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id AND a.deleted_at IS NULL) AS article_count
                FROM subscriptions s WHERE s.id = ?
                """,
                (subscription_id,),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise NotFoundError("订阅不存在或已被删除")
            return _row_to_subscription(updated), url_changed

    async def delete_subscription(self, subscription_id: int) -> bool:
        async with self._session(write=True) as db:
            await db.execute("DELETE FROM sent WHERE source_id = ?", (subscription_id,))
            await db.execute("DELETE FROM articles WHERE source_id = ?", (subscription_id,))
            cursor = await db.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
            return cursor.rowcount > 0

    async def list_due_subscriptions(self, now: int, interval_seconds: int) -> list[Subscription]:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id AND a.deleted_at IS NULL) AS article_count
                FROM subscriptions s
                WHERE s.pull_at IS NULL OR s.pull_at + ? <= ?
                ORDER BY s.weight ASC
                """,
                (interval_seconds, now),
            )
            return [_row_to_subscription(row) for row in await cursor.fetchall()]

    async def set_pull_at(self, subscription_id: int, timestamp: int) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                "UPDATE subscriptions SET pull_at = ? WHERE id = ?",
                (timestamp, subscription_id),
            )

    # ------------------------------------------------------------------ 文章

    async def insert_articles(
        self, source_id: int, entries: Iterable[FeedEntry], fetched_at: int
    ) -> int:
        inserted = 0
        async with self._session(write=True) as db:
            for entry in entries:
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO articles
                        (source_id, guid, title, link, body, summary, pubdate, fetched_at, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        source_id,
                        entry.guid,
                        entry.title,
                        entry.link,
                        entry.body,
                        entry.summary,
                        entry.pubdate,
                        fetched_at,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted += 1
        return inserted

    async def list_candidates(
        self,
        destination_id: int,
        source_id: int,
        after: int,
        limit: int,
    ) -> list[Article]:
        """取该订阅未成功发送、失败次数未达 3 次的最新文章。"""

        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT a.*
                FROM articles a
                LEFT JOIN sent s
                  ON s.destination_id = ?
                 AND s.source_id = a.source_id
                 AND s.article_guid = a.guid
                WHERE a.source_id = ?
                  AND a.deleted_at IS NULL
                  AND a.pubdate > ?
                  AND (s.article_guid IS NULL OR (s.failed_cnt > 0 AND s.failed_cnt < 3))
                ORDER BY a.pubdate DESC
                LIMIT ?
                """,
                (destination_id, source_id, after, limit),
            )
            return [_row_to_article(row) for row in await cursor.fetchall()]

    async def mark_skipped(self, destination_id: int) -> int:
        """把失败次数已达 3 次的记录标记为跳过，避免继续占用发送名额。"""

        async with self._session(write=True) as db:
            cursor = await db.execute(
                "UPDATE sent SET skipped = 1 "
                "WHERE destination_id = ? AND failed_cnt >= 3 AND skipped = 0",
                (destination_id,),
            )
            return max(cursor.rowcount, 0)

    async def record_sent(self, destination_id: int, article: Article, now: int) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                """
                INSERT INTO sent
                    (destination_id, source_id, article_guid, failed_cnt, last_attempt_at, skipped)
                VALUES (?, ?, ?, 0, ?, 0)
                ON CONFLICT (destination_id, source_id, article_guid)
                DO UPDATE SET failed_cnt = 0,
                              last_attempt_at = excluded.last_attempt_at,
                              skipped = 0
                """,
                (destination_id, article.source_id, article.guid, now),
            )

    async def record_failure(self, destination_id: int, article: Article, now: int) -> int:
        async with self._session(write=True) as db:
            await db.execute(
                """
                INSERT INTO sent
                    (destination_id, source_id, article_guid, failed_cnt, last_attempt_at, skipped)
                VALUES (?, ?, ?, 1, ?, 0)
                ON CONFLICT (destination_id, source_id, article_guid)
                DO UPDATE SET failed_cnt = failed_cnt + 1,
                              last_attempt_at = excluded.last_attempt_at,
                              skipped = 0
                """,
                (destination_id, article.source_id, article.guid, now),
            )
            cursor = await db.execute(
                "SELECT failed_cnt FROM sent "
                "WHERE destination_id = ? AND source_id = ? AND article_guid = ?",
                (destination_id, article.source_id, article.guid),
            )
            row = await cursor.fetchone()
            return int(row["failed_cnt"]) if row is not None else 1

    async def cleanup_articles(self, now: int, cache_days: int) -> tuple[int, int]:
        """标记超期文章并彻底删除满 60 天的记录及其已发送记录。"""

        cutoff = now - cache_days * 24 * 60 * 60
        purge_at = now + DELETED_RETENTION_SECONDS
        async with self._session(write=True) as db:
            cursor = await db.execute(
                """
                UPDATE articles
                SET title = NULL, link = NULL, body = NULL, summary = NULL, deleted_at = ?
                WHERE deleted_at IS NULL AND pubdate < ?
                """,
                (purge_at, cutoff),
            )
            marked = max(cursor.rowcount, 0)
            await db.execute(
                """
                DELETE FROM sent
                WHERE EXISTS (
                    SELECT 1 FROM articles a
                    WHERE a.source_id = sent.source_id
                      AND a.guid = sent.article_guid
                      AND a.deleted_at IS NOT NULL
                      AND a.deleted_at <= ?
                )
                """,
                (now,),
            )
            cursor = await db.execute(
                "DELETE FROM articles WHERE deleted_at IS NOT NULL AND deleted_at <= ?",
                (now,),
            )
            purged = max(cursor.rowcount, 0)
            return marked, purged

    async def random_article(self) -> tuple[Article, str] | None:
        """随机取一篇仍可用的缓存文章，只读，不改变任何记录。"""

        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT a.*, s.name AS source_name
                FROM articles a
                JOIN subscriptions s ON s.id = a.source_id
                WHERE a.deleted_at IS NULL
                  AND (
                      COALESCE(a.title, '') != ''
                      OR COALESCE(a.link, '') != ''
                      OR COALESCE(a.body, '') != ''
                  )
                ORDER BY RANDOM()
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_article(row), str(row["source_name"])

    async def list_recent_articles(self, limit: int = 30) -> list[ArticlePreview]:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT a.source_id, s.name AS source_name, a.guid, a.title, a.link,
                       a.pubdate,
                       CASE WHEN a.body IS NOT NULL AND a.body != '' THEN 1 ELSE 0 END AS has_body,
                       CASE WHEN a.deleted_at IS NOT NULL THEN 1 ELSE 0 END AS deleted
                FROM articles a
                JOIN subscriptions s ON s.id = a.source_id
                WHERE a.deleted_at IS NULL
                ORDER BY a.pubdate DESC
                LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            )
            return [
                ArticlePreview(
                    source_id=int(row["source_id"]),
                    source_name=str(row["source_name"]),
                    guid=str(row["guid"]),
                    title=str(row["title"] or ""),
                    link=str(row["link"] or ""),
                    pubdate=int(row["pubdate"]),
                    has_body=bool(row["has_body"]),
                    deleted=bool(row["deleted"]),
                )
                for row in await cursor.fetchall()
            ]

    async def article_count(self) -> int:
        async with self._session() as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM articles WHERE deleted_at IS NULL")
            row = await cursor.fetchone()
            return int(row["total"]) if row is not None else 0

    async def latest_pull_at(self) -> int | None:
        async with self._session() as db:
            cursor = await db.execute("SELECT MAX(pull_at) AS latest FROM subscriptions")
            row = await cursor.fetchone()
            if row is None or row["latest"] is None:
                return None
            return int(row["latest"])

    # ------------------------------------------------------------------ 接收者

    async def list_destinations(self) -> list[Destination]:
        async with self._session() as db:
            cursor = await db.execute("SELECT * FROM destinations ORDER BY destination_id ASC")
            return [_row_to_destination(row) for row in await cursor.fetchall()]

    async def get_destination(self, destination_id: int) -> Destination | None:
        async with self._session() as db:
            cursor = await db.execute(
                "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
            )
            row = await cursor.fetchone()
            return _row_to_destination(row) if row is not None else None

    async def add_destination(
        self, destination_id: int, next_send: int | None, now: int
    ) -> Destination:
        async with self._session(write=True) as db:
            try:
                await db.execute(
                    """
                    INSERT INTO destinations
                        (destination_id, last_sent, next_send, enabled, reason,
                         consecutive_failed_rounds)
                    VALUES (?, NULL, ?, 1, NULL, 0)
                    """,
                    (destination_id, next_send),
                )
            except sqlite3.IntegrityError:
                raise DuplicateValueError("这个 QQ 号已经在接收者列表中") from None
            cursor = await db.execute(
                "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFoundError("接收者创建失败")
            return _row_to_destination(row)

    async def set_destination_enabled(
        self, destination_id: int, enabled: bool, reason: str | None
    ) -> Destination:
        async with self._session(write=True) as db:
            cursor = await db.execute(
                "SELECT destination_id FROM destinations WHERE destination_id = ?",
                (destination_id,),
            )
            if await cursor.fetchone() is None:
                raise NotFoundError("接收者不存在或已被删除")
            await db.execute(
                "UPDATE destinations SET enabled = ?, reason = ? WHERE destination_id = ?",
                (1 if enabled else 0, reason, destination_id),
            )
            cursor = await db.execute(
                "SELECT * FROM destinations WHERE destination_id = ?", (destination_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFoundError("接收者不存在或已被删除")
            return _row_to_destination(row)

    async def set_next_send(self, destination_id: int, next_send: int) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                "UPDATE destinations SET next_send = ? WHERE destination_id = ?",
                (next_send, destination_id),
            )

    async def mark_send_started(
        self, destination_id: int, sent_at: int, next_send: int
    ) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                "UPDATE destinations SET last_sent = ?, next_send = ? WHERE destination_id = ?",
                (sent_at, next_send, destination_id),
            )

    async def set_failed_rounds(self, destination_id: int, rounds: int) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                "UPDATE destinations SET consecutive_failed_rounds = ? WHERE destination_id = ?",
                (max(0, int(rounds)), destination_id),
            )

    async def disable_destination(self, destination_id: int, reason: str) -> None:
        async with self._session(write=True) as db:
            await db.execute(
                "UPDATE destinations SET enabled = 0, reason = ? WHERE destination_id = ?",
                (reason[:500], destination_id),
            )

    async def delete_destination(self, destination_id: int) -> bool:
        async with self._session(write=True) as db:
            await db.execute("DELETE FROM sent WHERE destination_id = ?", (destination_id,))
            cursor = await db.execute(
                "DELETE FROM destinations WHERE destination_id = ?", (destination_id,)
            )
            return cursor.rowcount > 0

    async def count_destinations(self) -> int:
        async with self._session() as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM destinations")
            row = await cursor.fetchone()
            return int(row["total"]) if row is not None else 0

    async def count_sent(self) -> int:
        async with self._session() as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM sent")
            row = await cursor.fetchone()
            return int(row["total"]) if row is not None else 0


def _require_text(value: str, label: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > max_length:
        raise ValueError(f"{label}不能超过 {max_length} 个字符")
    return text


def available_weights(subscriptions: Sequence[Subscription]) -> list[int]:
    used = {subscription.weight for subscription in subscriptions}
    return [weight for weight in range(MIN_WEIGHT, MAX_WEIGHT + 1) if weight not in used]


def normalize_now(value: Any = None) -> int:
    if value is None:
        import time

        return int(time.time())
    return int(value)


__all__ = [
    "DELETED_RETENTION_SECONDS",
    "MAX_SUBSCRIPTIONS",
    "MAX_WEIGHT",
    "MIN_WEIGHT",
    "RssNewsStore",
    "available_weights",
    "normalize_now",
]
