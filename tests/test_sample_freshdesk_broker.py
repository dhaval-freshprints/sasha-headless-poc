"""Host-side Freshdesk broker authorization and idempotency."""

import json
import tempfile
import unittest
from pathlib import Path

from account_manager_agent.samples.broker import SampleFreshdeskBroker


class FakeService:
    def __init__(self):
        self.calls = []

    def freshdesk_show(self, deal_id, sample_id):
        self.calls.append(("show", deal_id, sample_id))
        return {"ok": True, "ticket_id": 91}

    def freshdesk_note(self, deal_id, sample_id, body, action_id=None):
        self.calls.append(("note", deal_id, sample_id, body, action_id))
        return {"ok": True, "conversation_id": 7}


class SampleFreshdeskBrokerTests(unittest.TestCase):
    def test_broker_owns_deal_scope_and_reuses_response_for_same_action_id(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeService()
            broker = SampleFreshdeskBroker(
                Path(directory),
                "397003",
                service_factory=lambda: service,
            )
            broker.requests.mkdir(parents=True)
            broker.responses.mkdir()
            request_path = broker.requests / "stable-action.json"
            payload = {
                "action_id": "stable-action",
                "action": "note",
                "sample_id": 1,
                "body": "Client confirmed purple.",
                "deal_id": "999999",
            }
            request_path.write_text(json.dumps(payload), encoding="utf-8")
            broker._process(request_path)
            self.assertEqual(
                service.calls,
                [("note", "397003", 1, "Client confirmed purple.", "stable-action")],
            )

            request_path.write_text(json.dumps(payload), encoding="utf-8")
            broker._process(request_path)
            self.assertEqual(len(service.calls), 1)
            response = json.loads((broker.responses / "stable-action.json").read_text())
            self.assertTrue(response["ok"])

    def test_broker_rejects_unknown_actions_before_calling_service(self):
        with tempfile.TemporaryDirectory() as directory:
            service = FakeService()
            broker = SampleFreshdeskBroker(
                Path(directory),
                "42",
                service_factory=lambda: service,
            )
            result = broker._execute({"action": "delete", "sample_id": 1})
            self.assertEqual(result, {"ok": False, "error": "unsupported_action"})
            self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()
