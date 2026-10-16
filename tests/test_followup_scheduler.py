"""Automatic follow-ups: due deals are claimed once and run in client hours when sasha_settings allow it."""
from datetime import datetime, timezone
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.database_helpers import MemoryStorage
from tests.test_account_manager_agent_environment import APPLICATION_VALUES
from webapp.app import run_due_followups
from webapp.deals import DealStore
from webapp.jobs import JobManager
from webapp.tests.helpers import FakeRunner

WEDNESDAY_11_ET = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
ENABLED = {"sasha_master_key": "enabled", "auto_followup_enabled": "true"}


class Settings:
    def __init__(self, values):
        self.data = values

    def values(self, *keys):
        return {key: self.data[key] for key in keys if key in self.data}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, APPLICATION_VALUES)
        environment.start()
        self.addCleanup(environment.stop)
        self.database = MemoryStorage()
        self.manager = JobManager(root / "jobs", lambda write: FakeRunner(write, root),
                                  storage_factory=lambda: self.database)
        self.addCleanup(self.manager.shutdown)
        self.deals = DealStore(root / "deals")
        due = {"status": "active", "next_followup_date": "2026-10-14T14:00:00",
               "scheduled_followup_reason": "Sent mockup; check in"}
        self.database.followups = {"101": dict(due), "102": dict(due), "103": dict(due),
                                   "104": {**due, "next_followup_date": "2026-10-20T14:00:00"},
                                   "105": {**due, "status": "stopped"}}
        self.database.statuses = {"102": "opt_out", "103": "handoff_needed"}

    def run_due(self, settings=ENABLED, now=WEDNESDAY_11_ET, force=False):
        return run_due_followups(self.manager, self.deals, Settings(settings), force=force, now=now)

    def wait(self):
        deadline = time.monotonic() + 3
        while any(run.status not in {"completed", "failed"} for run in self.manager.list_all()):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def test_only_due_contactable_deals_start_and_only_once(self):
        result = self.run_due()
        self.assertEqual([item["deal_id"] for item in result["started"]], ["101"])
        run = self.manager.get(result["started"][0]["run_id"])
        self.assertEqual((run.turn_type, run.as_of_date, run.client_message), ("follow_up", "2026-10-14", None))
        self.assertEqual(self.run_due()["started"], [])
        self.assertIsNotNone(self.deals.get("101"))
        self.wait()

    def test_settings_switches_turn_it_off(self):
        for values in ({}, {**ENABLED, "auto_followup_enabled": "false"}, {**ENABLED, "sasha_master_key": "disabled"}):
            with self.subTest(values=values):
                result = self.run_due(values)
                self.assertEqual(result["started"], [])
                self.assertIn("disabled", result["skipped"])
        self.assertEqual(self.manager.list_all(), [])

    def test_runs_on_weekends_and_catches_up_overdue_days(self):
        self.assertEqual([item["deal_id"] for item in self.run_due(now=SATURDAY)["started"]], ["101"])
        self.wait()

    def test_waits_for_the_scheduled_hour_unless_forced(self):
        early = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # 08:00 ET; due 10:00 ET
        self.assertEqual(self.run_due(now=early)["started"], [])
        self.assertEqual(len(self.run_due(now=early, force=True)["started"]), 1)
        self.wait()

    def test_claimed_once_per_day_and_released_when_the_run_cannot_start(self):
        with patch.object(self.manager, "submit", side_effect=RuntimeError("queue down")):
            with self.assertRaises(RuntimeError):
                self.run_due()
        self.assertEqual([item["deal_id"] for item in self.run_due()["started"]], ["101"])
        self.assertEqual(self.run_due()["started"], [])
        self.wait()

    def test_won_or_lost_deals_stop_instead_of_running(self):
        class Resolver:
            def resolve(self, deal_id, workflow):
                assert workflow == "follow_up"
                return SimpleNamespace(status="skipped", reason="deal_won", to_dict=dict)

        result = run_due_followups(self.manager, self.deals, Settings(ENABLED), now=WEDNESDAY_11_ET,
                                   resolver=Resolver())
        self.assertEqual((result["started"], result["stopped"]), ([], [{"deal_id": "101", "reason": "deal_won"}]))
        self.assertEqual((self.database.followups["101"]["status"], self.database.followups["101"]["stop_reason"]),
                         ("stopped", "won"))

    def test_paused_deal_with_a_planned_check_still_runs(self):
        self.database.statuses["101"] = "paused"
        self.assertEqual([item["deal_id"] for item in self.run_due()["started"]], ["101"])
        self.wait()


if __name__ == "__main__":
    unittest.main()
