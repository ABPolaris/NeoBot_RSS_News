"""发送时间：伪代码中的 last_sent / 30 分钟缓冲 / N 天步长。"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from NeoBot_RSS_News.config import Config
from NeoBot_RSS_News.schedule import compute_next_send, in_send_window, missed_window

SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")


def ts(year: int, month: int, day: int, hour: int, minute: int, zone=SHANGHAI) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=zone).timestamp())


def local(value: int, zone=SHANGHAI) -> datetime:
    return datetime.fromtimestamp(value, zone)


def test_never_sent_uses_today() -> None:
    now = ts(2026, 11, 8, 17, 0)
    result = compute_next_send(None, now, Config())
    assert local(result) == datetime(2026, 11, 8, 18, 0, tzinfo=SHANGHAI)


def test_never_sent_after_time_moves_to_next_cycle() -> None:
    now = ts(2026, 11, 8, 18, 10)
    result = compute_next_send(None, now, Config())
    assert local(result) == datetime(2026, 11, 9, 18, 0, tzinfo=SHANGHAI)


def test_thirty_minute_coalesce_boundary() -> None:
    just_before = ts(2026, 11, 8, 17, 29)
    at_boundary = ts(2026, 11, 8, 17, 30)
    assert local(compute_next_send(None, just_before, Config())) == datetime(
        2026, 11, 8, 18, 0, tzinfo=SHANGHAI
    )
    assert local(compute_next_send(None, at_boundary, Config())) == datetime(
        2026, 11, 9, 18, 0, tzinfo=SHANGHAI
    )


def test_last_sent_sets_daily_phase() -> None:
    now = ts(2026, 11, 8, 17, 0)
    last = ts(2026, 11, 7, 18, 0)
    result = compute_next_send(last, now, Config())
    assert local(result) == datetime(2026, 11, 8, 18, 0, tzinfo=SHANGHAI)


def test_every_three_days_skips_missed_cycles() -> None:
    now = ts(2026, 11, 8, 12, 0)
    last = ts(2026, 11, 1, 18, 0)
    result = compute_next_send(last, now, Config(send_every_days=3))
    assert local(result) == datetime(2026, 11, 10, 18, 0, tzinfo=SHANGHAI)


def test_timezone_changes_wall_clock() -> None:
    now = ts(2026, 11, 8, 16, 0, UTC)
    result = compute_next_send(None, now, Config(timezone="Europe/London"))
    assert datetime.fromtimestamp(result, UTC) == datetime(
        2026, 11, 8, 18, 0, tzinfo=UTC
    )


def test_send_window_boundaries() -> None:
    now = 1_800_000_000
    assert in_send_window(now - 300, now, 300)
    assert in_send_window(now - 299, now, 300)
    assert not in_send_window(now - 301, now, 300)
    assert not in_send_window(now + 1, now, 300)
    assert missed_window(now - 301, now, 300)
    assert not missed_window(now - 300, now, 300)
    assert not missed_window(None, now, 300)
