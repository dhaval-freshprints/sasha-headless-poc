"""Free blank samples placed for a deal (sasha_sample_requests)."""

from __future__ import annotations

from .config import DatabaseSettings
from .connection import transaction
from .messages import utc_now
from .storage import validate_deal_id

COLUMNS = ("id,status,style_code,color,size,freshdesk_ticket_url,created_at AS placed_at,shipped_at,"
           "delivered_at,cancelled_at,cancel_reason")
UPDATED = ("cancel_reason",)
STATUS_TIMES = {"shipped": "shipped_at", "delivered": "delivered_at", "cancelled": "cancelled_at"}


class SampleRequests:
    def __init__(self, settings=None, transaction_factory=transaction):
        self.settings = settings or DatabaseSettings.from_environment()
        self.transaction = transaction_factory

    def _write(self, sql, parameters):
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                return cursor.lastrowid

    def for_deal(self, deal_id) -> list[dict]:
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT {COLUMNS} FROM sasha_sample_requests WHERE deal_id=%s "
                               "AND deleted_at IS NULL ORDER BY id", (validate_deal_id(deal_id),))
                return cursor.fetchall()

    def deal_ids_with_open_tickets(self, limit: int = 200) -> list[str]:
        """Deals that still have a sample ticket to poll on Freshdesk."""
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT deal_id FROM sasha_sample_requests "
                    "WHERE deleted_at IS NULL AND status IN ('placed','shipped') "
                    "AND freshdesk_ticket_url IS NOT NULL ORDER BY deal_id LIMIT %s",
                    (limit,),
                )
                return [str(row["deal_id"]) for row in cursor.fetchall()]

    def place(self, deal_id, run_id: str, request: dict) -> dict:
        sample_id = self._write(
            "INSERT INTO sasha_sample_requests (deal_id,run_id,status,style_code,color,size) "
            "VALUES (%s,%s,'placed',%s,%s,%s)",
            (validate_deal_id(deal_id), run_id, request["style_code"], request["color"], request["size"]))
        return {**request, "id": sample_id, "status": "placed"}

    def set_ticket(self, sample_id: int, ticket_url: str) -> None:
        self._write("UPDATE sasha_sample_requests SET freshdesk_ticket_url=%s WHERE id=%s", (ticket_url, sample_id))

    def update(self, deal_id, sample_id: int, status: str, details: dict) -> None:
        values = {name: details[name] for name in UPDATED if details.get(name)}
        values.update({"status": status, STATUS_TIMES[status]: utc_now()})
        assignments = ",".join(f"{name}=%s" for name in values)
        self._write(f"UPDATE sasha_sample_requests SET {assignments} WHERE id=%s AND deal_id=%s",
                    (*values.values(), sample_id, validate_deal_id(deal_id)))

    def patch_details(self, deal_id, sample_id: int, **fields) -> None:
        """Update style, color, or size on an open sample row (after logistics agrees on Freshdesk)."""
        allowed = ("style_code", "color", "size")
        values = {name: fields[name] for name in allowed if fields.get(name)}
        if not values:
            return
        assignments = ",".join(f"{name}=%s" for name in values)
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE sasha_sample_requests SET {assignments} "
                    "WHERE id=%s AND deal_id=%s AND status='placed' AND deleted_at IS NULL",
                    (*values.values(), sample_id, validate_deal_id(deal_id)),
                )
                if cursor.rowcount != 1:
                    raise ValueError("Sample is no longer an amendable placed request")
