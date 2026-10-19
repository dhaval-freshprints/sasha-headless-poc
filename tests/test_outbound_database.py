"""Real transaction tests, restricted to a disposable loopback MySQL database."""

from dataclasses import replace
import os
import unittest
from unittest.mock import patch

import pymysql

from account_manager_agent.handoff.service import HandoffService
from outbound.repository import CallbackError
from outbound.service import OutboundService
from outbound.transport import PublishError
from sasha_database.config import DatabaseSettings
from sasha_database.storage import SashaStorage
from tests.test_outbound import SETTINGS, task, result
from tests.test_sasha_database import TABLES


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.error = None
        self.on_publish = None

    def publish(self, message):
        self.calls.append(message)
        if self.on_publish:
            self.on_publish(message)
        if self.error:
            raise self.error
        return "sqs-1"


@unittest.skipUnless(os.environ.get("SASHA_TEST_MYSQL_PORT"), "Requires disposable local MySQL")
class OutboundDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin = pymysql.connect(host="127.0.0.1", port=int(os.environ["SASHA_TEST_MYSQL_PORT"]),
                                   user="root", password="", autocommit=True, cursorclass=pymysql.cursors.DictCursor)
        with cls.admin.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS sasha_outbound_test")
            cursor.execute("USE sasha_outbound_test")
            for statement in TABLES:
                cursor.execute(statement)
            for table, additions in {
                "user": {"phone": "VARCHAR(32)", "first_name": "VARCHAR(64)", "last_name": "VARCHAR(64)", "account_id": "INT"},
                "deals": {"account_id": "INT"},
            }.items():
                cursor.execute(f"SHOW COLUMNS FROM {table}")
                existing = {row["Field"] for row in cursor.fetchall()}
                for name, definition in additions.items():
                    if name not in existing:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        cls.settings = DatabaseSettings({"NON_PROD_HOST": "127.0.0.1", "NON_PROD_PORT": os.environ["SASHA_TEST_MYSQL_PORT"],
                                        "NON_PROD_USER": "root", "NON_PROD_PASS": "", "DEAL_MANAGER_MAIN_SCHEMA": "sasha_outbound_test",
                                        "DEAL_MANAGER_OPERATIONS_SCHEMA": "sasha_outbound_test"})

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def setUp(self):
        with self.admin.cursor() as cursor:
            for table in ("sasha_followup_schedule", "sasha_handoff_offers", "sasha_handoff_isas",
                          "sasha_conversation_history", "sasha_deal_notes", "sasha_deal_status", "deals", "user"):
                cursor.execute(f"DELETE FROM {table}")
            cursor.execute("INSERT INTO user (id,email,phone,first_name,last_name,account_id) VALUES "
                           "(7,'client@example.com','4155550100','Casey','Test',12)")
            cursor.execute("INSERT INTO deals (id,client_id,account_id) VALUES (42,7,10)")
        environment = patch.dict(os.environ, {"SASHA_SENDER_EMAIL": SETTINGS.sender, "SASHA_OUTBOUND_ENABLED": "false"})
        environment.start()
        self.addCleanup(environment.stop)
        self.storage = SashaStorage(self.settings)
        self.storage.prepare_run("42", "run-42")
        self.transport = FakeTransport()
        self.service = OutboundService(self.storage, SETTINGS, transport=self.transport, sleep=lambda seconds: None)

    def emails(self):
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT * FROM sasha_conversation_history WHERE deal_id=42 AND direction='OUTBOUND' "
                           "AND message_type='EMAIL' ORDER BY id")
            return [{**row, "history_id": row["id"], "trace_id": f"sasha-poc-history-{row['id']}"}
                    for row in cursor.fetchall()]

    def submit(self, value=None):
        return self.service.submit_result(task(), value or result(), previous_notes="", notes="Notes")

    def callback(self, row, **changes):
        return self.service.store_thread_id({"deal_id": "42", "thread_id": "thread-1", "trace_id": row["trace_id"], **changes})

    def test_hands_over_both_channels_using_existing_history(self):
        final, handoff = self.submit()
        self.assertEqual(handoff["status"], "submitted")
        self.assertEqual([row["payload"]["type"] for row in self.transport.calls],
                         ["sasha-email-trigger", "send-text-message"])
        self.assertEqual(len(self.emails()), 1)
        self.assertEqual(self.transport.calls[0]["payload"]["traceId"], self.emails()[0]["trace_id"])
        history = self.storage.load_context("42")[1]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["subject"], final.email_subject)
        self.assertEqual(history[0]["delivery_status"], "generated")

    def test_metadata_failure_rolls_back_draft_and_notes_without_publishing(self):
        original = self.service.repository.prepare_saved_emails
        def fail(cursor, messages, ids):
            original(cursor, messages, ids)
            raise ValueError("forced rollback")
        with patch.object(self.service.repository, "prepare_saved_emails", side_effect=fail), self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.emails(), [])
        self.assertEqual(self.storage.load_context("42"), ("", []))
        self.assertEqual(self.transport.calls, [])

    def test_submission_error_is_reported_once_without_a_retry(self):
        self.transport.error = PublishError("RequestThrottled")
        final, handoff = self.submit(result("email"))
        self.assertEqual(final.status, "completed")
        self.assertEqual(handoff["status"], "outbound_failed")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(len(self.emails()), 1)

    def test_fast_callback_matches_before_sqs_returns(self):
        self.transport.on_publish = lambda message: self.callback(
            {"trace_id": message["payload"]["traceId"]}, message_id="gmail-1")
        _, handoff = self.submit(result("email"))
        self.assertEqual(handoff["status"], "submitted")
        row = self.emails()[0]
        self.assertEqual((row["external_thread_id"], row["external_message_id"]), ("thread-1", "gmail-1"))
        self.assertEqual(self.callback(row, message_id="gmail-1")["status"], "unchanged")
        self.assertEqual(len(self.transport.calls), 1)

    def test_callback_saves_thread_without_delivery_tracking(self):
        self.submit(result("email"))
        row = self.emails()[0]
        self.callback(row)
        self.assertEqual(self.callback(row, message_id="gmail-1")["status"], "updated")
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT * FROM sasha_conversation_history WHERE id=%s", (row["history_id"],))
            history = cursor.fetchone()
        self.assertEqual(history["external_thread_id"], "thread-1")
        self.assertEqual(history["external_message_id"], "gmail-1")
        self.assertEqual(history["delivery_status"], "generated")
        for changes in ({"thread_id": "wrong"}, {"message_id": "wrong"}):
            with self.assertRaises(CallbackError) as raised:
                self.callback(row, **changes)
            self.assertEqual(raised.exception.status_code, 409)

    def test_legacy_callback_rejects_ambiguity_but_accepts_known_duplicate(self):
        self.submit(result("email"))
        self.service.store_thread_id({"deal_id": "42", "thread_id": "thread-1", "message_id": "gmail-1"})
        self.service.submit_result(replace(task(), run_id="next-run"), result("email"),
                                   previous_notes="Notes", notes="Notes 2")
        with self.assertRaises(CallbackError) as raised:
            self.service.store_thread_id({"deal_id": "42", "thread_id": "thread-1"})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.service.store_thread_id(
            {"deal_id": "42", "thread_id": "thread-1", "message_id": "gmail-1"})["status"], "unchanged")

    def test_no_reply_saves_notes_without_submitting_messages(self):
        _, handoff = self.submit(result("none"))
        self.assertEqual(handoff["status"], "none")
        self.assertEqual(self.emails(), [])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.storage.load_context("42"), ("Notes", []))

    def test_followup_uses_the_callback_thread(self):
        self.submit(result("email", convo_type="outreach"))
        self.callback(self.emails()[0], message_id="first-message")
        final, _ = self.service.submit_result(replace(task(), run_id="followup"),
                                              result("email", convo_type="followup", email_subject="New subject"),
                                              previous_notes="Notes", notes="Followup notes")
        self.assertEqual(final.email_subject, "Re: Fresh Prints here to help!")
        draft = self.transport.calls[-1]["payload"]["payload"]["workflow_result"]["result"]["draft"]
        self.assertEqual(draft["thread_id"], "thread-1")

    def test_new_thread_reuses_saved_subject_before_callback(self):
        self.submit(result("email", convo_type="outreach"))
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_conversation_history SET subject=%s WHERE deal_id=42 AND message_type='EMAIL'",
                           ("Re: Custom existing subject",))
        final, _ = self.service.submit_result(replace(task(), run_id="new-thread"),
                                              result("email", convo_type="client_response"),
                                              previous_notes="Notes", notes="Reply notes")
        self.assertEqual(final.email_subject, "Re: Custom existing subject")
        draft = self.transport.calls[-1]["payload"]["payload"]["workflow_result"]["result"]["draft"]
        self.assertIsNone(draft["thread_id"])
        self.assertEqual(draft["email_subject"], final.email_subject)
        self.assertEqual(self.emails()[-1]["subject"], final.email_subject)

    def test_subject_lookup_stays_in_requested_thread(self):
        self.submit(result("email", convo_type="outreach"))
        self.callback(self.emails()[0], message_id="first-message")
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_conversation_history SET subject=%s WHERE external_thread_id=%s",
                           ("Earlier thread subject", "thread-1"))
        self.service.submit_result(replace(task(), run_id="second-thread"),
                                   result("email", convo_type="outreach"), previous_notes="Notes", notes="Notes")
        self.callback(self.emails()[-1], thread_id="thread-2", message_id="second-message")
        self.assertEqual(self.service.repository.client_thread("42")["subject"], "Fresh Prints here to help!")
        self.assertEqual(self.service.repository.client_thread("42", "thread-1")["subject"], "Earlier thread subject")

    def prepare_handoff(self):
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_deal_status SET status='handoff_needed',status_reason='Needs a person' WHERE deal_id=42")
            cursor.execute("INSERT INTO sasha_handoff_isas (id,user_id,email,first_name,last_name,rank_order,is_active) "
                           "VALUES (1,100,'isa@example.com','Alex','Test',1,1),(2,101,'next@example.com','Next','Test',2,1)")
        return HandoffService(self.storage, outbound=self.service, crm_factory=lambda: None)

    def test_handoff_offer_and_transition_have_separate_threads(self):
        handoffs = self.prepare_handoff()
        first = handoffs.offer_next("42")
        self.assertEqual(first["outbound"]["status"], "submitted")
        self.service.store_thread_id({"deal_id": "42", "thread_id": "isa-thread", "assignment_type": "handoff_offer",
                                     "trace_id": self.transport.calls[0]["payload"]["traceId"]})
        self.assertEqual(handoffs.offers.offer(first["offer_id"]).thread_id, "isa-thread")
        self.assertEqual(self.storage.load_context("42")[1], [])
        accepted = handoffs.apply_isa_decision(first["offer_id"], "accepted")
        self.assertEqual(accepted["outbound"]["status"], "submitted")
        draft = self.transport.calls[-1]["payload"]["payload"]["workflow_result"]["result"]["draft"]
        self.assertIsNone(draft["thread_id"])
        self.assertEqual(draft["cc_email"], ["isa@example.com"])
        self.assertFalse(handoffs.apply_isa_decision(first["offer_id"], "accepted")["accepted"])
        self.assertEqual(len(self.transport.calls), 2)

    def test_rejection_sends_next_offer_without_delivery_monitoring(self):
        handoffs = self.prepare_handoff()
        first = handoffs.offer_next("42")
        rejected = handoffs.apply_isa_decision(first["offer_id"], "rejected")
        self.assertEqual(rejected["next_offer"]["outbound"]["status"], "submitted")
        with self.assertRaises(CallbackError) as raised:
            self.service.store_thread_id({"deal_id": "42", "thread_id": "late-first", "assignment_type": "handoff_offer"})
        self.assertEqual(raised.exception.status_code, 409)
        handoffs.offer_next("42")
        self.assertEqual(len(self.transport.calls), 2)

    def test_callback_updates_only_the_identified_email(self):
        self.submit(result("email"))
        first = self.emails()[0]
        self.service.submit_result(replace(task(), run_id="another-email"), result("email"),
                                   previous_notes="Notes", notes="More notes")
        second = self.emails()[1]
        self.callback(second, thread_id="second-thread", message_id="second-message")
        self.assertIsNone(self.emails()[0]["external_thread_id"])
        self.callback(first, thread_id="first-thread", message_id="first-message")
        self.assertEqual([row["external_thread_id"] for row in self.emails()], ["first-thread", "second-thread"])

    def test_callback_rejects_wrong_deal_sms_and_invalid_trace(self):
        self.submit()
        email = self.emails()[0]
        with self.admin.cursor() as cursor:
            cursor.execute("SELECT id FROM sasha_conversation_history WHERE message_type='SMS'")
            sms_id = cursor.fetchone()["id"]
        for changes, status in [
            ({"deal_id": "43"}, 404),
            ({"trace_id": f"sasha-poc-history-{sms_id}"}, 404),
            ({"trace_id": "sasha-poc-history-0"}, 422),
            ({"trace_id": "unknown"}, 422),
            ({"assignment_type": "handoff_offer"}, 422),
        ]:
            with self.subTest(changes=changes), self.assertRaises(CallbackError) as raised:
                self.callback(email, **changes)
            self.assertEqual(raised.exception.status_code, status)
        self.assertIsNone(self.emails()[0]["external_thread_id"])

    def test_deleted_history_cannot_receive_callback(self):
        self.submit(result("email"))
        email = self.emails()[0]
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_conversation_history SET deleted_at=UTC_TIMESTAMP() WHERE id=%s", (email["id"],))
        with self.assertRaises(CallbackError) as raised:
            self.callback(email)
        self.assertEqual(raised.exception.status_code, 404)
