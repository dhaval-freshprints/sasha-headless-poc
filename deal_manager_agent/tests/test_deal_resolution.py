"""Validation and consolidation before Account Manager, ported from Sasha v3; fake DB and CRM."""
import io
import json
import unittest
import urllib.error
from datetime import date
from types import SimpleNamespace

from deal_manager_agent.deal_resolution import DUPLICATE_LOST_REASON, LOST_REASON, DealResolver
from deal_manager_agent.v3_api import EDIT_DEAL_PATH, V3Api, sign_token

SASHA = 42


class FakeConnection:
    def __init__(self):
        self.closed = False

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def fake_database(deals, proofs=None, outreach=None, fail=False):
    """deals: {id: {"client_id", "stage", "manager_id", "deleted"}}; outreach: {deal_id: time}."""
    proofs = proofs or {}
    outreach = outreach or {}
    connection = FakeConnection()

    def read_query(conn, sql, parameters=(), limit=100):
        if fail:
            raise RuntimeError("secret-host unreachable")
        if "sasha_conversation_history" in sql:
            return rows([{"deal_id": int(d), "last_outbound_at": outreach[int(d)]}
                         for d in parameters if int(d) in outreach])
        if "deal_proofs" in sql:
            return rows([{"proof_id": p} for p in proofs.get(parameters[0], ())])
        if "WHERE id=%s" in sql:
            deal = deals.get(parameters[0])
            if not deal or deal.get("deleted"):
                return rows([])
            return rows([{"id": parameters[0], "client_id": deal["client_id"],
                          "title": deal.get("title", "T"), "stage": deal["stage"]}])
        if "WHERE client_id IN" in sql:
            client, manager = parameters[0], parameters[1]
            return rows([{"deal_id": i, "title": d.get("title", "T"), "client_id": client,
                          "stage": d["stage"], "created_at": d.get("created_at")}
                         for i, d in sorted(deals.items(), reverse=True)
                         if d["client_id"] == client and not d.get("deleted")
                         and d.get("manager_id", SASHA) == manager
                         and d["stage"].lower() not in ("won", "lost")])
        raise AssertionError(sql)

    database = SimpleNamespace(
        configured_schemas=lambda: {"main": "m", "operations": "o"},
        quote_schema=lambda name: f"`{name}`",
        connect=lambda schema: connection,
        read_query=read_query,
    )
    return database, connection


def rows(values):
    return {"rows": values, "truncated": False}


class FakeCrm:
    """Records edits; with deals, a successful stage edit is visible to later reads."""

    def __init__(self, fail_on=(), deals=None):
        self.calls = []
        self.fail_on = set(fail_on)
        self.deals = deals

    def edit_deal(self, deal_id, payload):
        self.calls.append((deal_id, payload))
        if (deal_id, next(iter(payload))) in self.fail_on:
            return False, "CRM refused"
        if self.deals is not None and "stage" in payload:
            self.deals[deal_id]["stage"] = payload["stage"]
        return True, "Deal updated successfully"


def resolve(deal_id, deals, crm=None, workflow="client_response", **database):
    fake, connection = fake_database(deals, **database)
    resolver = DealResolver(fake, crm=crm, manager_id=SASHA, crm_from_environment=False,
                            recency_days=45, today=date(2026, 10, 13))
    return resolver.resolve(deal_id, workflow), connection


class ValidationTests(unittest.TestCase):
    def test_invalid_or_unknown_deal_is_handed_off(self):
        self.assertEqual(resolve("abc", {})[0].reason, "missing_deal_id")
        self.assertEqual(resolve("0", {})[0].reason, "missing_deal_id")
        self.assertEqual(resolve("10", {})[0].reason, "missing_client_id")
        deleted = {10: {"client_id": 7, "stage": "leads", "deleted": True}}
        self.assertEqual(resolve("10", deleted)[0].reason, "missing_client_id")

    def test_only_active_deal_proceeds_without_crm_writes(self):
        crm = FakeCrm()
        result, connection = resolve("10", {10: {"client_id": 7, "stage": "leads"}}, crm)
        self.assertEqual((result.status, result.deal_id, result.reason),
                         ("proceed", "10", "single_active_deal"))
        self.assertEqual(crm.calls, [])
        self.assertTrue(connection.closed)

    def test_won_deal_still_proceeds_for_a_client_response(self):
        deals = {10: {"client_id": 7, "stage": "won"}, 11: {"client_id": 7, "stage": "leads"}}
        result, _ = resolve("10", deals, FakeCrm())
        self.assertEqual((result.status, result.deal_id, result.reason),
                         ("proceed", "10", "incoming_deal_won_client_response"))

    def test_internal_email_proceeds_on_an_active_or_won_deal_without_merging(self):
        deals = {10: {"client_id": 7, "stage": "leads"}, 11: {"client_id": 7, "stage": "leads"},
                 12: {"client_id": 7, "stage": "won"}}
        crm = FakeCrm()
        active, _ = resolve("10", deals, crm, "internal_email", outreach={11: "2026-10-01"})
        won, _ = resolve("12", deals, crm, "internal_email")
        self.assertEqual((active.status, active.deal_id, active.reason, active.consolidation),
                         ("proceed", "10", "internal_email_active_deal", None))
        self.assertEqual((won.status, won.deal_id, won.reason),
                         ("proceed", "12", "incoming_deal_won_internal_email"))
        self.assertEqual(crm.calls, [])

    def test_internal_email_on_a_lost_deal_is_handed_off(self):
        result, _ = resolve("10", {10: {"client_id": 7, "stage": "lost"}}, FakeCrm(), "internal_email")
        self.assertEqual((result.status, result.reason), ("handoff", "incoming_deal_not_active"))

    def test_lost_or_other_managers_deal_is_handed_off(self):
        for deal in ({"client_id": 7, "stage": "lost"}, {"client_id": 7, "stage": "leads", "manager_id": 99}):
            with self.subTest(deal=deal):
                result, _ = resolve("10", {10: deal}, FakeCrm())
                self.assertEqual((result.status, result.reason), ("handoff", "incoming_deal_not_active"))
                self.assertIsNone(result.deal_id)

    def test_database_failure_is_a_handoff_without_details(self):
        result, connection = resolve("10", {}, fail=True)
        self.assertEqual(result.status, "handoff")
        self.assertNotIn("secret-host", result.reason)
        self.assertTrue(connection.closed)


class ConsolidationTests(unittest.TestCase):
    deals = {10: {"client_id": 7, "stage": "leads"}, 11: {"client_id": 7, "stage": "negotiating"},
             12: {"client_id": 7, "stage": "proof-submitted"}}
    proofs = {10: {500, 501}, 11: {501, 502}, 12: {503}}

    def test_merges_into_the_deal_sasha_last_wrote_to_like_v3(self):
        crm = FakeCrm()
        result, _ = resolve("10", self.deals, crm, proofs=self.proofs,
                            outreach={11: "2026-03-01", 12: "2026-01-01"})
        self.assertEqual((result.status, result.deal_id, result.reason),
                         ("proceed", "11", "consolidated_for_client_response"))
        reason = LOST_REASON.format(canonical=11)
        self.assertEqual(crm.calls, [
            (11, {"proofIds": [500, 503]}),
            (12, {"stage": "lost", "lostReason": reason}),
            (10, {"stage": "lost", "lostReason": reason}),
        ])
        plan = result.consolidation
        self.assertEqual((plan["canonical_deal_id"], plan["merged_deal_ids"], plan["proof_ids_to_link"]),
                         (11, [12, 10], [500, 503]))
        self.assertTrue(all(action["ok"] for action in plan["actions"]))

    def test_without_outreach_the_incoming_deal_is_kept(self):
        crm = FakeCrm()
        result, _ = resolve("12", self.deals, crm, proofs=self.proofs)
        self.assertEqual(result.deal_id, "12")
        self.assertEqual([deal for deal, payload in crm.calls if "stage" in payload], [11, 10])

    def test_no_proof_link_call_when_nothing_to_link(self):
        crm = FakeCrm()
        deals = {10: {"client_id": 7, "stage": "leads"}, 11: {"client_id": 7, "stage": "leads"}}
        resolve("10", deals, crm, proofs={10: {1}, 11: {1}})
        self.assertEqual(crm.calls, [(11, {"stage": "lost", "lostReason": LOST_REASON.format(canonical=10)})])

    def test_crm_writes_not_configured_hands_off_with_the_plan(self):
        result, _ = resolve("10", self.deals, None, proofs=self.proofs)
        self.assertEqual((result.status, result.reason), ("handoff", "crm_writes_not_configured"))
        self.assertEqual(result.consolidation["merged_deal_ids"], [12, 11])
        self.assertEqual(result.consolidation["actions"], [])

    def test_proof_link_failure_stops_before_marking_lost(self):
        crm = FakeCrm(fail_on={(10, "proofIds")})
        result, _ = resolve("10", self.deals, crm, proofs=self.proofs)
        self.assertEqual((result.status, result.reason), ("handoff", "proof_link_failed"))
        self.assertEqual(len(crm.calls), 1)

    def test_mark_lost_failure_stops_and_is_reported(self):
        crm = FakeCrm(fail_on={(12, "stage")})
        result, _ = resolve("10", self.deals, crm, proofs=self.proofs)
        self.assertEqual((result.status, result.reason), ("handoff", "mark_lost_failed"))
        self.assertEqual(result.consolidation["actions"][-1]["message"], "CRM refused")
        self.assertNotIn(11, [deal for deal, payload in crm.calls if "stage" in payload])


class OutreachTests(unittest.TestCase):
    """INITIAL_OUTREACH: v3 duplicate guard, then consolidation, then skip if already outreached."""

    def deals(self, extra=None):
        return {20: {"client_id": 7, "stage": "leads", "title": "Club  Hoodies", "created_at": "2026-10-13"},
                **(extra or {})}

    def test_single_deal_proceeds_without_writes(self):
        crm = FakeCrm()
        result, _ = resolve("20", self.deals(), crm, "outreach", proofs={20: {9}})
        self.assertEqual((result.status, result.deal_id, result.reason, result.workflow),
                         ("proceed", "20", "single_active_deal", "outreach"))
        self.assertEqual(crm.calls, [])

    def test_duplicate_of_an_outreached_deal_marks_the_new_deal_lost(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "club hoodies", "created_at": "2025-01-01"}})
        crm = FakeCrm(deals=deals)
        result, _ = resolve("20", deals, crm, "outreach", proofs={20: {9}, 10: {9}}, outreach={10: "2026-09-01"})
        self.assertEqual(crm.calls, [(20, {"stage": "lost", "lostReason": DUPLICATE_LOST_REASON})])
        self.assertEqual((result.status, result.deal_id), ("proceed", "10"))
        self.assertEqual(result.duplicate_guard["lost_deal_ids"], [20])

    def test_duplicate_without_outreach_keeps_the_oldest_recent_deal(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "Club Hoodies", "created_at": "2026-10-01"},
                              11: {"client_id": 7, "stage": "leads", "title": "Club Hoodies", "created_at": "2026-10-05"}})
        crm = FakeCrm(deals=deals)
        result, _ = resolve("20", deals, crm, "outreach", proofs={20: {9}, 10: {9}, 11: {9}})
        self.assertEqual(result.deal_id, "10")
        self.assertEqual(sorted(deal for deal, _ in crm.calls), [11, 20])

    def test_old_duplicates_are_lost_and_the_new_deal_kept(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "Club Hoodies", "created_at": "2026-01-01"}})
        crm = FakeCrm(deals=deals)
        result, _ = resolve("20", deals, crm, "outreach", proofs={20: {9}, 10: {9}})
        self.assertEqual((result.deal_id, result.reason), ("20", "single_active_deal"))
        self.assertEqual(result.duplicate_guard["lost_deal_ids"], [10])

    def test_different_proofs_are_not_duplicates_but_are_consolidated(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "Club Hoodies"}})
        crm = FakeCrm(deals=deals)
        result, _ = resolve("20", deals, crm, "outreach", proofs={20: {9}, 10: {8}})
        self.assertEqual((result.status, result.deal_id, result.reason),
                         ("proceed", "20", "consolidated_for_outreach"))
        self.assertIsNone(result.duplicate_guard)
        self.assertEqual(crm.calls[0], (20, {"proofIds": [8]}))

    def test_outreach_is_skipped_when_the_kept_deal_already_had_outreach(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "Other"}})
        result, _ = resolve("20", deals, FakeCrm(deals=deals), "outreach",
                            proofs={20: {9}, 10: {8}}, outreach={10: "2026-09-01"})
        self.assertEqual((result.status, result.deal_id, result.reason),
                         ("skipped", "10", "consolidated_canonical_already_outreached"))

    def test_won_deal_is_handed_off_for_outreach(self):
        result, _ = resolve("20", {20: {"client_id": 7, "stage": "won"}}, FakeCrm(), "outreach")
        self.assertEqual((result.status, result.reason), ("handoff", "incoming_deal_not_active"))

    def test_duplicates_without_crm_writes_are_handed_off_with_the_plan(self):
        deals = self.deals({10: {"client_id": 7, "stage": "leads", "title": "Club Hoodies", "created_at": "2026-10-01"}})
        result, _ = resolve("20", deals, None, "outreach", proofs={20: {9}, 10: {9}})
        self.assertEqual((result.status, result.reason), ("handoff", "crm_writes_not_configured"))
        self.assertEqual(result.duplicate_guard["survivor_deal_id"], 10)
        self.assertIn("CRM writes are not configured", result.summary)


class V3ApiTests(unittest.TestCase):
    def test_token_matches_v3_claims_and_signature(self):
        import base64, hashlib, hmac
        token = sign_token("secret", 7, now=1000)
        header, payload, signature = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        self.assertEqual(claims, {"purpose": "authentication", "userId": 7, "exp": 1600})
        expected = hmac.new(b"secret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
        self.assertEqual(base64.urlsafe_b64decode(signature + "=="), expected)

    def test_edit_deal_puts_the_card_with_the_deal_id(self):
        sent = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout):
            sent.update(url=request.full_url, method=request.get_method(), body=json.loads(request.data))
            return Response(b'{"message": "Deal updated"}')

        ok, message = V3Api("https://v3.example/", "secret", 7, opener=opener).edit_deal(
            12, {"stage": "lost", "lostReason": "x", "id": 999})
        self.assertEqual((ok, message), (True, "Deal updated"))
        self.assertEqual(sent["method"], "PUT")
        self.assertTrue(sent["url"].startswith(f"https://v3.example{EDIT_DEAL_PATH}?authToken="))
        self.assertEqual(sent["body"], {"stage": "lost", "lostReason": "x", "id": 12})

    def test_errors_are_reported_without_the_token(self):
        def http_error(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, io.BytesIO(b'{"error": {"message": "Deal locked"}}'))

        def network_error(request, timeout):
            raise urllib.error.URLError(f"cannot reach {request.full_url}")

        self.assertEqual(V3Api("https://v3", "k", 7, opener=http_error).edit_deal(1, {}), (False, "Deal locked"))
        ok, message = V3Api("https://v3", "k", 7, opener=network_error).edit_deal(1, {})
        self.assertFalse(ok)
        self.assertNotIn("authToken", message)

    def test_not_configured_without_all_three_settings(self):
        from unittest.mock import patch
        full = {"V3_API_BASE_URL": "https://v3", "JWT_AUTH_KEY": "k", "SASHA_ADMIN_USER": "7"}
        with patch.dict("os.environ", full):
            self.assertIsNotNone(V3Api.from_environment())
        for missing in full:
            with self.subTest(missing=missing), patch.dict("os.environ", {**full, missing: ""}):
                self.assertIsNone(V3Api.from_environment())


if __name__ == "__main__":
    unittest.main()
