"""Host-owned persistence for Sasha status, notes, and conversations."""

from datetime import datetime

from dataclasses import replace
import json

from .config import DatabaseSettings, quote_schema, required
from .connection import transaction
from .messages import (
    CONVERSATION_TYPES, IncomingMessage, read_sms_messages, sms_body, utc_now, valid_email,
    validate_message_body, validate_sms_messages,
)


def validate_handoff_reason(reason: str) -> str:
    """The agent's own words for why a person should take over; only emptiness is refused."""
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("A handoff needs the agent's reason")
    return reason


AGENT_STATUSES = {"active", "paused", "opt_out", "completed"}


def validate_deal_state(state: dict) -> dict:
    """The Account Manager's decision about the deal: status, its reasoning, and the next check day."""
    status = str(state.get("status") or "").strip()
    reason = str(state.get("reason") or "").strip()
    next_check = str(state.get("next_check_on") or "").strip() or None
    if status not in AGENT_STATUSES:
        raise ValueError(f"deal_state.status must be one of {', '.join(sorted(AGENT_STATUSES))}")
    if not reason:
        raise ValueError("deal_state needs the Account Manager's reason")
    if next_check is not None:
        from datetime import date
        date.fromisoformat(next_check)
    return {"status": status, "reason": reason, "next_check_on": next_check,
            "crm_lost_reason": str(state.get("crm_lost_reason") or "").strip()}


def validate_deal_id(deal_id: str) -> int:
    if not str(deal_id).isascii() or not str(deal_id).isdigit() or not 0 < int(deal_id) <= 2147483647:
        raise ValueError("deal_id must be a positive database INT")
    return int(deal_id)


def draft_rows(message_html: str, sms_messages: list) -> list[dict]:
    validate_message_body(message_html)
    validate_sms_messages(sms_messages)
    rows = []
    if message_html.strip():
        rows.append({"message_type": "EMAIL", "body": message_html, "content_type": "text/html"})
    if sms_messages:
        body = json.dumps(sms_messages, ensure_ascii=False, sort_keys=True)
        validate_message_body(body)
        rows.append({"message_type": "SMS", "body": body, "content_type": "text"})
    return rows


def same_drafts(existing, drafts, convo_type) -> bool:
    if len(existing) != len(drafts):
        return False
    expected = {draft["message_type"]: draft for draft in drafts}
    if len({row["message_type"] for row in existing}) != len(existing):
        return False
    for row in existing:
        draft = expected.get(row["message_type"])
        if not draft or row["convo_type"] != convo_type:
            return False
        if row["body"] != draft["body"] or row["content_type"] != draft["content_type"]:
            return False
    return True


class SashaStorage:
    def __init__(self, settings=None, transaction_factory=transaction):
        self.settings = settings or DatabaseSettings.from_environment()
        self.transaction = transaction_factory

    def _lock_deal(self, cursor, deal_id):
        cursor.execute(
            "INSERT INTO sasha_deal_status (deal_id,status) VALUES (%s,'active') "
            "ON DUPLICATE KEY UPDATE deal_id=VALUES(deal_id)", (deal_id,))
        cursor.execute("SELECT id FROM sasha_deal_status WHERE deal_id=%s FOR UPDATE", (deal_id,))
        cursor.fetchone()

    def prepare_run(self, deal_id: str, run_id: str, messages=()) -> tuple[IncomingMessage, ...]:
        deal_id = validate_deal_id(deal_id)
        if not run_id or len(run_id) > 64:
            raise ValueError("run_id must contain between 1 and 64 characters")
        stored = []
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                self._lock_deal(cursor, deal_id)
                for message in messages:
                    if not message.sender_name:
                        message = replace(message, sender_name=self._client_email(cursor, deal_id))
                    stored.append(self._save_incoming(cursor, deal_id, run_id, message))
        return tuple(stored)

    def _client_email(self, cursor, deal_id):
        main = quote_schema(self.settings.values["DEAL_MANAGER_MAIN_SCHEMA"])
        cursor.execute(
            f"SELECT u.email FROM {main}.deals d JOIN {main}.user u ON u.id=d.client_id "
            "WHERE d.id=%s AND d.deleted_at IS NULL AND u.deleted_at IS NULL", (deal_id,))
        row = cursor.fetchone()
        email = str(row["email"] or "").strip() if row else ""
        if not valid_email(email):
            raise ValueError("The deal client has no usable email; supply the incoming sender email")
        return email

    def _save_incoming(self, cursor, deal_id, run_id, message):
        if message.row_id is not None:
            cursor.execute(
                "SELECT id,body FROM sasha_conversation_history "
                "WHERE id=%s AND deal_id=%s AND direction='INBOUND' AND deleted_at IS NULL",
                (message.row_id, deal_id))
            row = cursor.fetchone()
            if not row or row["body"] != message.body:
                raise ValueError("Stored incoming message does not match this deal")
            return message
        if message.external_message_id:
            cursor.execute(
                "SELECT id,body FROM sasha_conversation_history "
                "WHERE deal_id=%s AND message_type=%s AND external_message_id=BINARY %s "
                "AND direction='INBOUND' AND deleted_at IS NULL ORDER BY id LIMIT 1",
                (deal_id, message.message_type, message.external_message_id))
            existing = cursor.fetchone()
            if existing:
                if existing["body"] != message.body:
                    raise ValueError("Provider message ID was already used for a different body")
                return replace(message, row_id=existing["id"])
        cursor.execute(
            "INSERT INTO sasha_conversation_history "
            "(deal_id,run_id,convo_type,direction,sender_name,message_type,subject,body,"
            "content_type,attachments,external_message_id,external_thread_id,message_timestamp) "
            "VALUES (%s,%s,%s,'INBOUND',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (deal_id, run_id, message.convo_type, message.sender_name, message.message_type,
             message.subject, message.body, message.content_type,
             json.dumps(message.attachments) if message.attachments else None,
             message.external_message_id, message.external_thread_id, message.timestamp()))
        return replace(message, row_id=cursor.lastrowid)

    def load_notes(self, deal_id: str) -> str:
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                return self._notes(cursor, validate_deal_id(deal_id))

    def _notes(self, cursor, deal_id):
        cursor.execute("SELECT notes FROM sasha_deal_notes WHERE deal_id=%s AND deleted_at IS NULL", (deal_id,))
        row = cursor.fetchone()
        return row["notes"] if row else ""

    def load_context(self, deal_id: str):
        deal_id = validate_deal_id(deal_id)
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                notes = self._notes(cursor, deal_id)
                cursor.execute(
                    "SELECT * FROM sasha_conversation_history WHERE deal_id=%s AND deleted_at IS NULL "
                    "ORDER BY COALESCE(message_timestamp,created_at),id", (deal_id,))
                history = [self._history_entry(row) for row in cursor.fetchall()]
        return notes, history

    @staticmethod
    def _history_entry(row):
        timestamp = row["message_timestamp"] or row["created_at"]
        messages = read_sms_messages(row)
        return {
            "id": row["id"], "role": "sasha" if row["direction"] == "OUTBOUND" else "client",
            "run_id": row.get("run_id"),
            "sender_name": row["sender_name"], "message_type": row["message_type"],
            "subject": row["subject"], "convo_type": row["convo_type"],
            "content": sms_body(messages) if messages is not None else row["body"],
            "content_type": row["content_type"],
            "delivery_status": row["delivery_status"], "date": timestamp.date().isoformat(),
            "attachments": json.loads(row["attachments"]) if isinstance(row["attachments"], str) else row["attachments"],
            "sms_messages": messages,
        }

    @staticmethod
    def sender_email():
        email = required("SASHA_SENDER_EMAIL")
        if not valid_email(email) or len(email) > 255:
            raise ValueError("SASHA_SENDER_EMAIL must be a valid email address")
        return email

    def deal_status(self, deal_id: str) -> tuple[str, str]:
        """(status, status_reason); a deal without a status row is active."""
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status,status_reason FROM sasha_deal_status "
                    "WHERE deal_id=%s AND deleted_at IS NULL", (validate_deal_id(deal_id),))
                row = cursor.fetchone()
        return (row["status"], row["status_reason"] or "") if row else ("active", "")

    def deal_state(self, deal_id: str) -> dict:
        """The deal's Sasha status with its reasons, plus its row in v3's follow-up schedule."""
        deal_id = validate_deal_id(deal_id)
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status,status_reason FROM sasha_deal_status "
                    "WHERE deal_id=%s AND deleted_at IS NULL", (deal_id,))
                row = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT status,next_followup_date,scheduled_followup_reason,stop_reason,followup_count,last_channel_used "
                    "FROM sasha_followup_schedule WHERE deal_id=%s", (deal_id,))
                followup = cursor.fetchone()
        return {"status": row.get("status") or "active", "status_reason": row.get("status_reason") or "",
                "followup": {key: (value.isoformat() if hasattr(value, "isoformat") else value)
                             for key, value in followup.items()} if followup else None}

    def apply_followup_schedule(self, deal_id: str, state: dict) -> str:
        """Record Account Manager's decision in v3's sasha_followup_schedule, as v3 does:
        a check date schedules the follow-up; opt-out, win-back and completion stop it."""
        deal_id = validate_deal_id(deal_id)
        status, reason = state["status"], state["reason"][:65535]
        stop = ("opted_out" if status == "opt_out" else "completed" if status == "completed"
                else "opt_out_winback" if state.get("crm_lost_reason") else None)
        now = utc_now()
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                if stop:
                    cursor.execute(
                        "INSERT INTO sasha_followup_schedule (deal_id,status,stop_reason,first_message_timestamp) "
                        "VALUES (%s,'stopped',%s,%s) ON DUPLICATE KEY UPDATE status='stopped',"
                        "stop_reason=VALUES(stop_reason),next_followup_date=NULL,scheduled_followup_reason=NULL",
                        (deal_id, stop, now))
                    return f"stopped ({stop})"
                if state.get("next_check_on"):
                    # The cron matches the hour; 14:00 UTC is mid-morning in US time zones.
                    when = datetime.fromisoformat(state["next_check_on"]).replace(hour=14)
                    cursor.execute(
                        "INSERT INTO sasha_followup_schedule (deal_id,status,first_message_timestamp,"
                        "next_followup_date,scheduled_followup_reason) VALUES (%s,'active',%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE status='active',stop_reason=NULL,"
                        "next_followup_date=VALUES(next_followup_date),"
                        "scheduled_followup_reason=VALUES(scheduled_followup_reason)",
                        (deal_id, now, when, reason))
                    return f"scheduled for {state['next_check_on']}"
                if status == "paused":
                    cursor.execute(
                        "INSERT INTO sasha_followup_schedule (deal_id,status,first_message_timestamp) "
                        "VALUES (%s,'paused',%s) ON DUPLICATE KEY UPDATE "
                        # MySQL applies these left to right, so status is changed last.
                        "next_followup_date=IF(status IN ('stopped','completed'),next_followup_date,NULL),"
                        "scheduled_followup_reason=IF(status IN ('stopped','completed'),scheduled_followup_reason,NULL),"
                        "status=IF(status IN ('stopped','completed'),status,'paused')",
                        (deal_id, now))
                    return "paused"
        return "unchanged"

    @staticmethod
    def _set_handoff(cursor, deal_id, reason):
        cursor.execute(
            "UPDATE sasha_deal_status SET status='handoff_needed',status_reason=%s WHERE deal_id=%s",
            (reason, deal_id))
        # A person owns the deal now: Sasha's follow-ups stop, as in v3.
        cursor.execute(
            "UPDATE sasha_followup_schedule SET status='stopped',stop_reason='handoff',"
            "next_followup_date=NULL WHERE deal_id=%s", (deal_id,))

    def claim_due_followups(self, now=None, limit: int = 50) -> list[int]:
        """Deals whose follow-up day has arrived and that Sasha may contact. Claimed in the same
        transaction (the date is cleared, the planned reason kept) so a follow-up never starts twice."""
        now = now or utc_now()
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT f.deal_id FROM sasha_followup_schedule f "
                    "LEFT JOIN sasha_deal_status s ON s.deal_id=f.deal_id AND s.deleted_at IS NULL "
                    "WHERE f.status='active' AND f.next_followup_date<=%s "
                    "AND COALESCE(s.status,'active') NOT IN ('opt_out','handoff_needed') "
                    "ORDER BY f.next_followup_date LIMIT %s FOR UPDATE", (now, limit))
                deal_ids = [int(row["deal_id"]) for row in cursor.fetchall()]
                if deal_ids:
                    placeholders = ",".join(["%s"] * len(deal_ids))
                    cursor.execute(f"UPDATE sasha_followup_schedule SET next_followup_date=NULL "
                                   f"WHERE deal_id IN ({placeholders})", deal_ids)
        return deal_ids

    def record_followup_sent(self, deal_id: str, channel: str = "email") -> None:
        """A follow-up draft was generated (v3 update_after_send)."""
        if channel not in {"email", "sms", "both"}:
            raise ValueError("Invalid follow-up channel")
        last_channel = None if channel == "both" else channel
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE sasha_followup_schedule SET followup_count=followup_count+1,"
                    "last_followup_timestamp=%s,last_channel_used=%s WHERE deal_id=%s",
                    (utc_now(), last_channel, validate_deal_id(deal_id)))

    def pause_followups(self, deal_id: str, reason: str) -> None:
        """No next follow-up was chosen: pause, keeping the reason, unless already stopped."""
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE sasha_followup_schedule SET next_followup_date=NULL,scheduled_followup_reason=%s,"
                    "status='paused' WHERE deal_id=%s AND status='active' AND next_followup_date IS NULL",
                    (reason, validate_deal_id(deal_id)))

    def client_contact(self, deal_id: str) -> dict:
        """The deal's client id and email, for assigning a manager and the transition email."""
        main = quote_schema(self.settings.values["DEAL_MANAGER_MAIN_SCHEMA"])
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT u.id AS client_id,u.email FROM {main}.deals d JOIN {main}.user u "
                    "ON u.id=d.client_id WHERE d.id=%s AND d.deleted_at IS NULL AND u.deleted_at IS NULL",
                    (validate_deal_id(deal_id),))
                row = cursor.fetchone()
        if not row:
            raise ValueError("The deal or its client was not found")
        return {"client_id": int(row["client_id"]), "email": str(row["email"] or "").strip()}

    def latest_thread(self, deal_id: str) -> dict:
        """The client's latest email thread on this deal: {external_thread_id, subject}."""
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT external_thread_id,subject FROM sasha_conversation_history "
                    "WHERE deal_id=%s AND message_type='EMAIL' AND convo_type<>'internal_mail' "
                    "AND deleted_at IS NULL AND (external_thread_id IS NOT NULL OR subject IS NOT NULL) "
                    "ORDER BY COALESCE(message_timestamp,created_at) DESC,id DESC LIMIT 1",
                    (validate_deal_id(deal_id),))
                row = cursor.fetchone()
        return {"external_thread_id": row["external_thread_id"], "subject": row["subject"]} if row \
            else {"external_thread_id": None, "subject": None}

    def record_outbound(self, deal_id: str, run_id: str, convo_type: str, body_html: str,
                        subject: str | None = None, delivery_status: str = "generated",
                        external_thread_id: str | None = None) -> None:
        """An outbound message produced outside an agent turn, such as the handoff transition."""
        deal_id = validate_deal_id(deal_id)
        validate_message_body(body_html)
        if convo_type not in CONVERSATION_TYPES or delivery_status not in {"generated", "sent", "failed"}:
            raise ValueError("Invalid outbound message metadata")
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO sasha_conversation_history "
                    "(deal_id,run_id,convo_type,direction,sender_name,message_type,subject,body,"
                    "content_type,delivery_status,external_thread_id,message_timestamp) "
                    "VALUES (%s,%s,%s,'OUTBOUND',%s,'EMAIL',%s,%s,'text/html',%s,%s,%s)",
                    (deal_id, run_id, convo_type, self.sender_email(), subject, body_html,
                     delivery_status, external_thread_id, utc_now()))

    def complete_run(self, deal_id, run_id, previous_notes, notes, message_html, convo_type,
                     handoff_reason=None, deal_state=None, sms_messages=None):
        deal_id = validate_deal_id(deal_id)
        drafts = draft_rows(message_html, [] if sms_messages is None else sms_messages)
        if handoff_reason is not None:
            handoff_reason = validate_handoff_reason(handoff_reason)
        if deal_state is not None:
            deal_state = validate_deal_state(deal_state)
        if drafts and convo_type not in CONVERSATION_TYPES:
            raise ValueError("A generated draft requires a valid convo_type")
        sender = self.sender_email() if message_html.strip() else None
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                self._lock_deal(cursor, deal_id)
                current_notes = self._notes(cursor, deal_id)
                cursor.execute(
                    "SELECT body,convo_type,message_type,content_type FROM sasha_conversation_history "
                    "WHERE deal_id=%s AND run_id=%s AND direction='OUTBOUND' AND deleted_at IS NULL",
                    (deal_id, run_id))
                existing = cursor.fetchall()
                if existing:
                    if same_drafts(existing, drafts, convo_type) and current_notes == notes:
                        return
                    raise ValueError("This run already saved a different result")
                if current_notes != previous_notes:
                    raise ValueError("Deal notes changed during this run; rerun with current context")
                cursor.execute(
                    "INSERT INTO sasha_deal_notes (deal_id,notes) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE notes=VALUES(notes),deleted_at=NULL", (deal_id, notes))
                if handoff_reason is not None:
                    self._set_handoff(cursor, deal_id, handoff_reason)
                elif deal_state is not None:
                    cursor.execute(
                        "UPDATE sasha_deal_status SET status=%s,status_reason=%s "
                        "WHERE deal_id=%s AND status<>'handoff_needed'",
                        (deal_state["status"], deal_state["reason"], deal_id))
                for draft in drafts:
                    cursor.execute(
                        "INSERT INTO sasha_conversation_history "
                        "(deal_id,run_id,convo_type,direction,sender_name,message_type,body,"
                        "content_type,delivery_status,message_timestamp) "
                        "VALUES (%s,%s,%s,'OUTBOUND',%s,%s,%s,%s,'generated',%s)",
                        (deal_id, run_id, convo_type,
                         sender if draft["message_type"] == "EMAIL" else "Sasha",
                         draft["message_type"], draft["body"], draft["content_type"], utc_now()))
