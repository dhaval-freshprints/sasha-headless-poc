"""Handoff to a person: result rules, ISA selection, timeouts, failure policy, offers and replies."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from openai_managed.handoff.selection import select_next_isa
from openai_managed.handoff.service import HandoffService, offer_body
from openai_managed.handoff.working_hours import is_offer_timed_out, is_working_time, working_minutes_elapsed
from sasha_database.handoff import Isa, Offer
from sasha_database.storage import validate_handoff_reason

from tests.database_helpers import MemoryStorage

UTC = timezone.utc
REASON = "Rita is upset that the proof was wrong twice and asked for a person."


def isa(isa_id, rank=None, email=None):
    return Isa(isa_id, 1000 + isa_id, email or f"isa{isa_id}@freshprints.com", f"Isa{isa_id}", "Test",
               rank if rank is not None else isa_id)


def offer(offer_id, deal_id, isa_id, status="OFFERED", offered_at=None):
    return Offer(offer_id, deal_id, isa_id, status, None,
                 offered_at or datetime(2026, 10, 14, 14, 0), None)


class ReasonTests(unittest.TestCase):
    def test_the_agents_reason_is_kept_in_its_own_words_and_only_emptiness_is_refused(self):
        self.assertEqual(validate_handoff_reason("  The chapter wants a store.  "), "The chapter wants a store.")
        for empty in ("", "   ", None):
            with self.subTest(empty=empty), self.assertRaises(ValueError):
                validate_handoff_reason(empty)


class SelectionTests(unittest.TestCase):
    isas = [isa(1), isa(2), isa(3)]

    def test_round_robin_after_the_globally_latest_offer(self):
        self.assertEqual(select_next_isa(self.isas, [], None).id, 1)
        self.assertEqual(select_next_isa(self.isas, [], 1).id, 2)
        self.assertEqual(select_next_isa(self.isas, [], 3).id, 1)
        self.assertEqual(select_next_isa(self.isas, [], 99).id, 1)

    def test_skips_isas_this_deal_tried_and_never_wraps(self):
        tried = [offer(1, 42, 2, "REJECTED"), offer(2, 42, 3, "TIMED_OUT")]
        self.assertEqual(select_next_isa(self.isas, tried, 1).id, 1)
        tried.append(offer(3, 42, 1, "REJECTED"))
        self.assertIsNone(select_next_isa(self.isas, tried, 1))

    def test_open_or_accepted_offer_blocks_a_new_one(self):
        for status in ("OFFERED", "ACCEPTED"):
            self.assertIsNone(select_next_isa(self.isas, [offer(1, 42, 1, status)], None))
        self.assertIsNone(select_next_isa([], [], None))


class WorkingHoursTests(unittest.TestCase):
    def test_counts_only_weekday_working_minutes_in_new_york(self):
        # Wed 2026-10-14 16:50 ET (20:50 UTC) to Thu 08:10 ET: 10 + 10 minutes.
        start = datetime(2026, 10, 14, 20, 50, tzinfo=UTC)
        end = datetime(2026, 10, 15, 12, 10, tzinfo=UTC)
        self.assertAlmostEqual(working_minutes_elapsed(start, end), 20)
        self.assertTrue(is_offer_timed_out(start, end, minutes=20))
        self.assertFalse(is_offer_timed_out(start, end - timedelta(minutes=1), minutes=20))

    def test_weekend_and_naive_utc(self):
        friday = datetime(2026, 10, 16, 20, 55)  # Naive UTC, 16:55 ET.
        monday = datetime(2026, 10, 19, 12, 5, tzinfo=UTC)  # 08:05 ET.
        self.assertAlmostEqual(working_minutes_elapsed(friday, monday), 10)
        self.assertFalse(is_working_time(datetime(2026, 10, 17, 15, 0, tzinfo=UTC)))
        self.assertTrue(is_working_time(datetime(2026, 10, 14, 15, 0, tzinfo=UTC)))


class FakeOffers:
    def __init__(self, isas):
        self.isas = {item.id: item for item in isas}
        self.rows = []
        self.handed_off = []

    def active_isas(self):
        return sorted(self.isas.values(), key=lambda item: item.rank_order)

    def isa(self, isa_id):
        return self.isas.get(isa_id)

    def isa_by_email(self, email):
        return next((item for item in self.isas.values() if item.email.lower() == email.lower()), None)

    def offers_for_deal(self, deal_id):
        return [row for row in self.rows if row.deal_id == int(deal_id)]

    def offer(self, offer_id):
        return next((row for row in self.rows if row.id == offer_id), None)

    def open_offer_for_isa(self, isa_id):
        rows = [row for row in self.rows if row.isa_id == isa_id and row.status == "OFFERED"]
        return rows[0] if len(rows) == 1 else None

    def stale_candidates(self):
        return [row for row in self.rows if row.status == "OFFERED"]

    def deals_needing_offer(self):
        return [deal for deal in self.handed_off
                if not any(row.status in ("OFFERED", "ACCEPTED") for row in self.offers_for_deal(deal))]

    def create_offer(self, deal_id, choose):
        if not self.isas:
            return "empty_roster", None, None
        deal_offers = self.offers_for_deal(deal_id)
        if any(row.status in ("OFFERED", "ACCEPTED") for row in deal_offers):
            return "skipped_existing", None, None
        chosen = choose(self.active_isas(), deal_offers, self.rows[-1].isa_id if self.rows else None)
        if chosen is None:
            return "roster_exhausted", None, None
        row = offer(len(self.rows) + 1, int(deal_id), chosen.id)
        self.rows.append(row)
        return "created", row, chosen

    def _set(self, offer_id, status, allowed):
        for index, row in enumerate(self.rows):
            if row.id == offer_id and row.status in allowed:
                self.rows[index] = replace(row, status=status)
                return True
        return False

    def mark_timed_out(self, offer_id):
        return self._set(offer_id, "TIMED_OUT", ("OFFERED",))

    def mark_rejected(self, offer_id):
        return self._set(offer_id, "REJECTED", ("OFFERED", "TIMED_OUT"))

    def accept(self, offer_id):
        target = next(row for row in self.rows if row.id == offer_id)
        if any(row.status == "ACCEPTED" for row in self.offers_for_deal(target.deal_id)):
            return False
        for row in self.offers_for_deal(target.deal_id):
            if row.id != offer_id:
                self._set(row.id, "REJECTED", ("OFFERED",))
        return self._set(offer_id, "ACCEPTED", ("OFFERED", "TIMED_OUT"))


class FakeStorage(MemoryStorage):
    def __init__(self):
        super().__init__()
        self.outbound = []

    def client_contact(self, deal_id):
        return {"client_id": 7, "email": "rita@example.com"}

    def latest_thread(self, deal_id):
        return {"external_thread_id": "thread-1", "subject": "Your proof"}

    def record_outbound(self, deal_id, run_id, convo_type, body_html, **values):
        self.outbound.append((deal_id, run_id, convo_type, body_html, values))


class FakeCrm:
    def __init__(self):
        self.calls = []

    def assign_manager(self, client_id, manager_id, client_email):
        self.calls.append((client_id, manager_id, client_email))
        return True, "ok"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.storage = FakeStorage()
        self.storage.mark_handoff("42", REASON)
        self.offers = FakeOffers([isa(1), isa(2)])
        self.offers.handed_off.append(42)
        self.crm = FakeCrm()
        self.service = HandoffService(storage=self.storage, offers=self.offers, crm_factory=lambda: self.crm)

    def test_offer_goes_to_the_next_isa_and_its_email_is_rebuilt_from_the_rows(self):
        result = self.service.offer_next("42")
        self.assertEqual((result["outcome"], result["isa_email"]), ("created", "isa1@freshprints.com"))
        email = self.service.offer_email(self.offers.rows[0], isa(1), REASON)
        self.assertEqual((email["to"], email["subject"]), ("isa1@freshprints.com", "Handoff request — Deal 42"))
        self.assertIn("asked for a person", email["html"])
        self.assertEqual(self.service.offer_next("42")["outcome"], "skipped_existing")

    def test_handoff_context_gives_the_agent_the_reason_and_current_offer(self):
        self.assertEqual(self.service.handoff_context("42"), {"status": "handoff_needed", "reason": REASON, "offer": None})
        self.service.offer_next("42")
        offer_state = self.service.handoff_context("42")["offer"]
        self.assertEqual((offer_state["status"], offer_state["isa_email"], offer_state["isa_name"]),
                         ("OFFERED", "isa1@freshprints.com", "Isa1 Test"))

    def test_offer_needs_a_handed_off_deal_and_never_raises(self):
        self.assertEqual(self.service.offer_next("7")["outcome"], "not_handed_off")
        self.service.offers = None
        self.assertEqual(self.service.offer_next("42")["outcome"], "error")

    def test_timeout_moves_to_the_next_isa_then_runs_out(self):
        self.service.offer_next("42")
        later = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # 11:00 ET, 60 working minutes later.
        results = self.service.check_timeouts(now=later)
        self.assertEqual([item["outcome"] for item in results], ["created"])
        self.assertEqual([row.status for row in self.offers.rows], ["TIMED_OUT", "OFFERED"])
        self.assertEqual(self.offers.rows[1].isa_id, 2)
        results = self.service.check_timeouts(now=later, force=True)
        self.assertEqual([item["outcome"] for item in results], ["roster_exhausted"])

    def test_timeouts_wait_outside_working_hours_unless_forced(self):
        self.service.offer_next("42")
        saturday = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
        self.assertEqual(self.service.check_timeouts(now=saturday), [])
        self.assertEqual(len(self.service.check_timeouts(now=saturday, force=True)), 1)

    def test_accepted_decision_assigns_the_manager_and_tells_the_client(self):
        self.service.offer_next("42")
        result = self.service.apply_isa_decision(1, "accepted")
        self.assertEqual((result["decision"], result["accepted"], result["assign_manager"],
                          result["client_transition"]), ("accepted", True, "assigned", "saved"))
        self.assertEqual(self.crm.calls, [(7, 1001, "rita@example.com")])
        [(deal_id, run_id, convo_type, body, values)] = self.storage.outbound
        self.assertEqual((deal_id, run_id, convo_type, values["subject"], values["external_thread_id"]),
                         ("42", "handoff-1", "client_response", "Re: Your proof", "thread-1"))
        self.assertIn("I've looped in Isa1 Test", body)
        self.assertEqual(self.service.handoff_context("42")["offer"]["status"], "ACCEPTED")

    def test_rejected_decision_offers_the_next_isa_and_unclear_waits(self):
        self.service.offer_next("42")
        self.assertEqual(self.service.apply_isa_decision(1, "unclear")["decision"], "unclear")
        self.assertEqual([row.status for row in self.offers.rows], ["OFFERED"])
        result = self.service.apply_isa_decision(1, "rejected")
        self.assertEqual((result["rejected"], result["next_offer"]["isa_email"]), (True, "isa2@freshprints.com"))

    def test_unconfigured_crm_and_unknown_offer_do_not_break_the_flow(self):
        self.service.offer_next("42")
        self.service.crm_factory = lambda: None
        self.assertIn("not found", self.service.apply_isa_decision(99, "accepted")["error"])
        self.assertEqual(self.service.apply_isa_decision(1, "accepted")["assign_manager"], "not configured")


class EmailTextTests(unittest.TestCase):
    def test_offer_body_escapes_the_reason(self):
        body = offer_body(isa(1), 42, "Wants printing on <b>own shirts</b>")
        self.assertIn("Wants printing on &lt;b&gt;own shirts&lt;/b&gt;", body)


if __name__ == "__main__":
    unittest.main()
