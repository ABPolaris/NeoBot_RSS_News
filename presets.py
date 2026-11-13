"""首次初始化写入的预设订阅；用户删改后不再补回。"""

from __future__ import annotations

PRESETS: tuple[tuple[str, str, int, str], ...] = (
    ("中新网要闻导读", "https://www.chinanews.com.cn/rss/importnews.xml", 1, "中新网"),
    ("IT之家", "https://www.ithome.com/rss/", 2, "之家"),
)

__all__ = ["PRESETS"]
