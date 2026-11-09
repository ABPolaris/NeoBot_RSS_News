"""发送时间计算：按周期、HH:MM、时区推导 next_send。"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from .config import Config

#: 配置保存后至少间隔 30 分钟才发送，避免刚保存就立刻触发。
COALESCE_SECONDS = 30 * 60


def compute_next_send(last_sent: int | None, now: int, config: Config) -> int:
    """按需求伪代码计算下一次发送时间（Unix 秒）。

    - 从未发送过：从配置时区“今天”的 HH:MM 开始；
    - 发送过：从 last_sent 起按 N 天步长对齐到 HH:MM；
    - 结果必须晚于 now + 30 分钟，否则继续加 N 天。
    """

    tz = config.tzinfo
    hour, minute = config.send_hour_minute
    step = timedelta(days=config.send_every_days)
    now_local = datetime.fromtimestamp(now, tz)
    if last_sent is None:
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    else:
        base = datetime.fromtimestamp(last_sent, tz)
        candidate = datetime.combine(
            base.date() + step, time(hour, minute), tzinfo=tz
        )
    threshold = now + COALESCE_SECONDS
    while candidate.timestamp() <= threshold:
        candidate += step
    return int(candidate.timestamp())


def in_send_window(next_send: int | None, now: int, window_seconds: int) -> bool:
    """当前是否落在 next_send 起的发送窗口内。"""

    return next_send is not None and next_send <= now <= next_send + window_seconds


def missed_window(next_send: int | None, now: int, window_seconds: int) -> bool:
    """发送窗口是否已经完全过去。"""

    return next_send is not None and now > next_send + window_seconds


__all__ = [
    "COALESCE_SECONDS",
    "compute_next_send",
    "in_send_window",
    "missed_window",
]
