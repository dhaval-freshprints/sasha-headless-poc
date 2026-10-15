"""Handoff emails are saved to an outbox; nothing is sent from this host."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sasha_database.config import env_value


@dataclass
class OutgoingEmail:
    kind: str  # isa_offer | client_transition
    deal_id: str
    to: str
    subject: str
    html: str
    cc: list[str] = field(default_factory=list)
    offer_id: int | None = None
    thread_id: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    delivery: str = "outbox"


def outbox_directory() -> Path:
    configured = env_value("HANDOFF_OUTBOX_DIRECTORY")
    if configured:
        return Path(configured)
    environment = env_value("FP_ENVIRONMENT") or "qa"
    return Path(__file__).resolve().parents[2] / "runs" / "handoff" / "outbox" / environment


class Outbox:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or outbox_directory()

    def save(self, email: OutgoingEmail) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{email.id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(email), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def list(self, deal_id: str | None = None) -> list[dict]:
        if not self.directory.is_dir():
            return []
        emails = []
        for path in self.directory.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if deal_id is None or value.get("deal_id") == str(deal_id):
                emails.append(value)
        return sorted(emails, key=lambda value: value["created_at"], reverse=True)


class Notifier:
    def __init__(self, outbox: Outbox | None = None):
        self.outbox = outbox or Outbox()

    @classmethod
    def from_environment(cls) -> "Notifier":
        return cls(Outbox())

    def send(self, email: OutgoingEmail) -> OutgoingEmail:
        self.outbox.save(email)
        return email
