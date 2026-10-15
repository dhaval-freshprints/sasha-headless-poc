"""Timing and engagement signals for an explicit follow-up check.

Computed from the dated database conversation, the deal notes' follow-up history and the
business as_of_date, so the follow-up skill chooses a tactic from facts instead of a stage.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .notes import used_follow_up_tactics


def _entry_date(entry: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(entry.get("date") or ""))
    except ValueError:
        return None


def _nonblank(history) -> list[dict[str, Any]]:
    return [entry for entry in history
            if entry.get("role") in ("client", "sasha") and str(entry.get("content") or "").strip()]


def follow_up_signals(
    history, notes: str, as_of_date: str, days_since_client_reply: int | None = None,
) -> dict[str, Any]:
    today = date.fromisoformat(as_of_date)
    entries = _nonblank(history)
    client = [entry for entry in entries if entry["role"] == "client"]
    sasha = [entry for entry in entries if entry["role"] == "sasha"]

    def days_since(found: list[dict[str, Any]]) -> int | None:
        dated = [value for value in map(_entry_date, found) if value]
        return (today - max(dated)).days if dated else None

    unanswered = 0  # Sasha messages since the client last wrote.
    for entry in reversed(entries):
        if entry["role"] == "client":
            break
        unanswered += 1

    dates = [value for value in map(_entry_date, entries) if value]
    if days_since_client_reply is not None:
        client_reply, client_reply_source = days_since_client_reply, "supplied"
    else:
        client_reply = days_since(client)
        client_reply_source = "conversation" if client_reply is not None else "unknown"
    tactics = used_follow_up_tactics(notes)
    return {
        "as_of_date": as_of_date,
        "days_since_client_reply": client_reply,
        "days_since_client_reply_source": client_reply_source,
        "days_since_sasha_message": days_since(sasha),
        "conversation_started": min(dates).isoformat() if dates else None,
        "conversation_age_days": (today - min(dates)).days if dates else None,
        "client_messages": len(client),
        "sasha_messages": len(sasha),
        "sasha_messages_since_client_reply": unanswered,
        "undated_messages": len(entries) - len(dates),
        "follow_ups_generated": len(tactics),
        "used_tactics": tactics,
    }


def describe_signals(signals: dict[str, Any]) -> str:
    def days(value, unknown="unknown"):
        return unknown if value is None else f"{value} day(s)"

    if signals["client_messages"] == 0:
        client_reply = "the client has never replied"
    else:
        client_reply = days(signals["days_since_client_reply"])
        if signals["days_since_client_reply_source"] == "supplied":
            client_reply += " (supplied for this run)"
    lines = [
        f"- Business as_of_date: {signals['as_of_date']}",
        f"- Days since client reply: {client_reply}",
        f"- Days since Sasha last wrote: {days(signals['days_since_sasha_message'])}",
        f"- Conversation started: {signals['conversation_started'] or 'unknown'}"
        f" ({days(signals['conversation_age_days'])} ago)",
        f"- Messages so far: {signals['client_messages']} from the client, "
        f"{signals['sasha_messages']} from Sasha; "
        f"{signals['sasha_messages_since_client_reply']} Sasha message(s) since the client last wrote",
        f"- Follow-ups already generated: {signals['follow_ups_generated']}; used tactics: "
        f"{', '.join(signals['used_tactics']) or 'none'}",
    ]
    if signals["undated_messages"]:
        lines.append(f"- {signals['undated_messages']} older message(s) have no date; "
                     "read their timing from the deal's activity history")
    return "\n".join(lines)
