"""Pure ISA selection for handoff offers (port of v3 nodes/handoff/isa_selection.py)."""

from __future__ import annotations

from typing import Sequence

from sasha_database.handoff import OPEN_STATUSES, Isa, Offer


def select_next_isa(isas: Sequence[Isa], deal_offers: Sequence[Offer], last_offered_isa_id: int | None) -> Isa | None:
    """Next untried ISA after the globally last offered one, in rank order.

    None when the deal already has an OFFERED or ACCEPTED offer, or every active ISA was tried
    on this deal (no wrap-around).
    """
    if not isas or any(offer.status in OPEN_STATUSES for offer in deal_offers):
        return None
    ordered = sorted(isas, key=lambda isa: (isa.rank_order, isa.id))
    tried = {offer.isa_id for offer in deal_offers}
    untried = [isa for isa in ordered if isa.id not in tried]
    if not untried:
        return None
    positions = {isa.id: index for index, isa in enumerate(ordered)}
    if last_offered_isa_id not in positions:
        return untried[0]
    start = positions[last_offered_isa_id]
    for offset in range(1, len(ordered) + 1):
        candidate = ordered[(start + offset) % len(ordered)]
        if candidate.id not in tried:
            return candidate
    return None
