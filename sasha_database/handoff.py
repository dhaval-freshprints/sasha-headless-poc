"""ISA handoff offers in v3's sasha_handoff_isas and sasha_handoff_offers tables, used as they are.

At most one OFFERED or ACCEPTED offer per deal (v3's open_deal_id unique index). Offers made
by the POC leave sasha_deal_context_id empty.
"""

from dataclasses import dataclass
from datetime import datetime

from .config import DatabaseSettings
from .connection import transaction
from .messages import utc_now
from .storage import validate_deal_id

OPEN_STATUSES = ("OFFERED", "ACCEPTED")


@dataclass(frozen=True)
class Isa:
    id: int
    user_id: int
    email: str
    first_name: str
    last_name: str
    rank_order: int

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()


@dataclass(frozen=True)
class Offer:
    id: int
    deal_id: int
    isa_id: int
    status: str
    thread_id: str | None
    offered_at: datetime
    responded_at: datetime | None


def _isa(row) -> Isa:
    return Isa(int(row["id"]), int(row["user_id"]), str(row["email"] or "").strip(),
               str(row["first_name"] or "").strip(), str(row["last_name"] or "").strip(),
               int(row["rank_order"]))


def _offer(row) -> Offer:
    return Offer(int(row["id"]), int(row["deal_id"]), int(row["isa_id"]), row["status"],
                 row.get("thread_id"), row["offered_at"], row.get("responded_at"))


OFFER_COLUMNS = "id,deal_id,isa_id,status,thread_id,offered_at,responded_at"


class HandoffOffers:
    def __init__(self, settings=None, transaction_factory=transaction):
        self.settings = settings or DatabaseSettings.from_environment()
        self.transaction = transaction_factory

    def _read(self, sql, parameters=()):
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                return cursor.fetchall()

    def active_isas(self) -> list[Isa]:
        return [_isa(row) for row in self._read(
            "SELECT id,user_id,email,first_name,last_name,rank_order FROM sasha_handoff_isas "
            "WHERE is_active=1 ORDER BY rank_order,id")]

    def isa(self, isa_id: int) -> Isa | None:
        rows = self._read("SELECT id,user_id,email,first_name,last_name,rank_order "
                          "FROM sasha_handoff_isas WHERE id=%s", (isa_id,))
        return _isa(rows[0]) if rows else None

    def offers_for_deal(self, deal_id) -> list[Offer]:
        return [_offer(row) for row in self._read(
            f"SELECT {OFFER_COLUMNS} FROM sasha_handoff_offers WHERE deal_id=%s ORDER BY offered_at,id",
            (validate_deal_id(deal_id),))]

    def offer(self, offer_id: int) -> Offer | None:
        rows = self._read(f"SELECT {OFFER_COLUMNS} FROM sasha_handoff_offers WHERE id=%s", (offer_id,))
        return _offer(rows[0]) if rows else None

    def stale_candidates(self) -> list[Offer]:
        return [_offer(row) for row in self._read(
            f"SELECT {OFFER_COLUMNS} FROM sasha_handoff_offers WHERE status='OFFERED' ORDER BY offered_at")]

    def deals_needing_offer(self) -> list[int]:
        """Handed-off deals with no OFFERED or ACCEPTED offer: retried after a failed advance."""
        return [int(row["deal_id"]) for row in self._read(
            "SELECT s.deal_id FROM sasha_deal_status s WHERE s.status='handoff_needed' "
            "AND s.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM sasha_handoff_offers o "
            "WHERE o.deal_id=s.deal_id AND o.status IN ('OFFERED','ACCEPTED'))")]

    def create_offer(self, deal_id, choose) -> tuple[str, Offer | None, Isa | None]:
        """Lock the deal, then create the next offer chosen by `choose(isas, deal_offers, last_isa_id)`.

        Returns (outcome, offer, isa): created | skipped_existing | roster_exhausted | empty_roster.
        """
        deal_id = validate_deal_id(deal_id)
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM sasha_deal_status WHERE deal_id=%s FOR UPDATE", (deal_id,))
                cursor.execute("SELECT id,user_id,email,first_name,last_name,rank_order "
                               "FROM sasha_handoff_isas WHERE is_active=1 ORDER BY rank_order,id")
                isas = [_isa(row) for row in cursor.fetchall()]
                if not isas:
                    return "empty_roster", None, None
                cursor.execute(f"SELECT {OFFER_COLUMNS} FROM sasha_handoff_offers WHERE deal_id=%s",
                               (deal_id,))
                deal_offers = [_offer(row) for row in cursor.fetchall()]
                if any(offer.status in OPEN_STATUSES for offer in deal_offers):
                    return "skipped_existing", None, None
                cursor.execute("SELECT isa_id FROM sasha_handoff_offers ORDER BY offered_at DESC,id DESC LIMIT 1")
                latest = cursor.fetchone()
                isa = choose(isas, deal_offers, int(latest["isa_id"]) if latest else None)
                if isa is None:
                    return "roster_exhausted", None, None
                now = utc_now()
                cursor.execute(
                    "INSERT INTO sasha_handoff_offers (deal_id,isa_id,status,offered_at,created_at,updated_at) "
                    "VALUES (%s,%s,'OFFERED',%s,%s,%s)", (deal_id, isa.id, now, now, now))
                offer = Offer(cursor.lastrowid, deal_id, isa.id, "OFFERED", None, now, None)
        return "created", offer, isa

    def _transition(self, offer_id: int, status: str, allowed: tuple[str, ...]) -> bool:
        placeholders = ",".join(["%s"] * len(allowed))
        now = utc_now()
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE sasha_handoff_offers SET status=%s,responded_at=%s,updated_at=%s "
                    f"WHERE id=%s AND status IN ({placeholders})",
                    (status, now if status != "TIMED_OUT" else None, now, offer_id, *allowed))
                return cursor.rowcount == 1

    def mark_timed_out(self, offer_id: int) -> bool:
        return self._transition(offer_id, "TIMED_OUT", ("OFFERED",))

    def mark_rejected(self, offer_id: int) -> bool:
        return self._transition(offer_id, "REJECTED", ("OFFERED", "TIMED_OUT"))

    def accept(self, offer_id: int) -> bool:
        """Accept this offer and reject the deal's other OFFERED rows, in one transaction."""
        now = utc_now()
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT deal_id FROM sasha_handoff_offers WHERE id=%s", (offer_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                cursor.execute("SELECT id FROM sasha_deal_status WHERE deal_id=%s FOR UPDATE", (row["deal_id"],))
                cursor.execute("SELECT 1 FROM sasha_handoff_offers WHERE deal_id=%s AND status='ACCEPTED'",
                               (row["deal_id"],))
                if cursor.fetchone():
                    return False
                cursor.execute(
                    "UPDATE sasha_handoff_offers SET status='REJECTED',responded_at=%s,updated_at=%s "
                    "WHERE deal_id=%s AND id<>%s AND status='OFFERED'", (now, now, row["deal_id"], offer_id))
                cursor.execute(
                    "UPDATE sasha_handoff_offers SET status='ACCEPTED',responded_at=%s,updated_at=%s "
                    "WHERE id=%s AND status IN ('OFFERED','TIMED_OUT')", (now, now, offer_id))
                return cursor.rowcount == 1
