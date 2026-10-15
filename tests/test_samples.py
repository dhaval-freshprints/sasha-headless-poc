"""Free samples: the agent decides; code stores the sample, creates the Freshdesk ticket and records updates."""
import json
import unittest

from account_manager_agent.samples.freshdesk import (
    FreshdeskClient,
    infer_sample_status_from_thread,
    ticket_id_from_url,
    ticket_payload,
)
from account_manager_agent.samples.service import SampleService

REQUEST = {"trigger": "client_request", "style_code": "C1717", "product_link": "https://fp.test/p/c1717",
           "color": "White", "size": "M", "ship_to_name": "Rita Lee",
           "ship_to_address": "1 Main St, Davis CA", "contact_email": "rita@example.com",
           "contact_phone": "555-0100", "organization": "Delta Gamma"}


class FakeSettings:
    def __init__(self, values=None):
        self.data = values if values is not None else {
            "followup_sample_max_unit_value": "50", "sample_max_per_deal": "4", "followup_sample_cooldown_days": "14"}

    def values(self, *keys):
        return {key: self.data[key] for key in keys if key in self.data}


class FakeSamples:
    def __init__(self):
        self.rows = []
        self.tickets = {}

    def for_deal(self, deal_id):
        return list(self.rows)

    def deal_ids_with_open_tickets(self, limit=200):
        return sorted({
            str(row["deal_id"]) if "deal_id" in row else "42"
            for row in self.rows
            if row.get("status") in ("placed", "shipped") and row.get("freshdesk_ticket_url")
        })

    def place(self, deal_id, run_id, request):
        row = {**request, "id": len(self.rows) + 1, "status": "placed"}
        self.rows.append(row)
        return row

    def set_ticket(self, sample_id, url):
        self.tickets[sample_id] = url
        for row in self.rows:
            if row["id"] == sample_id:
                row["freshdesk_ticket_url"] = url

    def update(self, deal_id, sample_id, status, details):
        row = next(row for row in self.rows if row["id"] == sample_id)
        row.update(status=status, cancel_reason=details.get("cancel_reason"))


class FakeFreshdesk:
    def __init__(self, fail=False, conversations=()):
        self.fail = fail
        self.calls = []
        self.conversations = list(conversations)

    def create_sample_ticket(self, sample, deal_id, organization):
        self.calls.append((sample["style_code"], deal_id, organization))
        if self.fail:
            raise RuntimeError("Freshdesk refused the ticket (HTTP 400)")
        return "https://fp.freshdesk.com/a/tickets/9001"

    def get_ticket(self, ticket_id):
        return {"id": ticket_id, "status": 2}

    def get_conversations(self, ticket_id):
        return self.conversations


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.samples = FakeSamples()
        self.freshdesk = FakeFreshdesk()
        self.service = SampleService(samples=self.samples, settings=FakeSettings(),
                                     freshdesk_factory=lambda: self.freshdesk)

    def test_places_the_sample_and_creates_its_ticket(self):
        result = self.service.place("42", "run-1", REQUEST)
        self.assertEqual((result["placed"], result["sample_id"], result["ticket"]),
                         (True, 1, "https://fp.freshdesk.com/a/tickets/9001"))
        self.assertEqual(self.freshdesk.calls, [("C1717", "42", "Delta Gamma")])
        self.assertEqual(self.samples.tickets[1], "https://fp.freshdesk.com/a/tickets/9001")

    def test_context_gives_the_agent_the_samples_and_the_settings_limits(self):
        self.service.place("42", "run-1", REQUEST)
        context = self.service.context("42")
        self.assertEqual(context["limits"], {"followup_sample_max_unit_value": "50", "sample_max_per_deal": "4",
                                             "followup_sample_cooldown_days": "14"})
        self.assertEqual(context["samples"][0]["style_code"], "C1717")

    def test_a_failed_ticket_cancels_the_sample_and_reports_it(self):
        self.freshdesk.fail = True
        result = self.service.place("42", "run-1", REQUEST)
        self.assertEqual((result["placed"], self.samples.rows[0]["status"]), (False, "cancelled"))
        self.assertIn("HTTP 400", result["reason"])

    def test_without_freshdesk_the_sample_is_stored_but_not_ticketed(self):
        self.service.freshdesk_factory = lambda: None
        result = self.service.place("42", "run-1", REQUEST)
        self.assertEqual((result["placed"], result["ticket"]), (True, "not sent: Freshdesk is not configured"))

    def test_followup_sync_advances_sample_status_from_freshdesk_thread(self):
        self.service.place("42", "run-1", REQUEST)
        self.samples.set_ticket(1, "https://fp.freshdesk.com/a/tickets/9001")
        self.freshdesk.conversations = [{"body_text": "Shipped via UPS tracking 1Z999"}]
        sync = self.service.sync_freshdesk_for_deal("42")
        self.assertEqual(sync["updates_applied"], [{"sample_id": 1, "from": "placed", "to": "shipped"}])
        self.assertEqual(self.samples.rows[0]["status"], "shipped")
        all_sync = self.service.sync_all_open_samples()
        self.assertEqual(len(all_sync["deals"]), 1)
        context = self.service.context("42")
        self.assertEqual(context["samples"][0]["status"], "shipped")

    def test_updates_are_stored_as_the_agent_read_them(self):
        self.service.place("42", "run-1", REQUEST)
        shipped = self.service.record_update("42", {"sample_id": 1, "status": "shipped", "cancel_reason": ""})
        self.assertEqual((shipped["updated"], self.samples.rows[0]["status"]), (True, "shipped"))
        self.assertFalse(self.service.record_update("42", {"sample_id": 99, "status": "shipped"})["updated"])


class FreshdeskParsingTests(unittest.TestCase):
    def test_ticket_id_from_url(self):
        self.assertEqual(ticket_id_from_url("https://fp.freshdesk.com/a/tickets/9001"), 9001)
        self.assertIsNone(ticket_id_from_url(""))

    def test_infer_status_from_thread(self):
        conversations = [{"body_text": "Your sample shipped today. Tracking: 1Z"}]
        self.assertEqual(infer_sample_status_from_thread(conversations, "placed"), "shipped")
        self.assertIsNone(infer_sample_status_from_thread(conversations, "shipped"))


class FreshdeskTests(unittest.TestCase):
    def test_ticket_matches_the_v3_sample_ticket(self):
        payload = ticket_payload(REQUEST, 42, "Delta Gamma", "sasha@freshprints.com")
        self.assertEqual((payload["subject"], payload["type"], payload["tags"], payload["group_id"], payload["source"]),
                         ("New Sample Requested for Fresh Prints", "Sample Orders", ["sasha-sample", "sasha"],
                          1069000474780, 10))
        self.assertEqual((payload["email"], payload["custom_fields"]["cf_deal_id"],
                          payload["custom_fields"]["cf_garment_color"]), ("sasha@freshprints.com", "42", "White"))
        self.assertIn("BLANK sample only", payload["description"])
        self.assertIn("Client requested a sample", payload["description"])

    def test_client_posts_the_ticket_with_basic_auth(self):
        sent = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"id": 77}'

        def opener(request, timeout):
            sent.append(request)
            return Response()

        client = FreshdeskClient("https://fp.freshdesk.com", "key", "sasha@freshprints.com", opener=opener)
        self.assertEqual(client.create_sample_ticket(REQUEST, 42, "DG"), "https://fp.freshdesk.com/a/tickets/77")
        self.assertEqual(sent[0].full_url, "https://fp.freshdesk.com/api/v2/tickets")
        self.assertTrue(sent[0].headers["Authorization"].startswith("Basic "))
        self.assertEqual(json.loads(sent[0].data)["custom_fields"]["cf_style_code"], "C1717")


if __name__ == "__main__":
    unittest.main()
