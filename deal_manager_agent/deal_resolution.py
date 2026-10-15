"""Validate and consolidate a deal before every Account Manager run.

Port of sasha-ai-assistant-v3 `core/workflow/deal_resolution` (`resolve_incoming_deal`,
`run_duplicate_guard`, `run_deal_consolidation`). The workflow follows v3: a run with a
client message is CLIENT_RESPONSE, a run without one is INITIAL_OUTREACH.

Outreach only, first: the duplicate guard. Other active deals for the client with the same
title and the same proof set are Zap duplicates. One survives (the one Sasha wrote to last,
else the oldest created within DUPLICATE_DEAL_RECENCY_DAYS, else the incoming deal) and the
rest, the incoming deal included, are marked lost.

Both workflows, then: consolidation. The deal must be active for its client (not deleted,
not won/lost, managed by Sasha); for a client response a won deal still proceeds (replies
after an order). With several active deals, the kept deal is the one Sasha last wrote to,
else the incoming deal; the others' proofs are linked onto it and they are marked lost.
Outreach is skipped when the kept deal already had Sasha outreach.

Internal team mail (INTERNAL_EMAIL) only checks the deal is active or won for Sasha; it never
merges deals or marks them lost.

CRM writes go through the v3 edit-deal API (`v3_api.V3Api`). Not ported: v3's Sasha-DB side
effects of marking a deal lost (stopping its follow-up schedule, updating its deal context)
and its Slack alerts; those belong to Sasha v3.
"""

import os
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

from .deal_queries import (
    CLOSED_STAGES, active_deals, database_session, deal_by_id, latest_outbound_by_deal,
    pick_canonical_deal_id, positive_int, proof_ids_for_deal, sasha_status, sasha_user_id,
)

Workflow = Literal["client_response", "outreach", "internal_email"]
LOST_REASON = "Merged multi-deal client — proofs consolidated to deal {canonical}"
DUPLICATE_LOST_REASON = "Duplicate deal — Zap created multiple deals for same proof"
DEFAULT_DUPLICATE_RECENCY_DAYS = 45  # v3 DUPLICATE_DEAL_RECENCY_DAYS default
REASON_LABELS = {
    "single_active_deal": "The client's only active deal.",
    "consolidated_for_client_response": "Merged the client's other active deals into this one.",
    "consolidated_for_outreach": "Merged the client's other active deals into this one.",
    "consolidated_canonical_already_outreached":
        "Merged the client's active deals; the kept deal already had Sasha outreach, so outreach is skipped.",
    "incoming_deal_won_client_response": "Won deal: replies after an order are allowed.",
    "incoming_deal_lost_winback_reply": "Lost after a win-back question: the client's answer goes to Account Manager.",
    "internal_email_active_deal": "Active deal for Sasha; internal mail does not merge deals.",
    "incoming_deal_won_internal_email": "Won deal: internal updates after an order are allowed.",
    "incoming_deal_not_active": "The deal is not active for Sasha (won, lost, deleted, or managed by someone else).",
    "incoming_deal_not_found": "The deal was not found or is deleted.",
    "deal_has_no_client": "The deal has no client.",
    "missing_client_id": "The deal was not found or has no client.",
    "missing_deal_id": "No valid deal ID.",
    "not_duplicate": "No duplicate deals.",
    "incoming_deal_closed": "The deal is closed, so there is no duplicate check.",
    "incoming_deal_no_proofs": "The deal has no proofs, so there is no duplicate check.",
    "duplicate_outreach_on_outreached_sibling":
        "Duplicate deals: kept the one Sasha already wrote to and marked the others lost.",
    "duplicate_outreach_on_recent_sibling":
        "Duplicate deals: kept the oldest recent one and marked the others lost.",
    "duplicate_outreach_on_incoming_siblings_lost":
        "Duplicate deals: kept this deal and marked the older duplicates lost.",
    "crm_writes_not_configured":
        "Deals must be merged or marked lost, but CRM writes are not configured "
        "(V3_API_BASE_URL, JWT_AUTH_KEY, SASHA_ADMIN_USER).",
    "proof_link_failed": "Linking proofs onto the kept deal failed in the CRM.",
    "mark_lost_failed": "Marking a deal lost failed in the CRM.",
}
_client_locks: defaultdict[int, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


@dataclass
class Resolution:
    """proceed: run Account Manager on deal_id. skipped: v3 skips this outreach.
    handoff: a person must decide. Only proceed starts a run."""

    status: Literal["proceed", "skipped", "handoff"]
    reason: str
    deal_id: str | None = None
    consolidation: dict | None = None
    duplicate_guard: dict | None = None
    workflow: Workflow = "client_response"

    @property
    def summary(self) -> str:
        return REASON_LABELS.get(self.reason.split(" ")[0], self.reason)

    def to_dict(self) -> dict:
        return {**asdict(self), "summary": self.summary}


@dataclass
class Consolidation:
    canonical_deal_id: int
    merged_deal_ids: list[int]
    proof_ids_to_link: list[int]
    actions: list[dict] = field(default_factory=list)


@dataclass
class DuplicateGuard:
    survivor_deal_id: int
    lost_deal_ids: list[int]
    actions: list[dict] = field(default_factory=list)


class DealResolver:
    def __init__(self, database=None, crm=None, manager_id=None, crm_from_environment=True,
                 recency_days=None, today=None):
        self.database = database
        self.crm = crm
        self.manager_id = manager_id
        self.crm_from_environment = crm_from_environment
        self.recency_days = recency_days
        self.today = today

    def resolve(self, deal_id, workflow: Workflow = "client_response") -> Resolution:
        try:
            resolution = self._resolve(deal_id, workflow)
        except Exception as error:
            # Error text can echo SQL or connection details; keep only the type.
            resolution = Resolution("handoff", f"resolution_error ({type(error).__name__})")
        resolution.workflow = workflow
        return resolution

    def _resolve(self, deal_id, workflow: Workflow) -> Resolution:
        incoming = positive_int(deal_id)
        if incoming is None:
            return Resolution("handoff", "missing_deal_id")
        guard = None
        if workflow == "outreach":
            guard = self._duplicate_guard(incoming)
            if guard.status != "proceed":
                return guard
            incoming = int(guard.deal_id)
        # A fresh read: the duplicate guard may have just marked deals lost.
        resolution = self._consolidation(incoming, workflow)
        if guard is not None:
            resolution.duplicate_guard = guard.duplicate_guard
        return resolution

    def _crm(self):
        if self.crm is None and self.crm_from_environment:
            from .v3_api import V3Api

            self.crm = V3Api.from_environment()
        return self.crm

    @property
    def _manager(self) -> int:
        return self.manager_id if self.manager_id is not None else sasha_user_id()

    # --- duplicate guard (outreach) ---

    def _duplicate_guard(self, incoming: int) -> Resolution:
        with database_session(self.database) as db:
            deal = deal_by_id(db, incoming)
            if deal is None:
                return Resolution("handoff", "incoming_deal_not_found")
            client_id = positive_int(deal["client_id"])
            if client_id is None:
                return Resolution("handoff", "deal_has_no_client")
            if str(deal["stage"] or "").strip().lower() in CLOSED_STAGES:
                return Resolution("proceed", "incoming_deal_closed", str(incoming))
            proofs = proof_ids_for_deal(db, incoming)
            if not proofs:
                return Resolution("proceed", "incoming_deal_no_proofs", str(incoming))
            with _lock_for(client_id):
                title = _normalize_title(deal["title"])
                siblings = [row for row in active_deals(db, [client_id], self._manager)["rows"]
                            if int(row["deal_id"]) != incoming
                            and _normalize_title(row["title"]) == title
                            and proof_ids_for_deal(db, int(row["deal_id"])) == proofs]
                if not siblings:
                    return Resolution("proceed", "not_duplicate", str(incoming))
                survivor, reason = self._pick_survivor(db, incoming, siblings)
                lost = [d for d in [incoming, *(int(row["deal_id"]) for row in siblings)] if d != survivor]
                plan = DuplicateGuard(survivor, lost)

                def handoff(why):
                    return Resolution("handoff", why, duplicate_guard=asdict(plan))

                crm = self._crm()
                if crm is None:
                    return handoff("crm_writes_not_configured")
                for deal_id in lost:
                    ok, message = self._mark_lost(db, crm, deal_id, DUPLICATE_LOST_REASON)
                    plan.actions.append({"deal_id": deal_id, "action": "mark_lost", "ok": ok, "message": message})
                    if not ok:
                        return handoff("mark_lost_failed")
                return Resolution("proceed", reason, str(survivor), duplicate_guard=asdict(plan))

    def _pick_survivor(self, db, incoming: int, siblings: list[dict]) -> tuple[int, str]:
        sibling_ids = [int(row["deal_id"]) for row in siblings]
        latest = latest_outbound_by_deal(db, sibling_ids)
        outreached = [deal_id for deal_id in sibling_ids if deal_id in latest]
        if outreached:
            survivor = pick_canonical_deal_id(outreached, latest, incoming)
            if survivor not in outreached:
                survivor = min(outreached)
            return survivor, "duplicate_outreach_on_outreached_sibling"
        days = self.recency_days if self.recency_days is not None else int(
            os.environ.get("DUPLICATE_DEAL_RECENCY_DAYS") or DEFAULT_DUPLICATE_RECENCY_DAYS)
        today = self.today or datetime.now(timezone.utc).date()
        recent = [(created, int(row["deal_id"])) for row in siblings
                  if (created := _utc_date(row["created_at"])) and (today - created).days <= days]
        if recent:
            return min(recent)[1], "duplicate_outreach_on_recent_sibling"
        return incoming, "duplicate_outreach_on_incoming_siblings_lost"

    # --- consolidation (both workflows) ---

    def _consolidation(self, incoming: int, workflow: Workflow) -> Resolution:
        with database_session(self.database) as db:
            deal = deal_by_id(db, incoming)
            client_id = positive_int(deal["client_id"]) if deal else None
            if client_id is None:
                return Resolution("handoff", "missing_client_id")
            with _lock_for(client_id):
                active = [int(row["deal_id"]) for row in active_deals(db, [client_id], self._manager)["rows"]]
                if incoming not in active:
                    return _not_active(db, incoming, deal, workflow)
                if workflow == "internal_email":  # Team mail never merges deals or writes to the CRM.
                    return Resolution("proceed", "internal_email_active_deal", str(incoming))
                if len(active) == 1:
                    return Resolution("proceed", "single_active_deal", str(incoming))
                latest = latest_outbound_by_deal(db, active)
                canonical = pick_canonical_deal_id(active, latest, incoming)
                if canonical is None:
                    return Resolution("handoff", "incoming_deal_not_active")
                return self._consolidate(db, canonical, [d for d in active if d != canonical],
                                         workflow, canonical in latest)

    def _consolidate(self, db, canonical: int, others: list[int], workflow: Workflow,
                     canonical_had_outreach: bool) -> Resolution:
        canonical_proofs = proof_ids_for_deal(db, canonical)
        other_proofs = {deal_id: proof_ids_for_deal(db, deal_id) for deal_id in others}
        to_link = sorted(set().union(*other_proofs.values()) - canonical_proofs)
        plan = Consolidation(canonical, others, to_link)

        def handoff(reason):
            return Resolution("handoff", reason, consolidation=asdict(plan))

        crm = self._crm()
        if crm is None:
            return handoff("crm_writes_not_configured")
        if to_link:
            ok, message = crm.edit_deal(canonical, {"proofIds": to_link})
            plan.actions.append({"deal_id": canonical, "action": "link_proofs",
                                 "proof_ids": to_link, "ok": ok, "message": message})
            if not ok:
                return handoff("proof_link_failed")
        reason = LOST_REASON.format(canonical=canonical)
        for deal_id in others:
            ok, message = self._mark_lost(db, crm, deal_id, reason)
            plan.actions.append({"deal_id": deal_id, "action": "mark_lost", "ok": ok,
                                 "message": message, "proof_ids": sorted(other_proofs[deal_id] & set(to_link))})
            if not ok:
                return handoff("mark_lost_failed")
        if workflow == "outreach" and canonical_had_outreach:
            return Resolution("skipped", "consolidated_canonical_already_outreached", str(canonical), asdict(plan))
        return Resolution("proceed", f"consolidated_for_{workflow}", str(canonical), asdict(plan))

    @staticmethod
    def _mark_lost(db, crm, deal_id: int, reason: str) -> tuple[bool, str]:
        """v3 mark_deal_lost_in_crm: re-validate, then set stage lost with a lost reason."""
        deal = deal_by_id(db, deal_id)
        if deal is None:
            return False, f"Deal {deal_id} not found or deleted"
        stage = str(deal["stage"] or "").strip().lower()
        if stage == "lost":
            return True, f"Deal {deal_id} is already lost"
        if stage == "won":
            return False, f"Deal {deal_id} is in closed stage 'won' and cannot be updated"
        return crm.edit_deal(deal_id, {"stage": "lost", "lostReason": reason})


def _not_active(db, incoming: int, deal: dict, workflow: Workflow) -> Resolution:
    stage = str(deal["stage"] or "").strip().lower()
    if workflow != "outreach" and stage == "won":
        return Resolution("proceed", f"incoming_deal_won_{workflow}", str(incoming))
    # As in v3: after a win-back question the deal is lost in the CRM and paused for Sasha;
    # the client's answer still reaches Account Manager (reply only, the deal is not reopened).
    if workflow == "client_response" and stage == "lost" and sasha_status(db, incoming) == "paused":
        return Resolution("proceed", "incoming_deal_lost_winback_reply", str(incoming))
    return Resolution("handoff", "incoming_deal_not_active")


def _normalize_title(title) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip().lower())


def _utc_date(value) -> date | None:
    """A deal's created_at as a UTC calendar date (v3 recency.utc_calendar_date)."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).date() if parsed.tzinfo else parsed.date()


def _lock_for(client_id: int) -> threading.Lock:
    """Serialize resolution per client in this process (v3 uses a MySQL GET_LOCK)."""
    with _locks_guard:
        return _client_locks[client_id]
