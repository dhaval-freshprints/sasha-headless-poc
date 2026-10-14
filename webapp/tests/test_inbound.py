"""Webhook -> code lookup -> (Deal Manager) -> validation -> Account Manager, all faked."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from deal_manager_agent.deal_lookup import DealSearch
from deal_manager_agent.deal_resolution import Resolution
from deal_manager_agent.models import LookupResult
from webapp.app import create_app
from webapp.deal_manager_jobs import DealManagerJobs
from webapp.deals import DealStore
from webapp.inbound import FINAL_STATUSES, InboundJobs, InboundRequest
from webapp.jobs import JobManager
from webapp.tests.helpers import FakeRunner, PassThroughResolver


def agent_result(**changes):
    value = {
        "status": "matched", "deal_id": "500", "deal_title": "Agent deal", "confidence": "high",
        "reason": "Verified proof relationship",
        "candidate_deals": [{"deal_id": "500", "supports": ["Proof"], "conflicts": []}],
        "evidence": [{"query_id": "Q1", "table": "main.deals", "primary_key": "500",
                      "fields": [{"name": "deleted_at", "value": "null"}], "interpretation": "Verified"}],
        "limitations": [], "next_check": None, **changes,
    }
    return LookupResult.model_validate(value, context={"main_schema": "main"})


def wait_for(read, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value["status"] in FINAL_STATUSES:
            return value
        time.sleep(0.01)
    raise AssertionError("Inbound message did not finish")


EMAIL = {"email": "Client@Example.com", "subject": "Proof", "body": "Where is my proof?",
         "thread_id": "t-1"}
SMS = {"phone": "+14155550100", "body": ["Hi", "Is my order ready?"], "thread_id": "t-2"}


class InboundRequestTests(unittest.TestCase):
    def test_phone_requires_an_array_and_email_requires_a_string(self):
        for payload in ({"phone": "+14155550100", "body": "Hi"}, {"email": "a@b.co", "body": ["Hi"]},
                        {"body": "Hi"}, {"email": "a@b.co", "body": "  "},
                        {"phone": "+14155550100", "body": [" ", ""]}, {"phone": "12345", "body": ["Hi"]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                InboundRequest.model_validate(payload)

    def test_blank_fields_are_treated_as_missing_and_national_phone_is_us(self):
        request = InboundRequest.model_validate(
            {"email": "", "phone": "(415) 555-0100", "body": ["Hi"], "thread_id": " "})
        lookup = request.to_lookup()
        self.assertEqual((lookup.sender_phone, lookup.thread_id, lookup.channel), ("+14155550100", None, "SMS"))

    def test_client_message_uses_the_deals_form_envelope(self):
        self.assertEqual(InboundRequest.model_validate(SMS).client_message(),
                         "Channel: SMS\nFrom: +14155550100\n\nHi\nIs my order ready?")
        self.assertEqual(InboundRequest.model_validate(EMAIL).client_message(),
                         "Channel: Email\nFrom: Client@Example.com\nSubject: Proof\n\nWhere is my proof?")


class InboundWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.finder = Mock()
        self.finder.find.return_value = DealSearch("found", "Only active deal", "300")
        self.agent = Mock(usage=None)
        self.agent.run.return_value = agent_result()
        self.lookups = DealManagerJobs(root / "lookups", lambda write: self.agent)
        self.release = threading.Event()  # Cleared, a fake Account Manager run stays open ~2 s.
        self.release.set()
        self.sales = JobManager(root / "sales", lambda write: FakeRunner(write, root, self.release))
        self.deals = DealStore(root / "deals")
        self.resolver = Mock()
        self.resolver.resolve.side_effect = lambda deal_id: Resolution("proceed", "single_active_deal", deal_id)
        self.inbound = InboundJobs(
            root / "inbound", self.finder, self.lookups,
            self.start_run, self.resolver,
            send_message=self.sales.submit_message,
            run_state=lambda run_id: self.sales.get(run_id).to_dict(), retry_seconds=0.02,
        )
        self.runs = []
        app = create_app(self.sales, self.deals, self.lookups, self.inbound, PassThroughResolver())
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.root = root

    def start_run(self, deal_id, message, deal_check=None):
        self.runs.append((deal_id, message))
        self.deals.touch(deal_id)
        return self.sales.submit(deal_id, message, []).to_dict()

    def post(self, payload, **headers):
        return self.client.post("/api/inbound", json=payload, headers=headers)

    def finished(self, response):
        inbound_id = response.json()["inbound_id"]
        return wait_for(lambda: self.client.get(f"/api/inbound/{inbound_id}").json())

    def test_deal_found_by_code_starts_sasha_without_running_the_agent(self):
        response = self.post(EMAIL)
        self.assertEqual(response.status_code, 202)
        record = self.finished(response)
        self.assertEqual((record["status"], record["deal_id"], record["resolved_by"]),
                         ("run_started", "300", "code"))
        self.agent.run.assert_not_called()
        self.assertEqual(self.runs[0][0], "300")
        run = self.sales.get(record["run_id"])
        self.assertTrue(run.client_message.startswith("Channel: Email\n"))
        self.resolver.resolve.assert_called_once_with("300")
        self.assertIn("Where is my proof?", run.client_message)
        self.assertEqual(self.finder.find.call_args.args[0].sender_email, "client@example.com")

    def test_sms_array_reaches_finder_and_sasha_as_ordered_text(self):
        record = self.finished(self.post(SMS))
        lookup = self.finder.find.call_args.args[0]
        self.assertEqual((lookup.channel, lookup.sender_phone, lookup.thread_id), ("SMS", "+14155550100", "t-2"))
        self.assertEqual(self.runs[0], ("300", "Channel: SMS\nFrom: +14155550100\n\nHi\nIs my order ready?"))
        self.assertEqual(record["status"], "run_started")

    def test_no_code_match_runs_agent_and_a_verified_match_starts_sasha(self):
        for outcome in ("none", "multiple", "error"):
            with self.subTest(outcome=outcome):
                self.runs.clear()
                self.finder.find.return_value = DealSearch(outcome, "x")
                # Its own thread: the same thread would join the previous, still-open conversation.
                record = self.finished(self.post({**EMAIL, "message_id": f"m-{outcome}",
                                                  "thread_id": f"t-{outcome}"}))
                self.assertEqual((record["status"], record["deal_id"], record["resolved_by"]),
                                 ("run_started", "500", "agent"))
                self.assertEqual(self.runs[0][0], "500")
                self.assertTrue(record["lookup_id"])
                self.assertEqual(record["deal_search"]["outcome"], outcome)

    def test_unresolved_agent_outcomes_never_start_sasha(self):
        self.finder.find.return_value = DealSearch("none", "x")
        cases = {
            "needs_review": dict(status="needs_review", deal_id=None, deal_title=None, confidence="low"),
            "no_match": dict(status="no_match", deal_id=None, deal_title=None, confidence="none"),
        }
        for status, changes in cases.items():
            with self.subTest(status=status):
                self.agent.run.return_value = agent_result(**changes)
                record = self.finished(self.post({**EMAIL, "message_id": status}))
                self.assertEqual(record["status"], status)
                self.assertIsNone(record["run_id"])
        self.assertEqual(self.runs, [])

    def test_agent_failure_is_recorded_as_failed(self):
        self.finder.find.return_value = DealSearch("none", "x")
        self.agent.run.side_effect = RuntimeError("executor down")
        record = self.finished(self.post(EMAIL))
        self.assertEqual(record["status"], "failed")
        self.assertIn("executor down", record["error"])
        self.assertEqual(self.runs, [])

    def test_message_id_makes_the_webhook_idempotent(self):
        first = self.post({**EMAIL, "message_id": "provider-1"}).json()
        second = self.post({**EMAIL, "message_id": "provider-1"}).json()
        self.assertEqual(first["inbound_id"], second["inbound_id"])
        self.finished(self.post({**EMAIL, "message_id": "provider-1"}))
        self.assertEqual(len(self.runs), 1)

    def test_both_sections_are_linked_and_served(self):
        page = self.client.get("/client-response")
        self.assertIn("New client message", page.text)
        self.assertIn("Find deal and open Account Manager", page.text)
        self.assertEqual(self.client.get("/client-response/" + "a" * 32).status_code, 200)
        self.assertEqual(self.client.get("/static/client_response.js").status_code, 200)
        for path in ("/", "/client-response", "/deal-manager"):
            with self.subTest(path=path):
                text = self.client.get(path).text
                self.assertIn('href="/client-response"', text)
                self.assertIn('href="/"', text)

    def test_validation_handoff_needs_review_and_starts_nothing(self):
        self.resolver.resolve.side_effect = lambda deal_id: Resolution("handoff", "incoming_deal_not_active")
        record = self.finished(self.post(EMAIL))
        self.assertEqual((record["status"], record["found_deal_id"], record["deal_id"], record["run_id"]),
                         ("needs_review", "300", None, None))
        self.assertIn("incoming_deal_not_active", record["reason"])
        self.assertEqual(self.runs, [])

    def test_consolidation_runs_account_manager_on_the_kept_deal(self):
        plan = {"canonical_deal_id": 301, "merged_deal_ids": [300], "proof_ids_to_link": [], "actions": []}
        self.resolver.resolve.side_effect = lambda deal_id: Resolution(
            "proceed", "consolidated_for_client_response", "301", plan)
        record = self.finished(self.post(EMAIL))
        self.assertEqual((record["found_deal_id"], record["deal_id"]), ("300", "301"))
        self.assertEqual(record["resolution"]["consolidation"], plan)
        self.assertEqual(self.runs[0][0], "301")

    def test_agent_match_is_validated_before_account_manager(self):
        self.finder.find.return_value = DealSearch("none", "x")
        record = self.finished(self.post(EMAIL))
        self.resolver.resolve.assert_called_once_with("500")
        self.assertEqual((record["resolved_by"], record["deal_id"]), ("agent", "500"))

    def test_invalid_payload_is_422_and_unknown_record_is_404(self):
        self.assertEqual(self.post({"phone": "+14155550100", "body": "not an array"}).status_code, 422)
        self.assertEqual(self.post({"body": "no sender"}).status_code, 422)
        self.assertEqual(self.client.get("/api/inbound/unknown").status_code, 404)
        self.finder.find.assert_not_called()

    def test_interrupted_record_is_failed_after_restart_and_keeps_its_message_id(self):
        record = self.finished(self.post({**EMAIL, "message_id": "keep"}))
        path = self.root / "inbound" / f"{record['inbound_id']}.json"
        path.write_text(path.read_text().replace('"run_started"', '"resolving"'))
        reloaded = InboundJobs(self.root / "inbound", self.finder, self.lookups, self.start_run, self.resolver)
        self.addCleanup(reloaded.shutdown)
        self.assertEqual(reloaded.get(record["inbound_id"])["status"], "failed")
        self.assertEqual(reloaded.submit(InboundRequest.model_validate({**EMAIL, "message_id": "keep"}))
                         ["inbound_id"], record["inbound_id"])



class FollowUpMessageTests(unittest.TestCase):
    """Client messages that arrive while the flow runs, like additional messages on a run."""

    start_run = InboundWorkflowTests.start_run
    post = InboundWorkflowTests.post
    finished = InboundWorkflowTests.finished

    def setUp(self):
        InboundWorkflowTests.setUp(self)
        # Cleanups run last-in first-out: let runs and deliveries finish before the temp dir goes.
        self.addCleanup(self.wait_for_background_work)

    def wait_for_background_work(self):
        self.release.set()
        self.wait_until(lambda: all(run.status in {"completed", "failed"} for run in self.sales.list_all())
                        and not self.inbound.delivering)

    def hold_lookup(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def find(request):
            gate.wait(3)
            return DealSearch("found", "Only active deal", "300")
        self.finder.find.side_effect = find
        return gate

    def wait_until(self, check, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = check()
            if value:
                return value
            time.sleep(0.01)
        raise AssertionError("Condition not reached")

    def record(self, inbound_id):
        return self.client.get(f"/api/inbound/{inbound_id}").json()

    def message(self, inbound_id, text):
        return self.client.post(f"/api/inbound/{inbound_id}/messages", json={"text": text})

    def test_message_before_account_manager_is_held_and_sent_with_the_first(self):
        gate = self.hold_lookup()
        inbound_id = self.post(SMS).json()["inbound_id"]
        held = self.message(inbound_id, "Also need 20 more")
        self.assertEqual((held.status_code, held.json()["status"]), (202, "held"))
        self.assertTrue(self.record(inbound_id)["accepting_messages"])
        gate.set()
        record = wait_for(lambda: self.record(inbound_id))
        self.assertEqual(record["messages"][0]["status"], "included")
        self.assertEqual(self.runs[0][1],
                         "Channel: SMS\nFrom: +14155550100\n\nHi\nIs my order ready?\nAlso need 20 more")

    def test_message_after_account_manager_starts_is_forwarded_to_the_run(self):
        self.release.clear()
        record = self.finished(self.post(EMAIL))
        sent = self.message(record["inbound_id"], "Actually black, quantity 60")
        self.assertEqual((sent.status_code, sent.json()["status"]), (202, "pending"))
        delivered = self.wait_until(lambda: [m for m in self.record(record["inbound_id"])["messages"]
                                             if m.get("delivery") == "accepted"])
        self.assertEqual(delivered[0]["status"], "forwarded")
        self.assertEqual([m["text"] for m in self.sales.get(record["run_id"]).messages],
                         ["Actually black, quantity 60"])

    def test_handed_off_flow_refuses_more_messages(self):
        self.resolver.resolve.side_effect = lambda deal_id: Resolution("handoff", "incoming_deal_not_active")
        record = self.finished(self.post(EMAIL))
        self.assertFalse(record["accepting_messages"])
        self.assertEqual(self.message(record["inbound_id"], "Hello?").status_code, 409)
        self.assertEqual(self.message("unknown", "x").status_code, 404)

    def test_finished_account_manager_run_refuses_more_messages(self):
        record = self.finished(self.post(EMAIL))
        self.wait_until(lambda: self.sales.get(record["run_id"]).status == "completed")
        self.assertFalse(self.record(record["inbound_id"])["accepting_messages"])
        self.assertEqual(self.message(record["inbound_id"], "Late").status_code, 409)

    def test_webhook_sms_from_the_same_phone_joins_the_open_flow(self):
        gate = self.hold_lookup()
        first = self.post(SMS).json()
        second = self.post({"phone": "(415) 555-0100", "body": ["One more thing", "Rush please"]})
        self.assertEqual((second.status_code, second.json()["inbound_id"], second.json()["attached"]),
                         (202, first["inbound_id"], True))
        gate.set()
        wait_for(lambda: self.record(first["inbound_id"]))
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(self.runs[0][1].endswith("Is my order ready?\nOne more thing\nRush please"))

    def test_webhook_email_joins_only_the_same_thread(self):
        gate = self.hold_lookup()
        first = self.post(EMAIL).json()
        same_thread = self.post({**EMAIL, "body": "Following up"}).json()
        other_thread = self.post({**EMAIL, "thread_id": "t-other"}).json()
        no_thread = self.post({**EMAIL, "thread_id": None}).json()
        self.assertEqual(same_thread["inbound_id"], first["inbound_id"])
        self.assertEqual(len({first["inbound_id"], other_thread["inbound_id"], no_thread["inbound_id"]}), 3)
        gate.set()
        for record in (first, other_thread, no_thread):
            wait_for(lambda: self.record(record["inbound_id"]))


if __name__ == "__main__":
    unittest.main()
