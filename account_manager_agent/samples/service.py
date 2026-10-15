"""Carry out the sample actions Account Manager decides on.

The agent judges eligibility against the limits, collects the details and reads logistics
emails. This only stores the sample, creates its Freshdesk ticket and records status updates,
because those need database writes and the Freshdesk key, which stay outside the agent.
"""

from __future__ import annotations

import html
from typing import Callable

from sasha_database.samples import SampleRequests
from sasha_database.settings import SashaSettings

from .freshdesk import (
    FreshdeskClient,
    infer_sample_status_from_thread,
    summarize_conversations,
    ticket_id_from_url,
)

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

    def sync_freshdesk_for_deal(self, deal_id) -> dict:
        """On follow-up checks, read open sample tickets from Freshdesk and advance stored status when the thread shows it."""
        client = self.freshdesk_factory()
        if client is None:
            return {"configured": False, "updates_applied": [], "tickets": []}
        updates_applied = []
        tickets = []
        for row in self.samples.for_deal(deal_id):
            status = str(row.get("status") or "placed")
            if status in ("delivered", "cancelled"):
                continue
            ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
            if ticket_id is None:
                continue
            entry = {"sample_id": row["id"], "ticket_id": ticket_id, "status_before": status}
            try:
                ticket = client.get_ticket(ticket_id)
                conversations = client.get_conversations(ticket_id)
            except Exception as error:
                entry["error"] = str(error)
                tickets.append(entry)
                continue
            entry["freshdesk_status"] = ticket.get("status")
            entry["recent_thread"] = summarize_conversations(conversations)
            inferred = infer_sample_status_from_thread(conversations, status)
            if inferred and inferred != status:
                details = {"cancel_reason": "Cancelled per Freshdesk ticket" if inferred == "cancelled" else ""}
                self.samples.update(deal_id, row["id"], inferred, details)
                updates_applied.append({"sample_id": row["id"], "from": status, "to": inferred})
                entry["status_after"] = inferred
            tickets.append(entry)
        return {"configured": True, "updates_applied": updates_applied, "tickets": tickets}

    def sync_all_open_samples(self) -> dict:
        """Poll Freshdesk for every deal with an open sample ticket. Used by the sample sync worker."""
        client = self.freshdesk_factory()
        if client is None:
            return {"configured": False, "deals": []}
        deals = []
        for deal_id in self.samples.deal_ids_with_open_tickets():
            sync = self.sync_freshdesk_for_deal(deal_id)
            if sync.get("tickets") or sync.get("updates_applied"):
                deals.append({"deal_id": deal_id, **sync})
        return {"configured": True, "deals": deals}

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

    def _sample_row(self, deal_id, sample_id: int) -> dict | None:
        for row in self.samples.for_deal(deal_id):
            if int(row["id"]) == int(sample_id):
                return row
        return None

    def freshdesk_show(self, deal_id, sample_id: int) -> dict:
        """Read ticket status and recent thread for one sample (agent tool)."""
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
        if ticket_id is None:
            return {"ok": False, "error": "no_freshdesk_ticket", "sample": row}
        client = self.freshdesk_factory()
        if client is None:
            return {"ok": False, "error": "freshdesk_not_configured", "sample": row}
        try:
            ticket = client.get_ticket(ticket_id)
            conversations = client.get_conversations(ticket_id)
        except Exception as error:
            return {"ok": False, "error": str(error), "sample": row}
        return {
            "ok": True,
            "sample": row,
            "ticket_id": ticket_id,
            "freshdesk_status": ticket.get("status"),
            "recent_thread": summarize_conversations(conversations, limit=8),
        }

    def freshdesk_sync_sample(self, deal_id, sample_id: int) -> dict:
        """Pull Freshdesk thread for one sample and advance stored status when the thread shows it."""
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        sync = self.sync_freshdesk_for_deal(deal_id)
        entry = next(
            (item for item in sync.get("tickets", []) if int(item.get("sample_id", 0)) == int(sample_id)),
            None,
        )
        show = self.freshdesk_show(deal_id, sample_id)
        return {
            "ok": True,
            "configured": sync.get("configured"),
            "updates_applied": sync.get("updates_applied", []),
            "ticket": entry,
            "show": show,
        }

    def freshdesk_reply(self, deal_id, sample_id: int, body: str, private: bool = False) -> dict:
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
        if ticket_id is None:
            return {"ok": False, "error": "no_freshdesk_ticket"}
        client = self.freshdesk_factory()
        if client is None:
            return {"ok": False, "error": "freshdesk_not_configured"}
        if "<" in body:
            body_html = body
        else:
            paragraphs = [html.escape(part.strip()) for part in body.split("\n") if part.strip()]
            body_html = "".join(f"<p>{part}</p>" for part in paragraphs) or "<p></p>"
        try:
            if private:
                result = client.add_private_note(ticket_id, body_html)
            else:
                result = client.reply_to_ticket(ticket_id, body_html)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "ticket_id": ticket_id, "conversation_id": result.get("id")}

    def freshdesk_amend(
        self,
        deal_id,
        sample_id: int,
        *,
        style_code: str | None = None,
        color: str | None = None,
        size: str | None = None,
        message: str | None = None,
    ) -> dict:
        """Update sample garment fields on the Freshdesk ticket, reply to logistics, and patch the DB row."""
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        if str(row.get("status")) in ("delivered", "cancelled"):
            return {"ok": False, "error": "sample_not_open", "status": row.get("status")}
        ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
        if ticket_id is None:
            return {"ok": False, "error": "no_freshdesk_ticket"}
        client = self.freshdesk_factory()
        if client is None:
            return {"ok": False, "error": "freshdesk_not_configured"}
        new_style = (style_code or row.get("style_code") or "").strip()
        new_color = (color or row.get("color") or "").strip()
        new_size = (size or row.get("size") or "").strip()
        custom = {
            "cf_style_code": new_style,
            "cf_product_code": new_style,
            "cf_product_codes": new_style,
            "cf_garment_color": new_color,
        }
        try:
            client.update_ticket(ticket_id, {"custom_fields": custom})
        except Exception as error:
            return {"ok": False, "error": str(error)}
        lines = []
        if style_code and style_code.strip() != str(row.get("style_code") or "").strip():
            lines.append(f"Style code: {html.escape(style_code.strip())}")
        if color and color.strip() != str(row.get("color") or "").strip():
            lines.append(f"Color: {html.escape(color.strip())}")
        if size and size.strip() != str(row.get("size") or "").strip():
            lines.append(f"Size: {html.escape(size.strip())}")
        default_message = (
            "Please update this blank sample request with the following change(s) for the client:"
            if lines
            else "Following up on this blank sample request."
        )
        reply_body = message.strip() if message and message.strip() else default_message
        if lines:
            reply_body = f"{reply_body}\n" + "\n".join(f"- {line}" for line in lines)
        reply = self.freshdesk_reply(deal_id, sample_id, reply_body, private=False)
        if not reply.get("ok"):
            return reply
        self.samples.patch_details(
            deal_id,
            sample_id,
            style_code=new_style if style_code else None,
            color=new_color if color else None,
            size=new_size if size else None,
        )
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "reply": reply,
            "patched": {"style_code": new_style, "color": new_color, "size": new_size},
        }
