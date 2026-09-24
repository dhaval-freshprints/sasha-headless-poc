"""Persistent deal registry for the Sasha web app."""

from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEAL_ID = re.compile(r"^[0-9]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Deal:
    deal_id: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Deal":
        return cls(**value)


class DealStore:
    def __init__(self, deals_directory: Path) -> None:
        self.deals_directory = deals_directory
        self.deals_directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.deals: dict[str, Deal] = {}
        self._load()

    def add(self, deal_id: str) -> Deal:
        self._validate(deal_id)
        with self.lock:
            existing = self.deals.get(deal_id)
            if existing is not None:
                return Deal.from_dict(existing.to_dict())
            timestamp = utc_now()
            deal = Deal(deal_id, timestamp, timestamp)
            self.deals[deal_id] = deal
            self._save(deal)
            return Deal.from_dict(deal.to_dict())

    def touch(self, deal_id: str) -> Deal:
        self._validate(deal_id)
        with self.lock:
            deal = self.deals.get(deal_id)
            if deal is None:
                return self.add(deal_id)
            deal.updated_at = utc_now()
            self._save(deal)
            return Deal.from_dict(deal.to_dict())

    def get(self, deal_id: str) -> Deal:
        with self.lock:
            deal = self.deals.get(deal_id)
            if deal is None:
                raise KeyError(deal_id)
            return Deal.from_dict(deal.to_dict())

    def list_all(self) -> list[Deal]:
        with self.lock:
            values = [Deal.from_dict(deal.to_dict()) for deal in self.deals.values()]
        return sorted(values, key=lambda deal: deal.updated_at, reverse=True)

    def _load(self) -> None:
        for path in self.deals_directory.glob("*.json"):
            try:
                deal = Deal.from_dict(json.loads(path.read_text(encoding="utf-8")))
                self._validate(deal.deal_id)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            self.deals[deal.deal_id] = deal

    def _save(self, deal: Deal) -> None:
        path = self.deals_directory / f"DEAL{deal.deal_id}.json"
        temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(deal.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _validate(deal_id: str) -> None:
        if not DEAL_ID.fullmatch(deal_id):
            raise ValueError("deal_id must contain only digits")
