"""
Per-deal memory on disk, so a client reply tomorrow still sees today's outreach.

runs/deal_<id>/history.json    — the full model conversation (what the model needs)
runs/deal_<id>/transcript.md   — client ↔ Sasha only, human-readable (what people need)
"""

import json
from datetime import datetime
from pathlib import Path

import config


def deal_dir(deal_id: int) -> Path:
    path = config.RUNS_DIR / f"deal_{deal_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_history(deal_id: int) -> list[dict]:
    path = deal_dir(deal_id) / "history.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_history(deal_id: int, history: list[dict]) -> None:
    (deal_dir(deal_id) / "history.json").write_text(json.dumps(history, indent=2))


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
    for name in ("history.json", "transcript.md"):
        path = deal_dir(deal_id) / name
        if path.exists():
            path.unlink()
