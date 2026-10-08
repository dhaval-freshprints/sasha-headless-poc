import os
import tempfile
import unittest
from tests.test_openai_managed_environment import APPLICATION_VALUES
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp.app import create_app, _jobs_directory, _deals_directory
from webapp.deals import DealStore
from webapp.jobs import JobManager
from webapp.tests.helpers import FakeRunner, wait_for_finish


class WebAppTests(unittest.TestCase):
    def test_web_storage_is_separate_for_each_environment(self):
        for environment in ["qa", "production"]:
            with patch.dict(os.environ, {
                **APPLICATION_VALUES,
                "FP_ENVIRONMENT": environment,
                "SASHA_WEB_JOBS_DIRECTORY": str(self.root / "jobs"),
                "SASHA_WEB_DEALS_DIRECTORY": str(self.root / "deals"),
            }):
                self.assertEqual(_jobs_directory(), self.root / "jobs" / environment)
                self.assertEqual(_deals_directory(), self.root / "deals" / environment)

    def test_rejects_missing_or_invalid_workflow_before_creating_jobs(self):
        for selection in ({}, {"workflow": "unknown"}, {"workflow": "client-response-orchestrator", "client_message": "  "}):
            with self.subTest(selection=selection):
                response = self.client.post("/api/runs", json={"deal_id": "303839", **selection})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manager.list_all(), [])
        self.assertEqual(self.deals.list_all(), [])

    def test_explicit_outreach_is_preserved_when_message_is_supplied(self):
        response = self.client.post("/api/runs", json={
            "deal_id": "303839", "workflow": "outreach", "client_message": "Context",
        })
        self.assertEqual(response.status_code, 202)
        run = wait_for_finish(self.manager, response.json()["run_id"])
        self.assertEqual(run.workflow, "outreach")
        self.assertEqual(self.manager._make_task(run).workflow, "outreach")

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = patch.dict(
            os.environ,
            APPLICATION_VALUES,
        )
        self.environment.start()
        self.manager = JobManager(
            self.root / "jobs",
            lambda write: FakeRunner(write, self.root),
        )
        self.deals = DealStore(self.root / "deals")
        self.client_context = TestClient(create_app(self.manager, self.deals))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_creates_run_and_serves_refreshable_run_page(self):
        response = self.client.post(
            "/api/runs",
            json={
                "deal_id": "303839",
                "client_message": "Price 40 shirts",
                "workflow": "client-response-orchestrator",
                "file_urls": [],
            },
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        page = self.client.get(f"/runs/{run_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Sasha Test Console", page.text)

        completed = wait_for_finish(self.manager, run_id)
        status = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(status.json()["status"], "completed")
        self.assertEqual(completed.result["message_html"], "<p>Test response</p>")

        artifact = self.client.get(f"/api/runs/{run_id}/artifacts/proof.txt")
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(artifact.text, "proof")

        deal = self.client.get("/api/deals/303839").json()
        self.assertEqual(deal["run_count"], 1)
        self.assertEqual(deal["runs"][0]["run_id"], run_id)

    def test_adds_empty_deal_without_starting_a_run(self):
        response = self.client.post("/api/deals", json={"deal_id": "303900"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["run_count"], 0)
        self.assertIsNone(response.json()["latest_run"])
        detail = self.client.get("/api/deals/303900")
        self.assertEqual(detail.json()["runs"], [])
        page = self.client.get("/deals/303900")
        self.assertEqual(page.status_code, 200)

    def test_lists_multiple_previous_runs_for_a_deal(self):
        self.client.post("/api/deals", json={"deal_id": "303901"})
        first = self.client.post(
            "/api/runs",
            json={"deal_id": "303901", "client_message": None, "file_urls": [], "workflow": "outreach"},
        ).json()
        second = self.client.post(
            "/api/runs",
            json={
                "deal_id": "303901",
                "client_message": "Show another option",
                "workflow": "client-response-orchestrator",
                "file_urls": [],
            },
        ).json()
        wait_for_finish(self.manager, first["run_id"])
        wait_for_finish(self.manager, second["run_id"])

        deal = self.client.get("/api/deals/303901").json()

        self.assertEqual(deal["run_count"], 2)
        self.assertEqual(
            {run["run_id"] for run in deal["runs"]},
            {first["run_id"], second["run_id"]},
        )

    def test_rejects_invalid_deal_id_and_artwork_url(self):
        response = self.client.post(
            "/api/runs",
            json={
                "deal_id": "deal-303839",
                "client_message": "",
                "workflow": "outreach",
                "file_urls": ["file:///tmp/logo.png"],
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_unknown_run_returns_not_found(self):
        response = self.client.get("/api/runs/missing")

        self.assertEqual(response.status_code, 404)

    def test_unknown_deal_returns_not_found(self):
        response = self.client.get("/api/deals/999999")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
