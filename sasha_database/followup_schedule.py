"""Follow-up trigger mechanics: the hourly due rule and the schedule
bookkeeping. Account Manager decides every follow-up date and time itself; code never
schedules one. A turn that should be followed up but ends without a date pauses the schedule.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("America/New_York")

INBOUND_TIMESTAMPS_KEY = "inbound_response_timestamps"
CLAIM_KEY = "last_unsent_attempt_et_date"


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def reply_timestamps(metadata: dict | None) -> list[datetime]:
    raw = (metadata or {}).get(INBOUND_TIMESTAMPS_KEY) or []
    return [stamp for stamp in map(_parse, raw if isinstance(raw, list) else []) if stamp]


def with_reply(metadata: dict | None, when: datetime) -> dict:
    meta = dict(metadata or {})
    stamps = list(meta.get(INBOUND_TIMESTAMPS_KEY) or [])
    stamp = _parse(when).astimezone(timezone.utc).isoformat()
    if stamp not in stamps:
        stamps.append(stamp)
    meta[INBOUND_TIMESTAMPS_KEY] = stamps
    return meta


def parse_check_time(value: str) -> datetime:
    """Account Manager's next check, a New York date and time (YYYY-MM-DDTHH:MM), as naive UTC.
    Sasha chooses the hour as well as the day; a bare date is refused."""
    text = str(value).strip()
    if len(text) <= 10:
        raise ValueError("next_check_on needs a New York date and time, YYYY-MM-DDTHH:MM")
    local = datetime.fromisoformat(text)
    if local.tzinfo is None:
        local = local.replace(tzinfo=BUSINESS_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def business_date(now: datetime) -> date:
    return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(BUSINESS_TZ).date()


def is_due(next_followup: datetime | None, now: datetime, last_followup: datetime | None,
           metadata: dict | None) -> bool:
    """The hourly rule: due once its ET hour arrives (earlier days catch up), never twice
    in an ET day, and never again on a day an unsent attempt already ran."""
    if next_followup is None:
        return False
    now_et = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(BUSINESS_TZ)
    if last_followup is not None and business_date(last_followup) == now_et.date():
        return False
    if str((metadata or {}).get(CLAIM_KEY) or "")[:10] == now_et.date().isoformat():
        return False
    next_et = (next_followup if next_followup.tzinfo
               else next_followup.replace(tzinfo=timezone.utc)).astimezone(BUSINESS_TZ)
    if next_et.date() != now_et.date():
        return next_et.date() < now_et.date()
    return next_et.hour == now_et.hour or next_et <= now_et



EVENTS = {"outreach", "followup_sent", "followup_unsent", "internal_update_sent",
          "client_reply_answered", "client_reply_unanswered"}
FOLLOW_UP_TURNS = {"outreach", "followup_sent", "followup_unsent"}
NO_DATE_REASON = "No next follow-up chosen after this turn"


def transition(row: dict | None, event: str, now: datetime, *, channel: str | None = None,
               reply_at: datetime | None = None, agent_set_date: bool = False) -> dict | None:
    """The schedule bookkeeping after a turn, as the columns to write (None: leave the row alone).

    Records what happened (first Sasha message, follow-ups sent, client reply times) and never
    picks a date. After outreach or a follow-up check, a turn where Account Manager chose no
    next day pauses the schedule: the day it ran is used up."""
    if event not in EVENTS:
        raise ValueError(f"Unknown follow-up event: {event}")
    status = (row or {}).get("status")
    if status in {"stopped", "completed"}:
        return None
    if row is None and event not in {"outreach", "followup_sent", "internal_update_sent"}:
        return None
    meta = dict((row or {}).get("followup_metadata") or {})
    meta.pop(CLAIM_KEY, None)
    if event.startswith("client_reply"):
        meta = with_reply(meta, reply_at or now)
    updates: dict[str, Any] = {"followup_metadata": meta}
    if row is None:
        updates.update(status="paused", first_message_timestamp=now, followup_count=0,
                       next_followup_date=None, scheduled_followup_reason=NO_DATE_REASON)
    if event in {"followup_sent", "internal_update_sent"}:
        updates.update(followup_count=int((row or {}).get("followup_count") or 0) + 1,
                       last_followup_timestamp=now,
                       last_channel_used=None if channel in {None, "both", "none"} else channel)
    if event in FOLLOW_UP_TURNS and not agent_set_date and status == "active":
        updates.update(status="paused", next_followup_date=None, scheduled_followup_reason=NO_DATE_REASON)
    return updates
