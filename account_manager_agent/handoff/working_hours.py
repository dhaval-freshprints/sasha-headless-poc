"""ISA working-hours math for offer timeouts (port of v3 handoff/working_hours.py)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo

ISA_TZ = ZoneInfo("America/New_York")
WORK_START = time(8, 0)
WORK_END = time(17, 0)


def offer_timeout_minutes() -> float:
    return float(os.environ.get("HANDOFF_OFFER_TIMEOUT_MINUTES") or 20)


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:  # Database values are naive UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ISA_TZ)


def working_minutes_elapsed(start: datetime, end: datetime) -> float:
    """Minutes of Mon–Fri [08:00, 17:00) America/New_York between start and end."""
    start_local, end_local = _local(start), _local(end)
    if end_local <= start_local:
        return 0.0
    total = 0.0
    day: date = start_local.date()
    while day <= end_local.date():
        if day.weekday() < 5:
            window_start = datetime.combine(day, WORK_START, tzinfo=ISA_TZ)
            window_end = datetime.combine(day, WORK_END, tzinfo=ISA_TZ)
            segment_start, segment_end = max(start_local, window_start), min(end_local, window_end)
            if segment_end > segment_start:
                total += (segment_end - segment_start).total_seconds()
        day += timedelta(days=1)
    return total / 60


def is_offer_timed_out(offered_at: datetime, now: datetime, minutes: float | None = None) -> bool:
    return working_minutes_elapsed(offered_at, now) >= (minutes if minutes is not None else offer_timeout_minutes())


def is_working_time(now: datetime | None = None) -> bool:
    local = _local(now or datetime.now(timezone.utc))
    return local.weekday() < 5 and WORK_START <= local.time() < WORK_END
