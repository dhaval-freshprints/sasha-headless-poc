"""Offer a handed-off deal to ISAs, time offers out, and apply ISA replies.

Port of Sasha v3 nodes/handoff (offer creation), handoff/expire_offers + advance (timeouts),
handoff/isa_reply (applying the decision) and handoff/client_notify (client transition).
Account Manager reads the ISA's reply and decides; this module only applies that decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
from typing import Callable

from sasha_database.handoff import HandoffOffers, Isa, Offer
from sasha_database.storage import SashaStorage

from .notify import Notifier, OutgoingEmail
from .selection import select_next_isa
from .working_hours import is_offer_timed_out, is_working_time

def offer_subject(deal_id) -> str:
    return f"Handoff request — Deal {deal_id}"


def offer_body(isa: Isa, deal_id, handoff_reason: str) -> str:
    reason = html.escape(handoff_reason.strip() or "(no reason given)").replace("\n", "<br>")
    return (
        f"Hi {html.escape(isa.first_name or 'there')},<br><br>"
        f"Sasha is handing off deal {deal_id} and offering it to you.<br><br>"
        f"Why Sasha is handing it off:<br>{reason}<br><br>"
        "Please reply to this email to accept or reject this handoff.<br>"
    )


def client_transition_body(person_name: str) -> str:
    """v3 Phase A copy: no ISA or technical terms."""
    person = html.escape(person_name or "a teammate")
    return (
        "<p>Hi there,</p>"
        f"<p>I've looped in {person} to help you from here. "
        "They'll take it from this point and follow up with you shortly.</p>"
        "<p>Thanks,<br>Sasha</p>"
    )


class HandoffService:
    def __init__(self, storage=None, offers=None, notifier: Notifier | None = None,
                 crm_factory: Callable | None = None):
        self.storage = storage or SashaStorage()
        self.offers = offers or HandoffOffers(self.storage.settings)
        self.notifier = notifier or Notifier.from_environment()
        self.crm_factory = crm_factory or _crm_from_environment

    # --- offers ---

    def offer_next(self, deal_id) -> dict:
        """Offer the deal to the next ISA and email them. Never raises."""
        try:
            status, reason = self.storage.deal_status(str(deal_id))
            if status != "handoff_needed":
                return {"outcome": "not_handed_off"}
            outcome, offer, isa = self.offers.create_offer(deal_id, select_next_isa)
            if outcome != "created":
                return {"outcome": outcome}
            email = self.notifier.send(OutgoingEmail(
                kind="isa_offer", deal_id=str(deal_id), to=isa.email, subject=offer_subject(deal_id),
                html=offer_body(isa, deal_id, reason), offer_id=offer.id))
            return {"outcome": "created", "offer_id": offer.id, "isa_email": isa.email,
                    "isa_name": isa.full_name, "delivery": email.delivery}
        except Exception as error:
            return {"outcome": "error", "error": type(error).__name__}

    def check_timeouts(self, now: datetime | None = None, force: bool = False) -> list[dict]:
        """Time out stale OFFERED rows and offer the next ISA; retry deals left without an offer."""
        now = now or datetime.now(timezone.utc)
        if not force and not is_working_time(now):
            return []
        results = []
        for offer in self.offers.stale_candidates():
            if (force or is_offer_timed_out(offer.offered_at, now)) and self.offers.mark_timed_out(offer.id):
                results.append({"deal_id": offer.deal_id, "timed_out_offer": offer.id,
                                **self.offer_next(offer.deal_id)})
        for deal_id in self.offers.deals_needing_offer():
            result = self.offer_next(deal_id)
            if result["outcome"] == "created":
                results.append({"deal_id": deal_id, **result})
        return results

    def handoff_context(self, deal_id) -> dict:
        """What the agent needs on a handed-off deal: the reason and the current ISA offer, if any."""
        status, reason = self.storage.deal_status(str(deal_id))
        offers = self.offers.offers_for_deal(deal_id)
        latest = offers[-1] if offers else None
        isa = self.offers.isa(latest.isa_id) if latest else None
        offer = None
        if latest:
            offer = {"id": latest.id, "status": latest.status, "offered_at": latest.offered_at,
                     "isa_email": isa.email if isa else None, "isa_name": isa.full_name if isa else None}
        return {"status": status, "reason": reason, "offer": offer}

    def apply_isa_decision(self, offer_id: int, decision: str) -> dict:
        """Apply Account Manager's reading of the ISA's reply: accepted, rejected or unclear."""
        offer = self.offers.offer(offer_id)
        isa = self.offers.isa(offer.isa_id) if offer else None
        if offer is None or isa is None:
            return {"decision": decision, "error": "The handoff offer was not found"}
        result = {"decision": decision, "deal_id": offer.deal_id, "offer_id": offer.id, "isa_email": isa.email}
        if decision == "accepted":
            result.update(self._accept(isa, offer))
        elif decision == "rejected":
            result["rejected"] = self.offers.mark_rejected(offer.id)
            result["next_offer"] = self.offer_next(offer.deal_id)
        return result

    def _accept(self, isa: Isa, offer: Offer) -> dict:
        if not self.offers.accept(offer.id):
            return {"accepted": False, "error": "The offer was no longer open"}
        result = {"accepted": True}
        deal_id = str(offer.deal_id)
        try:
            client = self.storage.client_contact(deal_id)
        except Exception as error:
            return {**result, "assign_manager": f"failed ({type(error).__name__})",
                    "client_transition": "not sent: client not found"}
        crm = self.crm_factory()
        if crm is None:
            result["assign_manager"] = "not configured"
        else:
            ok, message = crm.assign_manager(client["client_id"], isa.user_id, client["email"])
            result["assign_manager"] = "assigned" if ok else f"failed: {message}"
        if not client["email"]:
            return {**result, "client_transition": "not sent: client has no email"}
        thread = self.storage.latest_thread(deal_id)
        subject = thread["subject"] or "Following up on your Fresh Prints order"
        if thread["subject"] and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        body = client_transition_body(isa.full_name)
        email = self.notifier.send(OutgoingEmail(
            kind="client_transition", deal_id=deal_id, to=client["email"], cc=[isa.email],
            subject=subject, html=body, offer_id=offer.id, thread_id=thread["external_thread_id"]))
        self.storage.record_outbound(
            deal_id, f"handoff-{offer.id}", "client_response", body, subject=subject,
            delivery_status="generated",
            external_thread_id=thread["external_thread_id"])
        result["client_transition"] = email.delivery
        return result


def _crm_from_environment():
    from deal_manager_agent.v3_api import V3Api

    return V3Api.from_environment()
