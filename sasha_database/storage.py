"""Host-owned persistence for Sasha status, notes, and conversations."""

from dataclasses import replace
import json

from .config import DatabaseSettings, quote_schema, required
from .connection import transaction
from .messages import CONVERSATION_TYPES, IncomingMessage, utc_now, valid_email, validate_message_body


def validate_deal_id(deal_id: str) -> int:
    if not str(deal_id).isascii() or not str(deal_id).isdigit() or not 0 < int(deal_id) <= 2147483647:
        raise ValueError("deal_id must be a positive database INT")
    return int(deal_id)


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
        return {
            "id": row["id"], "role": "sasha" if row["direction"] == "OUTBOUND" else "client",
            "sender_name": row["sender_name"], "message_type": row["message_type"],
            "subject": row["subject"], "convo_type": row["convo_type"],
            "content": row["body"], "content_type": row["content_type"],
            "delivery_status": row["delivery_status"], "date": timestamp.date().isoformat(),
            "attachments": json.loads(row["attachments"]) if isinstance(row["attachments"], str) else row["attachments"],
        }

    @staticmethod
    def sender_email():
        email = required("SASHA_SENDER_EMAIL")
        if not valid_email(email) or len(email) > 255:
            raise ValueError("SASHA_SENDER_EMAIL must be a valid email address")
        return email

    def complete_run(self, deal_id, run_id, previous_notes, notes, message_html, convo_type):
        deal_id = validate_deal_id(deal_id)
        validate_message_body(message_html)
        if message_html.strip() and convo_type not in CONVERSATION_TYPES:
            raise ValueError("A generated draft requires a valid convo_type")
        sender = self.sender_email() if message_html.strip() else None
        with self.transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                self._lock_deal(cursor, deal_id)
                current_notes = self._notes(cursor, deal_id)
                cursor.execute(
                    "SELECT body,convo_type FROM sasha_conversation_history "
                    "WHERE deal_id=%s AND run_id=%s AND direction='OUTBOUND' AND deleted_at IS NULL",
                    (deal_id, run_id))
                existing = cursor.fetchone()
                if existing:
                    if (existing["body"] == message_html and existing["convo_type"] == convo_type
                            and current_notes == notes):
                        return
                    raise ValueError("This run already saved a different result")
                if current_notes != previous_notes:
                    raise ValueError("Deal notes changed during this run; rerun with current context")
                cursor.execute(
                    "INSERT INTO sasha_deal_notes (deal_id,notes) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE notes=VALUES(notes),deleted_at=NULL", (deal_id, notes))
                if message_html.strip():
                    cursor.execute(
                        "INSERT INTO sasha_conversation_history "
                        "(deal_id,run_id,convo_type,direction,sender_name,message_type,body,"
                        "content_type,delivery_status,message_timestamp) "
                        "VALUES (%s,%s,%s,'OUTBOUND',%s,'EMAIL',%s,'text/html','generated',%s)",
                        (deal_id, run_id, convo_type, sender, message_html, utc_now()))
