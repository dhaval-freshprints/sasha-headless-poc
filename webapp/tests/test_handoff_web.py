"""Handoff in the web app: the run records what Account Manager decided, and nothing blocks by rule."""
from tests.database_helpers import setup_database
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from account_manager_agent.task import SashaResult
from tests.test_account_manager_agent_environment import APPLICATION_VALUES
from webapp.app import create_app
from webapp.deal_manager_jobs import DealManagerJobs
from webapp.deals import DealStore
from webapp.jobs import JobManager
from webapp.tests.helpers import FakeRunner, PassThroughResolver


class HandoffRunner(FakeRunner):
    def run(self, task):
        super().run(task)
        self.last_handoff = {"source": "agent", "reason": "Wants a chapter store.",
                             "offer": {"outcome": "created", "isa_email": "isa@freshprints.com"}}
        return SashaResult(task.deal_id, "handoff", handoff_reason="Wants a chapter store.")


class FakeHandoffs:
    class offers:
        @staticmethod
        def offers_for_deal(deal_id):
            return []



class HandoffWebTests(unittest.TestCase):
    def setUp(self):
        setup_database(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        environment = patch.dict(os.environ, APPLICATION_VALUES)
        environment.start()
        self.addCleanup(environment.stop)
        self.runner_class = HandoffRunner
        self.sales = JobManager(root / "sales", lambda write: self.runner_class(write, root))
        self.addCleanup(self.sales.shutdown)
        app = create_app(self.sales, DealStore(root / "deals"), DealManagerJobs(root / "lookups"),
                         deal_resolver=PassThroughResolver(), handoff_service=FakeHandoffs())
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def finish(self, run_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            run = self.client.get(f"/api/runs/{run_id}").json()
            if run["status"] in {"completed", "handoff", "failed"}:
                return run
            time.sleep(0.01)
        raise AssertionError("Run did not finish")

    def test_handoff_run_is_final_and_records_what_happened(self):
        response = self.client.post("/api/runs", json={"deal_id": "300", "client_message": "Can we do a chapter store?"})
        run = self.finish(response.json()["run_id"])
        self.assertEqual(run["status"], "handoff")
        self.assertEqual((run["handoff"]["source"], run["handoff"]["offer"]["outcome"]), ("agent", "created"))

    def test_handed_off_deal_is_not_blocked_by_rule_and_shows_its_state(self):
        self.database.mark_handoff("300", "Asked for a manager.")
        self.runner_class = FakeRunner
        response = self.client.post("/api/runs", json={"deal_id": "300", "client_message": "Any update?"})
        self.assertEqual(response.status_code, 202)
        self.finish(response.json()["run_id"])
        state = self.client.get("/api/handoffs/300").json()
        self.assertEqual((state["status"], state["status_reason"], state["offers"]),
                         ("handoff_needed", "Asked for a manager.", []))


if __name__ == "__main__":
    unittest.main()
