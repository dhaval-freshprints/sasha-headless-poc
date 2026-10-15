"""Carry out the sample actions Account Manager decides on.

The agent judges eligibility against the limits, collects the details and reads logistics
emails. This only stores the sample, creates its Freshdesk ticket and records status updates,
because those need database writes and the Freshdesk key, which stay outside the agent.
"""

from __future__ import annotations

from typing import Callable

from sasha_database.samples import SampleRequests
from sasha_database.settings import SashaSettings

from .freshdesk import FreshdeskClient

# sasha_settings keys shared with Sasha v3; shown to the agent as the limits it applies.
LIMIT_KEYS = ("followup_sample_max_unit_value", "sample_max_per_deal", "followup_sample_cooldown_days")


class SampleService:
    def __init__(self, samples=None, storage=None, freshdesk_factory: Callable | None = None, settings=None):
        database = storage.settings if storage is not None else None
        self.samples = samples or SampleRequests(database)
        self.settings = settings or SashaSettings(database)
        self.freshdesk_factory = freshdesk_factory or FreshdeskClient.from_environment

    def context(self, deal_id) -> dict:
        """What the agent sees on every run: this deal's samples and the limits from sasha_settings."""
        return {"samples": self.samples.for_deal(deal_id), "limits": self.settings.values(*LIMIT_KEYS)}

    def place(self, deal_id, run_id: str, request: dict) -> dict:
        """Store the sample and create its Freshdesk ticket. Returns {"placed": bool, ...}; never raises."""
        try:
            row = self.samples.place(deal_id, run_id, request)
        except Exception as error:
            return {"placed": False, "reason": f"could not save the sample ({type(error).__name__})"}
        client = self.freshdesk_factory()
        if client is None:
            return {"placed": True, "sample_id": row["id"], "ticket": "not sent: Freshdesk is not configured"}
        try:
            url = client.create_sample_ticket(row, deal_id, request.get("organization", ""))
        except Exception as error:
            self.samples.update(deal_id, row["id"], "cancelled", {"cancel_reason": "Freshdesk ticket failed"})
            return {"placed": False, "reason": str(error)}
        self.samples.set_ticket(row["id"], url)
        return {"placed": True, "sample_id": row["id"], "ticket": url}

    def record_update(self, deal_id, update: dict) -> dict:
        """Store what Account Manager read from a logistics email."""
        try:
            self.samples.update(deal_id, int(update["sample_id"]), update["status"], update)
            return {"updated": True, "sample_id": update["sample_id"], "status": update["status"]}
        except Exception as error:
            return {"updated": False, "sample_id": update.get("sample_id"),
                    "reason": f"could not update the sample ({type(error).__name__})"}
