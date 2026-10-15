"""Read-only Main/Sasha queries shared by deal lookup and deal resolution.

Mirrors sasha-ai-assistant-v3 `core/workflow/deal_resolution/queries.py` and
`deal_consolidation.pick_canonical_deal_id`.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

CLOSED_STAGES = ("won", "lost")  # The only closed CRM stages (v3 CRM_DEAL_CLOSED_STAGES).


def load_database_helper():
    from sasha_database import read
    return read


def sasha_user_id() -> int:
    """Sasha's CRM user (deal manager of Sasha's deals), from sasha_settings key `sasha_user_id`."""
    from sasha_database.settings import SashaSettings

    value = SashaSettings().values("sasha_user_id").get("sasha_user_id", "")
    if not value.isdigit():
        raise ValueError("sasha_settings has no active numeric sasha_user_id")
    return int(value)


def positive_int(value) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@dataclass(frozen=True)
class DatabaseSession:
    query: Callable  # query(sql, parameters, limit=100) -> {"rows": [...], "truncated": bool}
    main: str
    operations: str


@contextmanager
def database_session(database=None):
    """Open one read-only connection. Without a helper, use the host's .env configuration."""
    if database is None:
        from .settings import export_database_environment

        export_database_environment()
        database = load_database_helper()
    schemas = database.configured_schemas()
    connection = database.connect(schemas["main"])
    try:
        yield DatabaseSession(
            lambda sql, parameters, limit=100: database.read_query(connection, sql, parameters, limit),
            database.quote_schema(schemas["main"]),
            database.quote_schema(schemas["operations"]),
        )
    finally:
        connection.rollback()
        connection.close()


def active_deals(db: DatabaseSession, client_ids, manager_id: int) -> dict:
    """Deals that are not deleted, not won/lost and managed by Sasha (v3 _SQL_ACTIVE_DEALS)."""
    clients = ",".join(["%s"] * len(client_ids))
    closed = ",".join(["%s"] * len(CLOSED_STAGES))
    return db.query(
        f"SELECT id AS deal_id,title,client_id,stage,created_at FROM {db.main}.deals "
        f"WHERE client_id IN ({clients}) AND deleted_at IS NULL AND manager_id=%s "
        f"AND LOWER(COALESCE(stage,'')) NOT IN ({closed}) ORDER BY id DESC",
        [*client_ids, manager_id, *CLOSED_STAGES],
    )


def deal_by_id(db: DatabaseSession, deal_id: int) -> dict | None:
    rows = db.query(
        f"SELECT id,client_id,title,stage FROM {db.main}.deals WHERE id=%s AND deleted_at IS NULL",
        [deal_id], limit=1,
    )["rows"]
    return rows[0] if rows else None


def sasha_status(db: DatabaseSession, deal_id: int) -> str:
    """Sasha's own status for the deal (sasha_deal_status); a deal without a row is active."""
    rows = db.query(f"SELECT status FROM {db.operations}.sasha_deal_status WHERE deal_id=%s "
                    "AND deleted_at IS NULL", [deal_id], limit=1)["rows"]
    return rows[0]["status"] if rows else "active"


def proof_ids_for_deal(db: DatabaseSession, deal_id: int) -> set[int]:
    rows = db.query(
        f"SELECT proof_id FROM {db.main}.deal_proofs WHERE deal_id=%s AND deleted_at IS NULL",
        [deal_id],
    )["rows"]
    return {proof_id for row in rows if (proof_id := positive_int(row["proof_id"]))}


def latest_outbound_by_deal(db: DatabaseSession, deal_ids) -> dict[int, object]:
    """Latest non-deleted Sasha outbound timestamp for each candidate deal."""
    if not deal_ids:
        return {}
    slots = ",".join(["%s"] * len(deal_ids))
    history = f"{db.operations}.sasha_conversation_history"
    rows = db.query(
        f"SELECT h.deal_id, MAX(COALESCE(h.message_timestamp,h.created_at)) AS last_outbound_at FROM {history} h "
        f"WHERE h.deal_id IN ({slots}) AND h.direction='OUTBOUND' AND h.deleted_at IS NULL "
        f"GROUP BY h.deal_id",
        [str(deal_id) for deal_id in deal_ids],
    )["rows"]
    return {int(row["deal_id"]): row["last_outbound_at"] for row in rows
            if row["last_outbound_at"] is not None}


def pick_canonical_deal_id(active_ids, latest_outbound: dict, incoming_deal_id: int) -> int | None:
    """v3 pick_canonical_deal_id: the deal Sasha last wrote to (lowest ID on a tie), else the
    incoming deal when it is active, else None."""
    if not active_ids:
        return None
    if len(active_ids) == 1:
        return active_ids[0]
    outreached = [deal_id for deal_id in active_ids if deal_id in latest_outbound]
    if outreached:
        latest = max(latest_outbound[deal_id] for deal_id in outreached)
        return min(deal_id for deal_id in outreached if latest_outbound[deal_id] == latest)
    return incoming_deal_id if incoming_deal_id in active_ids else None
