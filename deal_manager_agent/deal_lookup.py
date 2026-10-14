"""Deterministic sender -> active deal lookup that runs before the resolver agent.

Mirrors production: sasha-ai-assistant-v3 `deal_resolution` (`_SQL_ACTIVE_DEALS`,
`pick_canonical_deal_id`) and v3-crons `fetchClientInfoAndDeal` for the phone match.
"""

from dataclasses import asdict, dataclass
from typing import Literal

from .deal_queries import (
    active_deals, database_session, latest_outbound_by_deal, pick_canonical_deal_id,
    sasha_user_id,
)
from .models import LookupRequest


@dataclass(frozen=True)
class DealSearch:
    """found: one deal to use. none/multiple/error: the agent must decide."""

    outcome: Literal["found", "none", "multiple", "error"]
    reason: str
    deal_id: str | None = None
    candidates: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


class ActiveDealFinder:
    """Sender's own user record -> its open deals assigned to Sasha.

    Active means not deleted, stage not won/lost, and managed by Sasha, as in production.
    With several, take the deal Sasha last wrote to (latest outbound; lowest ID on a tie);
    if Sasha has written to none of them the agent decides. Account-level matches are
    weak evidence and are left to the agent.
    """

    def __init__(self, database=None, manager_id=None):
        self.database = database
        self.manager_id = manager_id

    def find(self, request: LookupRequest) -> DealSearch:
        try:
            return self._find(request)
        except Exception as error:
            # Error text can echo SQL or connection details; keep only the type.
            return DealSearch("error", f"Deal lookup failed ({type(error).__name__})")

    def _find(self, request: LookupRequest) -> DealSearch:
        manager_id = self.manager_id if self.manager_id is not None else sasha_user_id()
        with database_session(self.database) as db:
            client_ids = self._contact_ids(db.query, db.main, request)
            if not client_ids:
                return DealSearch("none", "No contact record matches the sender")
            found = active_deals(db, client_ids, manager_id)
            candidates = tuple(
                {"deal_id": str(row["deal_id"]), "title": row["title"], "stage": row["stage"]}
                for row in found["rows"]
            )
            if found["truncated"]:
                return DealSearch("multiple", "The sender has more deals than one lookup returns",
                                  candidates=candidates)
            if not candidates:
                return DealSearch("none", "The sender has no active deal assigned to Sasha")
            if len(candidates) == 1:
                return DealSearch("found", "The sender's only active deal",
                                  candidates[0]["deal_id"], candidates)
            return self._pick_by_outreach(db, candidates)

    def _contact_ids(self, query, main, request):
        if request.sender_phone:
            digits = request.sender_phone[1:]
            variants = [digits]
            if request.sender_phone.startswith("+1") and len(request.sender_phone) == 12:
                variants.append(digits[1:])
            slots = ",".join(["%s"] * len(variants))
            sql = (f"SELECT id FROM {main}.user WHERE deleted_at IS NULL "
                   f"AND REGEXP_REPLACE(phone,'[^0-9]','') IN ({slots})")
            parameters = variants
        else:
            sql = f"SELECT id FROM {main}.user WHERE deleted_at IS NULL AND email=%s"
            parameters = [request.sender_email]
        return [int(row["id"]) for row in query(sql, parameters, limit=20)["rows"]]

    def _pick_by_outreach(self, db, candidates):
        names = ", ".join(item["deal_id"] for item in candidates)
        deal_ids = [int(item["deal_id"]) for item in candidates]
        # incoming 0: no incoming deal yet, so only Sasha's outreach can choose (v3 inference).
        deal_id = pick_canonical_deal_id(deal_ids, latest_outbound_by_deal(db, deal_ids), 0)
        if deal_id is None:
            return DealSearch("multiple", f"Several active deals and no Sasha outreach: {names}",
                              candidates=candidates)
        return DealSearch("found", "Several active deals; Sasha last wrote to this one", str(deal_id),
                          tuple(item for item in candidates if item["deal_id"] == str(deal_id)))
