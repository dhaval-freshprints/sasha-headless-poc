"""Carry out the sample actions Account Manager decides on.

The agent judges eligibility against the limits, collects the details and reads logistics
emails. This only stores the sample, creates its Freshdesk ticket and records status updates,
because those need database writes and the Freshdesk key, which stay outside the agent.
"""

from __future__ import annotations

import html
import re
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
        if not sync.get("configured"):
            return {"ok": False, "error": "freshdesk_not_configured"}
        entry = next(
            (item for item in sync.get("tickets", []) if int(item.get("sample_id", 0)) == int(sample_id)),
            None,
        )
        show = self.freshdesk_show(deal_id, sample_id)
        if entry is None:
            return {"ok": False, "error": "ticket_not_open_or_unavailable", "show": show}
        if entry.get("error"):
            return {"ok": False, "error": entry["error"], "ticket": entry}
        if not show.get("ok"):
            return show
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
        body_html = _plain_text_html(body)
        try:
            if private:
                result = client.add_private_note(ticket_id, body_html)
            else:
                result = client.reply_to_ticket(ticket_id, body_html)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, "ticket_id": ticket_id, "conversation_id": result.get("id")}

    def freshdesk_note(
        self,
        deal_id,
        sample_id: int,
        body: str,
        action_id: str | None = None,
    ) -> dict:
        """Add an internal logistics note without emailing the ticket requester."""
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
        if ticket_id is None:
            return {"ok": False, "error": "no_freshdesk_ticket"}
        client = self.freshdesk_factory()
        if client is None:
            return {"ok": False, "error": "freshdesk_not_configured"}
        marker = f"[Sasha action: {action_id}]" if action_id else ""
        try:
            if marker:
                conversations = client.get_conversations(ticket_id)
                if any(marker in str(item.get("body_text") or item.get("body") or "") for item in conversations):
                    return {"ok": True, "ticket_id": ticket_id, "duplicate": True}
            note_body = f"{body}\n{marker}" if marker else body
            result = client.add_private_note(ticket_id, _plain_text_html(note_body))
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
        product_link: str | None = None,
        message: str | None = None,
        action_id: str | None = None,
    ) -> dict:
        """Update sample garment fields on the Freshdesk ticket, reply to logistics, and patch the DB row."""
        row = self._sample_row(deal_id, sample_id)
        if row is None:
            return {"ok": False, "error": "sample_not_found"}
        if str(row.get("status")) != "placed":
            return {"ok": False, "error": "sample_already_progressed", "status": row.get("status")}
        ticket_id = ticket_id_from_url(row.get("freshdesk_ticket_url"))
        if ticket_id is None:
            return {"ok": False, "error": "no_freshdesk_ticket"}
        client = self.freshdesk_factory()
        if client is None:
            return {"ok": False, "error": "freshdesk_not_configured"}
        new_style = (style_code or row.get("style_code") or "").strip()
        new_color = (color or row.get("color") or "").strip()
        new_size = (size or row.get("size") or "").strip()
        style_changed = bool(style_code and new_style != str(row.get("style_code") or "").strip())
        color_changed = bool(color and new_color != str(row.get("color") or "").strip())
        size_changed = bool(size and new_size != str(row.get("size") or "").strip())
        try:
            ticket = client.get_ticket(ticket_id)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        original_description = str(ticket.get("description") or "")
        original_custom = dict(ticket.get("custom_fields") or {})
        custom = {}
        if style_changed:
            custom.update({
                "cf_style_code": new_style,
                "cf_product_code": new_style,
                "cf_product_codes": new_style,
            })
        if color_changed:
            custom["cf_garment_color"] = new_color
        if product_link:
            custom["cf_order_page_v3_link"] = product_link.strip()
        description = _amended_description(
            original_description,
            row,
            style_code=new_style if style_changed else None,
            color=new_color if color_changed else None,
            size=new_size if size_changed else None,
            product_link=product_link,
        )
        update_payload = {"description": description}
        if custom:
            update_payload["custom_fields"] = custom
        try:
            client.update_ticket(ticket_id, update_payload)
        except Exception as error:
            return {"ok": False, "error": str(error)}
        lines = []
        if style_changed:
            lines.append(f"Style code: {style_code.strip()}")
        if color_changed:
            lines.append(f"Color: {color.strip()}")
        if size_changed:
            lines.append(f"Size: {size.strip()}")
        if product_link:
            lines.append(f"Product link: {product_link.strip()}")
        default_message = (
            "Please update this blank sample request with the following change(s) for the client:"
            if lines
            else "Following up on this blank sample request."
        )
        reply_body = message.strip() if message and message.strip() else default_message
        if lines:
            reply_body = f"{reply_body}\n" + "\n".join(f"- {line}" for line in lines)
        try:
            if any((style_changed, color_changed, size_changed)):
                self.samples.patch_details(
                    deal_id,
                    sample_id,
                    style_code=new_style if style_changed else None,
                    color=new_color if color_changed else None,
                    size=new_size if size_changed else None,
                )
        except Exception as error:
            rollback_custom = {
                key: original_custom.get(key)
                for key in custom
            }
            try:
                client.update_ticket(
                    ticket_id,
                    {"description": original_description, "custom_fields": rollback_custom},
                )
            except Exception:
                return {
                    "ok": False,
                    "error": f"database_update_failed_and_freshdesk_rollback_failed ({type(error).__name__})",
                    "manual_reconciliation_required": True,
                }
            return {"ok": False, "error": f"database_update_failed ({type(error).__name__})"}
        note = self.freshdesk_note(deal_id, sample_id, reply_body, action_id=action_id)
        if not note.get("ok"):
            return {
                "ok": False,
                "error": note.get("error"),
                "state_updated": True,
                "note_added": False,
            }
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "note": note,
            "patched": {"style_code": new_style, "color": new_color, "size": new_size},
        }


def _plain_text_html(body: str) -> str:
    paragraphs = [html.escape(part.strip()) for part in body.split("\n") if part.strip()]
    return "".join(f"<p>{part}</p>" for part in paragraphs) or "<p></p>"


def _amended_description(
    description: str,
    row: dict,
    *,
    style_code: str | None,
    color: str | None,
    size: str | None,
    product_link: str | None,
) -> str:
    """Replace known ticket details and append a clear amendment when an old value is absent."""
    updates = (
        ("Style Code", row.get("style_code"), style_code),
        ("Apparel Color", row.get("color"), color),
        ("Size", row.get("size"), size),
    )
    amended = description
    appended = []
    for label, old, new in updates:
        if not new:
            continue
        old_text, new_text = html.escape(str(old or ""), quote=True), html.escape(new, quote=True)
        if old_text and old_text in amended:
            amended = amended.replace(old_text, new_text, 1)
        else:
            appended.append(f"{label}: {new_text}")
    if product_link:
        old_link = ""
        match = re.search(r"<a\s+href=[\"']([^\"']+)[\"']", amended, flags=re.IGNORECASE)
        if match:
            old_link = match.group(1)
        escaped_link = html.escape(product_link, quote=True)
        if old_link:
            amended = amended.replace(old_link, escaped_link, 2)
        else:
            appended.append(f'Product link: <a href="{escaped_link}">{escaped_link}</a>')
    if appended:
        amended += "<p><strong>Latest amendment</strong><br>" + "<br>".join(appended) + "</p>"
    return amended
