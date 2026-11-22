"""System clock in Gulf Standard Time (NFR-18).

A time offset can be set for tests and for the live demo
("simulate +20 minutes" to show the no-show release).
"""
from datetime import datetime, timedelta, timezone

# Gulf Standard Time is always UTC+4 (the UAE has no daylight saving), so a
# fixed offset is used. This works on every operating system without needing
# a time-zone database (Windows does not ship one).
GST = timezone(timedelta(hours=4), "GST")
_offset = timedelta(0)


def now() -> datetime:
    """Current local time (naive, Gulf Standard Time) plus any simulated offset."""
    return datetime.now(GST).replace(tzinfo=None, microsecond=0) + _offset


def advance(minutes: int) -> datetime:
    global _offset
    _offset += timedelta(minutes=minutes)
    return now()


def reset() -> None:
    global _offset
    _offset = timedelta(0)


def offset_minutes() -> int:
    return int(_offset.total_seconds() // 60)
