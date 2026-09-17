"""
Per-deal memory on disk: the transcript of what the client and Sasha said.

runs/deal_<id>/transcript.md

This is the only thing Sasha remembers between turns. Tool calls, page trees and
screenshots from earlier turns are not carried forward; she re-reads the CRM each time.
"""

from datetime import datetime
from pathlib import Path

import config


def deal_dir(deal_id: int) -> Path:
    path = config.RUNS_DIR / f"deal_{deal_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_transcript(deal_id: int) -> str:
    path = deal_dir(deal_id) / "transcript.md"
    return path.read_text() if path.exists() else ""


def append_transcript(deal_id: int, client_message: str | None, sasha_reply: str) -> None:
    path = deal_dir(deal_id) / "transcript.md"
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []
    if client_message:
        lines.append(f"**Client** ({stamp}):\n\n{client_message}\n")
    lines.append(f"**Sasha** ({stamp}):\n\n{sasha_reply}\n")
    with path.open("a") as file:
        file.write("\n".join(lines) + "\n---\n\n")


def clear(deal_id: int) -> None:
    for name in ("transcript.md", "history.json"):
        path = deal_dir(deal_id) / name
        if path.exists():
            path.unlink()
