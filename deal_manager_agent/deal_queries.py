"""Read-only Main/Sasha queries shared by deal lookup and deal resolution.

Mirrors sasha-ai-assistant-v3 `core/workflow/deal_resolution/queries.py` and
`deal_consolidation.pick_canonical_deal_id`.
"""

import importlib.util
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DATABASE_HELPER = (Path(__file__).resolve().parent / "skills" / "deal-manager-database"
                   / "scripts" / "database.py")
CLOSED_STAGES = ("won", "lost")  # The only closed CRM stages (v3 CRM_DEAL_CLOSED_STAGES).
DEFAULT_SASHA_USER_ID = 297140  # Sasha's CRM user for active deals; set SASHA_USER_ID to override.


def load_database_helper():
    spec = importlib.util.spec_from_file_location("deal_manager_database", DATABASE_HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sasha_user_id() -> int:
    return int(os.environ.get("SASHA_USER_ID") or DEFAULT_SASHA_USER_ID)


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


def proof_ids_for_deal(db: DatabaseSession, deal_id: int) -> set[int]:
    rows = db.query(
        f"SELECT proof_id FROM {db.main}.deal_proofs WHERE deal_id=%s AND deleted_at IS NULL",
        [deal_id],
    )["rows"]
    return {proof_id for row in rows if (proof_id := positive_int(row["proof_id"]))}


def latest_outbound_by_deal(db: DatabaseSession, deal_ids) -> dict[int, object]:
    """Latest Sasha outbound time for each deal Sasha has outreached (context + outbound row)."""
    if not deal_ids:
        return {}
    slots = ",".join(["%s"] * len(deal_ids))
    history = f"{db.operations}.sasha_communication_history"
    rows = db.query(
        f"SELECT h.deal_id, MAX(h.message_timestamp) AS last_outbound_at FROM {history} h "
        f"WHERE h.deal_id IN ({slots}) AND UPPER(COALESCE(h.direction,''))='OUTBOUND' "
        f"AND h.message_timestamp IS NOT NULL AND EXISTS "
        f"(SELECT 1 FROM {db.operations}.sasha_deal_context c WHERE c.deal_id=h.deal_id) "
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
