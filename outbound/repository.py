"""Recipient lookup and email-thread correlation; FreshFlow owns delivery."""

import re

from .models import email_trace_id

from sasha_database.config import quote_schema
from sasha_database.messages import utc_now
from sasha_database.storage import validate_deal_id


class CallbackError(ValueError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class OutboundRepository:
    def __init__(self, storage):
        self.storage = storage

    def transaction(self, readonly=False):
        return self.storage.transaction(self.storage.settings, readonly=readonly)

    def contact(self, deal_id):
        main = quote_schema(self.storage.settings.values["DEAL_MANAGER_MAIN_SCHEMA"])
        with self.transaction(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT u.email,u.phone,u.first_name,u.last_name,COALESCE(u.account_id,d.account_id) AS account_id "
                    f"FROM {main}.deals d LEFT JOIN {main}.user u ON u.id=d.client_id AND u.deleted_at IS NULL "
                    "WHERE d.id=%s AND d.deleted_at IS NULL", (validate_deal_id(deal_id),))
                row = cursor.fetchone()
        if row is None:
            raise ValueError("The outbound deal was not found")
        return {key: (value.strip() if isinstance(value, str) else value) for key, value in row.items()}

    def client_thread(self, deal_id, thread_id=None):
        thread_filter = "AND external_thread_id=%s " if thread_id else ""
        parameters = (validate_deal_id(deal_id), thread_id) if thread_id else (validate_deal_id(deal_id),)
        with self.transaction(readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT external_thread_id,subject FROM sasha_conversation_history "
                    "WHERE deal_id=%s AND direction='OUTBOUND' AND message_type='EMAIL' "
                    "AND deleted_at IS NULL AND external_thread_id IS NOT NULL "
                    "AND TRIM(external_thread_id)<>'' "
                    + thread_filter +
                    "ORDER BY COALESCE(message_timestamp,created_at) DESC,id DESC LIMIT 1",
                    parameters)
                latest = cursor.fetchone()
                if not latest:
                    if thread_id:
                        return {}
                    cursor.execute(
                        "SELECT subject FROM sasha_conversation_history "
                        "WHERE deal_id=%s AND direction='OUTBOUND' AND message_type='EMAIL' "
                        "AND deleted_at IS NULL AND subject IS NOT NULL AND TRIM(subject)<>'' "
                        "ORDER BY COALESCE(message_timestamp,created_at) DESC,id DESC LIMIT 1",
                        (validate_deal_id(deal_id),))
                    saved = cursor.fetchone()
                    return {"thread_id": None, "subject": saved["subject"]} if saved else {}
                cursor.execute(
                    "SELECT subject FROM sasha_conversation_history WHERE deal_id=%s "
                    "AND direction='OUTBOUND' AND message_type='EMAIL' AND deleted_at IS NULL "
                    "AND external_thread_id=%s AND subject IS NOT NULL AND subject<>'' "
                    "ORDER BY COALESCE(message_timestamp,created_at),id LIMIT 1",
                    (validate_deal_id(deal_id), latest["external_thread_id"]))
                earliest = cursor.fetchone()
        return {"thread_id": latest["external_thread_id"], "subject": (earliest or latest)["subject"]}

    def prepare_saved_emails(self, cursor, messages, history_ids):
        for message in messages:
            if message.channel == "EMAIL":
                self._prepare_saved_email(cursor, message, history_ids["EMAIL"])

    @staticmethod
    def _prepare_saved_email(cursor, message, history_id):
        message.trace_id = email_trace_id("history", history_id)
        message.payload["traceId"] = message.trace_id
        draft = message.payload["payload"]["workflow_result"]["result"]["draft"]
        cursor.execute("UPDATE sasha_conversation_history SET subject=%s,"
                       "external_thread_id=COALESCE(external_thread_id,%s) WHERE id=%s AND deal_id=%s",
                       (draft["email_subject"], draft["thread_id"], history_id, message.deal_id))

    def store_thread_id(self, callback):
        deal_id = validate_deal_id(callback["deal_id"])
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                self.storage._lock_deal(cursor, deal_id)
                row = self._callback_row(cursor, deal_id, callback)
                return self._store_callback(cursor, row, callback)

    def _callback_row(self, cursor, deal_id, callback):
        offer = callback.get("assignment_type") == "handoff_offer"
        record_id = self._callback_record_id(callback.get("trace_id"), "offer" if offer else "history")
        rows = self._offer_rows(cursor, deal_id, record_id) if offer else self._history_rows(cursor, deal_id, record_id)
        if record_id is None:
            duplicates = [row for row in rows if callback.get("message_id")
                          and row["external_message_id"] == callback["message_id"]]
            if offer:
                duplicates = [row for row in rows if row["external_thread_id"] == callback["thread_id"]]
            if duplicates:
                rows = duplicates
        if not rows:
            raise CallbackError(404, "No matching outbound email")
        if len(rows) != 1:
            raise CallbackError(409, "Ambiguous callback; supply the original trace_id")
        return rows[0]

    @staticmethod
    def _callback_record_id(trace_id, record_type):
        if not trace_id:
            return None
        match = re.fullmatch(r"sasha-poc-(history|offer)-([1-9][0-9]{0,18})", trace_id)
        if not match or match[1] != record_type:
            raise CallbackError(422, "trace_id does not match this callback type")
        record_id = int(match[2])
        if record_id > 9223372036854775807:
            raise CallbackError(422, "Invalid callback record ID")
        return record_id

    @staticmethod
    def _history_rows(cursor, deal_id, record_id):
        parameters = [deal_id]
        clause = ""
        if record_id is not None:
            clause = " AND id=%s"
            parameters.append(record_id)
        cursor.execute("SELECT deal_id,id AS history_id,NULL AS offer_id,external_thread_id,external_message_id "
                       "FROM sasha_conversation_history WHERE deal_id=%s AND direction='OUTBOUND' "
                       "AND message_type='EMAIL' AND deleted_at IS NULL" + clause + " FOR UPDATE", tuple(parameters))
        return list(cursor.fetchall())

    @staticmethod
    def _offer_rows(cursor, deal_id, record_id):
        parameters = [deal_id]
        clause = ""
        if record_id is not None:
            clause = " AND id=%s"
            parameters.append(record_id)
        cursor.execute("SELECT deal_id,id AS offer_id,NULL AS history_id,thread_id AS external_thread_id,"
                       "NULL AS external_message_id FROM sasha_handoff_offers WHERE deal_id=%s" + clause + " FOR UPDATE",
                       tuple(parameters))
        return list(cursor.fetchall())

    def _store_callback(self, cursor, row, callback):
        thread_id = callback["thread_id"]
        message_id = callback.get("message_id") if row["history_id"] else None
        if row["external_thread_id"] and row["external_thread_id"] != thread_id:
            raise CallbackError(409, "Conflicting email thread ID")
        if row["external_message_id"] and message_id and row["external_message_id"] != message_id:
            raise CallbackError(409, "Conflicting email message ID")
        unchanged = row["external_thread_id"] == thread_id and (not message_id or row["external_message_id"] == message_id)
        if row["offer_id"]:
            self._update_offer_thread(cursor, row["offer_id"], thread_id)
        else:
            self._update_history_thread(cursor, row["history_id"], thread_id, message_id)
        return {"status": "unchanged" if unchanged else "updated", "deal_id": str(row["deal_id"]),
                "external_thread_id": thread_id, "external_message_id": message_id or row["external_message_id"],
                "communication_history_id": row["history_id"], "offer_id": row["offer_id"]}

    @staticmethod
    def _update_offer_thread(cursor, offer_id, thread_id):
        cursor.execute("UPDATE sasha_handoff_offers SET thread_id=%s,updated_at=%s WHERE id=%s",
                       (thread_id, utc_now(), offer_id))

    @staticmethod
    def _update_history_thread(cursor, history_id, thread_id, message_id):
        cursor.execute("UPDATE sasha_conversation_history SET external_thread_id=%s,"
                       "external_message_id=COALESCE(%s,external_message_id) WHERE id=%s",
                       (thread_id, message_id, history_id))
