"""The deterministic sender -> active deal step, using a fake database."""
import unittest
from types import SimpleNamespace

from deal_manager_agent.deal_lookup import ActiveDealFinder
from deal_manager_agent.models import LookupRequest


class FakeConnection:
    def __init__(self):
        self.closed = False

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def fake_database(users=(), deals=(), outreach=(), truncated=False, fail=False):
    """outreach: (deal_id, last_outbound_at) rows Sasha has written to."""
    log = []
    connection = FakeConnection()

    def read_query(conn, sql, parameters=(), limit=100):
        log.append((sql, list(parameters)))
        if fail:
            raise RuntimeError("secret-host unreachable")
        if ".user " in sql:
            return {"rows": [{"id": item} for item in users], "truncated": False}
        if ".deals " in sql:
            return {"rows": list(deals), "truncated": truncated}
        if "sasha_communication_history" in sql:
            return {"rows": [{"deal_id": d, "last_outbound_at": t} for d, t in outreach],
                    "truncated": False}
        raise AssertionError(sql)

    database = SimpleNamespace(
        configured_schemas=lambda: {"main": "m", "operations": "o", "crm": "c"},
        quote_schema=lambda name: f"`{name}`",
        connect=lambda schema: connection,
        read_query=read_query,
    )
    return database, log, connection


def deal(deal_id, stage="proof-submitted"):
    return {"deal_id": deal_id, "title": f"Deal {deal_id}", "client_id": 7, "stage": stage}


def email(**changes):
    return LookupRequest(sender_email="client@example.com", body="Where is my proof?", **changes)


class ActiveDealFinderTests(unittest.TestCase):
    def find(self, request=None, **database):
        fake, log, connection = fake_database(**database)
        result = ActiveDealFinder(fake, manager_id=42).find(request or email())
        return result, log, connection

    def test_single_active_deal_is_found_and_connection_closed(self):
        result, log, connection = self.find(users=[7], deals=[deal(11)])
        self.assertEqual((result.outcome, result.deal_id), ("found", "11"))
        self.assertTrue(connection.closed)
        self.assertEqual(log[0][1], ["client@example.com"])

    def test_deal_query_matches_production_active_rule(self):
        _, log, _ = self.find(users=[7, 8], deals=[deal(11)])
        sql, parameters = log[1]
        self.assertIn("deleted_at IS NULL", sql)
        self.assertIn("manager_id=%s", sql)
        self.assertIn("NOT IN", sql)
        self.assertEqual(parameters, [7, 8, 42, "won", "lost"])

    def test_no_active_deal_for_sender(self):
        result, *_ = self.find(users=[7], deals=[])
        self.assertEqual((result.outcome, result.deal_id), ("none", None))

    def test_unknown_sender_has_no_deal(self):
        result, log, _ = self.find(users=[], deals=[deal(11)])
        self.assertEqual(result.outcome, "none")
        self.assertEqual(len(log), 1)  # No deal query without a contact.

    def test_several_deals_pick_the_one_sasha_wrote_to_last(self):
        result, *_ = self.find(users=[7], deals=[deal(11), deal(12), deal(13)],
                               outreach=[(11, "2026-01-01"), (12, "2026-03-01")])
        self.assertEqual((result.outcome, result.deal_id), ("found", "12"))
        self.assertEqual([item["deal_id"] for item in result.candidates], ["12"])

    def test_outreach_tie_takes_the_lowest_deal_id(self):
        result, *_ = self.find(users=[7], deals=[deal(12), deal(11)],
                               outreach=[(12, "2026-03-01"), (11, "2026-03-01")])
        self.assertEqual(result.deal_id, "11")

    def test_several_deals_without_outreach_go_to_the_agent_with_candidates(self):
        result, *_ = self.find(users=[7], deals=[deal(11), deal(12)])
        self.assertEqual(result.outcome, "multiple")
        self.assertIsNone(result.deal_id)
        self.assertEqual([item["deal_id"] for item in result.candidates], ["11", "12"])

    def test_truncated_deal_list_is_never_a_unique_match(self):
        result, *_ = self.find(users=[7], deals=[deal(11)], truncated=True)
        self.assertEqual(result.outcome, "multiple")

    def test_phone_lookup_uses_digit_variants_for_north_america(self):
        request = LookupRequest(sender_phone="+1 (415) 555-0100", body="Hi")
        result, log, _ = self.find(request, users=[7], deals=[deal(11)])
        self.assertEqual(result.deal_id, "11")
        self.assertEqual(log[0][1], ["14155550100", "4155550100"])

    def test_database_failure_is_an_error_without_leaking_details(self):
        result, _, connection = self.find(users=[7], fail=True)
        self.assertEqual(result.outcome, "error")
        self.assertNotIn("secret-host", result.reason)
        self.assertTrue(connection.closed)


class LookupRequestTests(unittest.TestCase):
    def test_needs_email_or_phone_and_channel_follows_phone(self):
        with self.assertRaises(ValueError):
            LookupRequest(body="Hi")
        self.assertEqual(email().channel, "EMAIL")
        self.assertEqual(LookupRequest(sender_phone="+14155550100", body="Hi").channel, "SMS")

    def test_phone_must_be_international(self):
        with self.assertRaises(ValueError):
            LookupRequest(sender_phone="4155550100", body="Hi")


if __name__ == "__main__":
    unittest.main()
