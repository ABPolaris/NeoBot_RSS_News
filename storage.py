"""SQLite 存储层：订阅、文章缓存与旧版本迁移。

被动发送模式不再需要接收者表和已发送表；旧版本的 ``destinations`` /
``sent`` 会在打开数据库时删除。文章表不再保留 ``deleted_at``，超期文章
直接删除。
"""

from __future__ import annotations

import asyncio
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
    FeedEntry,
    Subscription,
)
from .presets import PRESETS

MAX_SUBSCRIPTIONS = 7
MIN_WEIGHT = 1
MAX_WEIGHT = 10
MAX_NICKNAME_LENGTH = 64
SCHEMA_VERSION = 2

_META_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_SUBSCRIPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    nickname TEXT,
    weight INTEGER NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    pull_at INTEGER
)
"""

_NICKNAME_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_nickname
    ON subscriptions (nickname)
    WHERE nickname IS NOT NULL AND nickname <> ''
"""

_ARTICLES_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    source_id INTEGER NOT NULL,
    guid TEXT NOT NULL,
    title TEXT,
    link TEXT,
    body TEXT,
    summary TEXT,
    pubdate INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (source_id, guid)
)
"""

_ARTICLES_NEW_SQL = """
CREATE TABLE IF NOT EXISTS articles_new (
    source_id INTEGER NOT NULL,
    guid TEXT NOT NULL,
    title TEXT,
    link TEXT,
    body TEXT,
    summary TEXT,
    pubdate INTEGER NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (source_id, guid)
)
"""

_ARTICLE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_articles_source_pubdate
    ON articles (source_id, pubdate DESC)
"""


def _row_to_subscription(row: aiosqlite.Row) -> Subscription:
    keys = row.keys()
    return Subscription(
        id=int(row["id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        nickname=str(row["nickname"] or ""),
        weight=int(row["weight"]),
        created_at=int(row["created_at"]),
        pull_at=int(row["pull_at"]) if row["pull_at"] is not None else None,
        article_count=int(row["article_count"]) if "article_count" in keys else 0,
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


def _normalize_nickname(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) > MAX_NICKNAME_LENGTH:
        raise ValueError(f"昵称不能超过 {MAX_NICKNAME_LENGTH} 个字符")
    return text


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
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode=WAL")
        await connection.execute("PRAGMA foreign_keys=ON")
        await connection.execute("PRAGMA busy_timeout=5000")
        await connection.execute("PRAGMA synchronous=NORMAL")
        self._db = connection
        try:
            await self._migrate()
        except BaseException:
            await connection.close()
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

    # ------------------------------------------------------------------ 迁移

    async def _tables(self, db: aiosqlite.Connection) -> set[str]:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        return {str(row["name"]) for row in await cursor.fetchall()}

    async def _columns(self, db: aiosqlite.Connection, table: str) -> set[str]:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        return {str(row["name"]) for row in await cursor.fetchall()}

    async def _schema_version(self, db: aiosqlite.Connection) -> int:
        cursor = await db.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = await cursor.fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    async def _migrate(self) -> None:
        db = self._require()
        await db.execute(_META_SQL)
        version = await self._schema_version(db)
        tables = await self._tables(db)
        try:
            if version < SCHEMA_VERSION:
                await self._migrate_to_v2(db, tables)
            else:
                await self._ensure_current_schema(db, tables)
            # 被动发送模式不再使用接收者与已发送记录。
            await db.execute("DROP TABLE IF EXISTS destinations")
            await db.execute("DROP TABLE IF EXISTS sent")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    async def _migrate_to_v2(
        self, db: aiosqlite.Connection, tables: set[str]
    ) -> None:
        if "subscriptions" not in tables:
            await db.execute(_SUBSCRIPTIONS_SQL)
        else:
            columns = await self._columns(db, "subscriptions")
            if "nickname" not in columns:
                await db.execute("ALTER TABLE subscriptions ADD COLUMN nickname TEXT")
            # 只在这一步给已有预设补昵称；之后用户清空昵称不会被重新写回。
            for _name, url, _weight, nickname in PRESETS:
                await db.execute(
                    "UPDATE subscriptions SET nickname = ? "
                    "WHERE url = ? AND (nickname IS NULL OR nickname = '')",
                    (nickname, url),
                )
        await db.execute(_NICKNAME_INDEX_SQL)

        if "articles" not in tables:
            await db.execute(_ARTICLES_SQL)
        else:
            columns = await self._columns(db, "articles")
            if "deleted_at" in columns:
                await db.execute("DROP TABLE IF EXISTS articles_new")
                await db.execute(_ARTICLES_NEW_SQL)
                await db.execute(
                    """
                    INSERT INTO articles_new
                        (source_id, guid, title, link, body, summary, pubdate, fetched_at)
                    SELECT source_id, guid, title, link, body, summary, pubdate, fetched_at
                    FROM articles
                    WHERE deleted_at IS NULL
                    """
                )
                await db.execute("DROP TABLE articles")
                await db.execute("ALTER TABLE articles_new RENAME TO articles")
        await db.execute(_ARTICLE_INDEX_SQL)
        await db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    async def _ensure_current_schema(
        self, db: aiosqlite.Connection, tables: set[str]
    ) -> None:
        await db.execute(_SUBSCRIPTIONS_SQL)
        if "subscriptions" in tables:
            columns = await self._columns(db, "subscriptions")
            if "nickname" not in columns:
                await db.execute("ALTER TABLE subscriptions ADD COLUMN nickname TEXT")
        await db.execute(_NICKNAME_INDEX_SQL)
        await db.execute(_ARTICLES_SQL)
        await db.execute(_ARTICLE_INDEX_SQL)
        await db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

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

    async def init_presets(
        self, presets: Sequence[tuple[str, str, int, str]], now: int
    ) -> bool:
        """首次初始化写入预设；用户删改后不再补回。"""

        async with self._session(write=True) as db:
            cursor = await db.execute(
                "SELECT value FROM meta WHERE key = 'presets_initialized'"
            )
            if await cursor.fetchone() is not None:
                return False
            for name, url, weight, nickname in presets:
                await db.execute(
                    "INSERT OR IGNORE INTO subscriptions "
                    "(name, url, nickname, weight, created_at, pull_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL)",
                    (name, url, nickname, weight, now),
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
                        WHERE a.source_id = s.id) AS article_count
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
                        WHERE a.source_id = s.id) AS article_count
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

    async def _used_weights(
        self, db: aiosqlite.Connection, exclude_id: int | None
    ) -> set[int]:
        if exclude_id is None:
            cursor = await db.execute("SELECT weight FROM subscriptions")
        else:
            cursor = await db.execute(
                "SELECT weight FROM subscriptions WHERE id != ?", (exclude_id,)
            )
        return {int(row["weight"]) for row in await cursor.fetchall()}

    async def _next_weight(self, db: aiosqlite.Connection) -> int:
        used = await self._used_weights(db, None)
        for weight in range(MIN_WEIGHT, MAX_WEIGHT + 1):
            if weight not in used:
                return weight
        raise SubscriptionLimitError("权重 1~10 已全部占用，无法再添加订阅")

    async def _nickname_taken(
        self, db: aiosqlite.Connection, nickname: str, exclude_id: int | None
    ) -> str | None:
        if exclude_id is None:
            cursor = await db.execute(
                "SELECT name FROM subscriptions WHERE nickname = ?", (nickname,)
            )
        else:
            cursor = await db.execute(
                "SELECT name FROM subscriptions WHERE nickname = ? AND id != ?",
                (nickname, exclude_id),
            )
        row = await cursor.fetchone()
        return str(row["name"]) if row is not None else None

    async def add_subscription(
        self,
        name: str,
        url: str,
        nickname: str | None,
        weight: int | None,
        now: int,
    ) -> Subscription:
        clean_name = _require_text(name, "订阅名称", 100)
        clean_url = _require_text(url, "RSS 地址", 2048)
        clean_nickname = _normalize_nickname(nickname)
        async with self._session(write=True) as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM subscriptions")
            row = await cursor.fetchone()
            if row is not None and int(row["total"]) >= MAX_SUBSCRIPTIONS:
                raise SubscriptionLimitError(
                    f"订阅列表最多 {MAX_SUBSCRIPTIONS} 条，请先删除一条再添加"
                )
            cursor = await db.execute(
                "SELECT id FROM subscriptions WHERE url = ?", (clean_url,)
            )
            if await cursor.fetchone() is not None:
                raise DuplicateValueError("这个 RSS 地址已经在订阅列表中")
            if clean_nickname is not None:
                owner = await self._nickname_taken(db, clean_nickname, None)
                if owner is not None:
                    raise DuplicateValueError(f"昵称“{clean_nickname}”已被订阅“{owner}”使用")
            used = await self._used_weights(db, None)
            chosen = await self._next_weight(db) if weight is None else weight
            if not MIN_WEIGHT <= chosen <= MAX_WEIGHT:
                raise DuplicateValueError("权重必须在 1~10 之间")
            if chosen in used:
                raise DuplicateValueError(f"权重 {chosen} 已被占用，请选择其它数值")
            cursor = await db.execute(
                "INSERT INTO subscriptions "
                "(name, url, nickname, weight, created_at, pull_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (clean_name, clean_url, clean_nickname, chosen, now),
            )
            subscription_id = int(cursor.lastrowid or 0)
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id) AS article_count
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
        nickname: str | None,
        nickname_changed: bool,
        weight: int | None,
        now: int,
    ) -> tuple[Subscription, bool]:
        """返回（更新后的订阅，URL 是否变化）。"""

        async with self._session(write=True) as db:
            cursor = await db.execute(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            )
            existing = await cursor.fetchone()
            if existing is None:
                raise NotFoundError("订阅不存在或已被删除")
            new_name = (
                _require_text(name, "订阅名称", 100)
                if name is not None
                else str(existing["name"])
            )
            new_url = (
                _require_text(url, "RSS 地址", 2048)
                if url is not None
                else str(existing["url"])
            )
            new_weight = int(existing["weight"]) if weight is None else weight
            if not MIN_WEIGHT <= new_weight <= MAX_WEIGHT:
                raise DuplicateValueError("权重必须在 1~10 之间")
            new_nickname = (
                _normalize_nickname(nickname)
                if nickname_changed
                else (str(existing["nickname"]) if existing["nickname"] else None)
            )
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
            if new_nickname is not None:
                owner = await self._nickname_taken(db, new_nickname, subscription_id)
                if owner is not None:
                    raise DuplicateValueError(f"昵称“{new_nickname}”已被订阅“{owner}”使用")
            if url_changed:
                await db.execute(
                    "DELETE FROM articles WHERE source_id = ?", (subscription_id,)
                )
                await db.execute(
                    "UPDATE subscriptions SET name = ?, url = ?, nickname = ?, "
                    "weight = ?, created_at = ?, pull_at = NULL WHERE id = ?",
                    (
                        new_name,
                        new_url,
                        new_nickname,
                        new_weight,
                        now,
                        subscription_id,
                    ),
                )
            else:
                await db.execute(
                    "UPDATE subscriptions SET name = ?, nickname = ?, weight = ? "
                    "WHERE id = ?",
                    (new_name, new_nickname, new_weight, subscription_id),
                )
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id) AS article_count
                FROM subscriptions s WHERE s.id = ?
                """,
                (subscription_id,),
            )
            updated = await cursor.fetchone()
            if updated is None:
                raise NotFoundError("订阅不存在或已被删除")
            return _row_to_subscription(updated), url_changed

    async def set_nickname(self, subscription_id: int, nickname: str | None) -> Subscription:
        clean_nickname = _normalize_nickname(nickname)
        async with self._session(write=True) as db:
            cursor = await db.execute(
                "SELECT id FROM subscriptions WHERE id = ?", (subscription_id,)
            )
            if await cursor.fetchone() is None:
                raise NotFoundError("订阅不存在或已被删除")
            if clean_nickname is not None:
                owner = await self._nickname_taken(db, clean_nickname, subscription_id)
                if owner is not None:
                    raise DuplicateValueError(f"昵称“{clean_nickname}”已被订阅“{owner}”使用")
            await db.execute(
                "UPDATE subscriptions SET nickname = ? WHERE id = ?",
                (clean_nickname, subscription_id),
            )
            cursor = await db.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM articles a
                        WHERE a.source_id = s.id) AS article_count
                FROM subscriptions s WHERE s.id = ?
                """,
                (subscription_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise NotFoundError("订阅不存在或已被删除")
            return _row_to_subscription(row)

    async def delete_subscription(self, subscription_id: int) -> bool:
        async with self._session(write=True) as db:
            await db.execute(
                "DELETE FROM articles WHERE source_id = ?", (subscription_id,)
            )
            cursor = await db.execute(
                "DELETE FROM subscriptions WHERE id = ?", (subscription_id,)
            )
            return cursor.rowcount > 0

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
                        (source_id, guid, title, link, body, summary, pubdate, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

    async def recent_articles(self, source_id: int, limit: int) -> list[Article]:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT * FROM articles
                WHERE source_id = ?
                ORDER BY pubdate DESC
                LIMIT ?
                """,
                (source_id, max(1, min(int(limit), 500))),
            )
            return [_row_to_article(row) for row in await cursor.fetchall()]

    async def random_article(self) -> tuple[Article, str] | None:
        """随机取一篇仍可用的缓存文章，只读，不改变任何记录。"""

        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT a.*, s.name AS source_name
                FROM articles a
                JOIN subscriptions s ON s.id = a.source_id
                WHERE (
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

    async def cleanup_articles(self, now: int, cache_days: int) -> int:
        """立即删除发布日期超过缓存保留天数的文章。"""

        cutoff = now - cache_days * 24 * 60 * 60
        async with self._session(write=True) as db:
            cursor = await db.execute(
                "DELETE FROM articles WHERE pubdate < ?", (cutoff,)
            )
            return max(cursor.rowcount, 0)

    async def list_recent_articles(self, limit: int = 30) -> list[ArticlePreview]:
        async with self._session() as db:
            cursor = await db.execute(
                """
                SELECT a.source_id, s.name AS source_name, a.guid, a.title, a.link,
                       a.pubdate,
                       CASE WHEN a.body IS NOT NULL AND a.body != '' THEN 1 ELSE 0 END AS has_body
                FROM articles a
                JOIN subscriptions s ON s.id = a.source_id
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
                )
                for row in await cursor.fetchall()
            ]

    async def article_count(self) -> int:
        async with self._session() as db:
            cursor = await db.execute("SELECT COUNT(*) AS total FROM articles")
            row = await cursor.fetchone()
            return int(row["total"]) if row is not None else 0

    async def latest_pull_at(self) -> int | None:
        async with self._session() as db:
            cursor = await db.execute("SELECT MAX(pull_at) AS latest FROM subscriptions")
            row = await cursor.fetchone()
            if row is None or row["latest"] is None:
                return None
            return int(row["latest"])


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
    "MAX_NICKNAME_LENGTH",
    "MAX_SUBSCRIPTIONS",
    "MAX_WEIGHT",
    "MIN_WEIGHT",
    "SCHEMA_VERSION",
    "RssNewsStore",
    "available_weights",
    "normalize_now",
]
