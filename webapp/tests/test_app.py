import os
import tempfile
import threading
import time
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
    def test_additional_message_endpoint_routes_to_active_runner_and_deduplicates(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.manager.runner_factory = lambda write: FakeRunner(write, self.root, release)
        run = self.client.post("/api/runs", json={
            "deal_id": "303839",
        }).json()
        run_id = run["run_id"]
        deadline = time.monotonic() + 1
        while not self.manager.get(run_id).accepting_messages:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        url = f"/api/runs/{run_id}/messages"
        message = {"submission_id": "correction", "text": "Black shirts for 60"}
        response = self.client.post(url, json=message)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.client.post(url, json=message).json(), response.json())
        conflict = self.client.post(url, json={**message, "text": "Different message"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(self.manager.get(run_id).messages), 1)
        self.assertEqual(len(self.manager.active_runners[run_id].additional_messages.messages), 1)
        release.set()
        completed = wait_for_finish(self.manager, run_id)
        self.assertEqual(completed.messages[0]["status"], "accepted")
        self.assertFalse(completed.accepting_messages)
        self.assertNotIn(run_id, self.manager.active_runners)
        self.assertEqual(self.client.post(url, json=message).json()["status"], "accepted")
        late = self.client.post(url, json={"submission_id": "late", "text": "Another"})
        self.assertEqual(late.status_code, 409)
        saved = JobManager(self.root / "jobs", self.manager.runner_factory)
        self.addCleanup(saved.shutdown)
        self.assertEqual(saved.get(run_id).messages, completed.messages)

    def test_additional_message_endpoint_rejects_blank_unknown_and_finished_runs(self):
        url = "/api/runs/missing/messages"
        self.assertEqual(self.client.post(url, json={"submission_id": "one", "text": "  "}).status_code, 422)
        self.assertEqual(self.client.post(url, json={"submission_id": "one", "text": "Hello"}).status_code, 404)
        run = self.manager.submit("303839", None, [])
        wait_for_finish(self.manager, run.run_id)
        response = self.client.post(f"/api/runs/{run.run_id}/messages", json={"submission_id": "one", "text": "Hello"})
        self.assertEqual(response.status_code, 409)

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

    def test_rejects_obsolete_workflow_before_creating_jobs(self):
        for workflow in ("outreach", "client-response-orchestrator", "unknown", None):
            with self.subTest(workflow=workflow):
                response = self.client.post("/api/runs", json={"deal_id": "303839", "workflow": workflow})
                self.assertEqual(response.status_code, 422)
                self.assertIn("Remove workflow", response.text)
        self.assertEqual(self.manager.list_all(), [])
        self.assertEqual(self.deals.list_all(), [])

    def test_optional_client_message_is_normalized_and_passed_to_task(self):
        for message in (None, "", "  ", "Context"):
            with self.subTest(message=message):
                response = self.client.post("/api/runs", json={"deal_id": "303839", "client_message": message})
                self.assertEqual(response.status_code, 202)
                run = wait_for_finish(self.manager, response.json()["run_id"])
                self.assertEqual(self.manager._make_task(run).client_message, message if message == "Context" else None)
                self.assertIsNone(run.workflow)

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
                "file_urls": [],
            },
        )

        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        page = self.client.get(f"/runs/{run_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Account Manager Console", page.text)

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
            json={"deal_id": "303901", "client_message": None, "file_urls": []},
        ).json()
        second = self.client.post(
            "/api/runs",
            json={
                "deal_id": "303901",
                "client_message": "Show another option",
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
