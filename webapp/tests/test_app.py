from tests.database_helpers import setup_database
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
from webapp.tests.helpers import FakeRunner, PassThroughResolver, wait_for_finish


class WebAppTests(unittest.TestCase):
    def test_structured_message_persists_metadata_before_run(self):
        response = self.client.post("/api/runs", json={
            "deal_id": "303839", "incoming_message": {
                "body": "Proof ready", "sender_name": "art@freshprints.com",
                "subject": "Ready", "external_message_id": "provider-1",
                "external_thread_id": "thread-1", "message_timestamp": "2026-10-14T10:00:00Z",
            },
        })
        self.assertEqual(response.status_code, 202)
        run = wait_for_finish(self.manager, response.json()["run_id"])
        message = run.incoming_messages[0]
        self.assertEqual(message["convo_type"], "internal_mail")
        self.assertEqual(message["external_message_id"], "provider-1")
        self.assertEqual(message["subject"], "Ready")
        self.assertEqual(run.deal_check["workflow"], "internal_email")
        self.assertEqual(self.database.statuses["303839"], "active")

    def test_input_db_failure_prevents_job_creation(self):
        from sasha_database.connection import DatabaseError
        with patch.object(self.database, "prepare_run", side_effect=DatabaseError("Unavailable")):
            response = self.client.post("/api/runs", json={"deal_id": "303839", "client_message": "Hello"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.manager.list_all(), [])

    def test_notes_db_error_does_not_look_like_empty_notes(self):
        from sasha_database.connection import DatabaseError
        with patch.object(self.database, "load_notes", side_effect=DatabaseError("Unavailable")):
            response = self.client.get("/api/deals/303839/notes")
        self.assertEqual(response.status_code, 503)

    def test_oversized_metadata_is_rejected_before_a_run(self):
        response = self.client.post("/api/runs", json={
            "deal_id": "303839", "incoming_message": {"body": "Hello", "subject": "x" * 501},
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manager.list_all(), [])

    def test_followup_validation_and_metadata(self):
        valid = {"deal_id": "303839", "turn_type": "follow_up",
                 "as_of_date": "2026-10-13", "days_since_client_reply": 99}
        invalid = [
            {"turn_type": "outreach"}, {"turn_type": "response"}, {"turn_type": None},
            {"follow_up_stage": 1}, {"follow_up_stage": None},
            {"as_of_date": None}, {"as_of_date": "2026-02-30"}, {"as_of_date": "20261013"},
            {"days_since_client_reply": -1}, {"days_since_client_reply": True},
            {"client_message": "Hello"}, {"file_urls": ["https://example.com/art.png"]},
            {"workflow": "outreach"},
        ]
        for changed in invalid:
            with self.subTest(changed=changed):
                response = self.client.post("/api/runs", json={**valid, **changed})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manager.list_all(), [])
        self.assertEqual(self.deals.list_all(), [])
        response = self.client.post("/api/runs", json={**valid, "client_message": "  "})
        self.assertEqual(response.status_code, 202)
        run = wait_for_finish(self.manager, response.json()["run_id"])
        self.assertIsNone(run.follow_up_stage)
        self.assertEqual(run.days_since_client_reply, 99)
        self.assertEqual(self.manager._make_task(run).turn_type, "follow_up")

    def test_notes_use_database_and_ignore_legacy_files(self):
        self.database.notes["303839"] = "Database notes"
        directory = self.root / "notes"
        directory.mkdir()
        (directory / "deal_303839_notes.md").write_text("Dead legacy notes")
        self.assertEqual(self.client.get("/api/deals/303839/notes").json()["notes"], "Database notes")
        self.assertEqual(self.client.get("/api/deals/999999/notes").json()["notes"], "")
        self.assertEqual(self.client.get("/api/deals/invalid/notes").status_code, 422)
        for path in ("/", "/static/app.js", "/static/styles.css"):
            self.assertEqual(self.client.get(path).headers["Cache-Control"], "no-store")

    def test_followup_active_messages_remain_available(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.manager.runner_factory = lambda write: FakeRunner(write, self.root, release)
        run = self.client.post("/api/runs", json={"deal_id": "303839", "turn_type": "follow_up",
            "as_of_date": "2026-10-13"}).json()
        deadline = time.monotonic() + 1
        while not self.manager.get(run["run_id"]).accepting_messages:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        response = self.client.post(f"/api/runs/{run['run_id']}/messages",
                                   json={"submission_id": "inbound", "text": "Are samples free?"})
        self.assertEqual(response.status_code, 202)
        release.set()
        completed = wait_for_finish(self.manager, run["run_id"])
        self.assertEqual(completed.messages[0]["status"], "accepted")

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

    def test_every_run_is_checked_with_the_workflow_from_the_message(self):
        runs = [self.client.post("/api/runs", json={"deal_id": "303839"}).json(),
                self.client.post("/api/runs", json={"deal_id": "303839", "client_message": "Price for 40?"}).json()]
        self.assertEqual(self.resolver.calls, [("303839", "outreach"), ("303839", "client_response")])
        for run in runs:
            wait_for_finish(self.manager, run["run_id"])

    def test_run_starts_on_the_kept_deal_and_saves_the_check(self):
        from deal_manager_agent.deal_resolution import Resolution
        self.resolver.resolve = lambda deal_id, workflow: Resolution(
            "proceed", "consolidated_for_client_response", "303840", {"canonical_deal_id": 303840})
        run = self.client.post("/api/runs", json={"deal_id": "303839", "client_message": "Hi"}).json()
        self.assertEqual(run["deal_id"], "303840")
        self.assertEqual(run["deal_check"]["reason"], "consolidated_for_client_response")
        self.assertIn("Merged", run["deal_check"]["summary"])
        wait_for_finish(self.manager, run["run_id"])

    def test_handed_off_or_skipped_check_starts_nothing(self):
        from deal_manager_agent.deal_resolution import Resolution
        for status, reason in (("handoff", "incoming_deal_not_active"),
                               ("skipped", "consolidated_canonical_already_outreached")):
            with self.subTest(status=status):
                self.resolver.resolve = lambda deal_id, workflow: Resolution(status, reason, None)
                response = self.client.post("/api/runs", json={"deal_id": "303839"})
                self.assertEqual(response.status_code, 409)
                self.assertIn("Account Manager", response.json()["detail"])
        self.assertEqual(self.manager.list_all(), [])

    def test_outreach_run_without_client_message_succeeds(self):
        response = self.client.post("/api/runs", json={"deal_id": "303839"})
        self.assertEqual(response.status_code, 202)
        run = wait_for_finish(self.manager, response.json()["run_id"])
        self.assertIsNone(run.client_message)
        self.assertIsNone(run.workflow)
        self.assertIsNone(self.manager._make_task(run).client_message)

    def test_optional_client_message_is_normalized_and_passed_to_task(self):
        for message in (None, "", "  ", "Context"):
            with self.subTest(message=message):
                response = self.client.post("/api/runs", json={"deal_id": "303839", "client_message": message})
                self.assertEqual(response.status_code, 202)
                run = wait_for_finish(self.manager, response.json()["run_id"])
                self.assertEqual(self.manager._make_task(run).client_message, message if message == "Context" else None)
                self.assertIsNone(run.workflow)

    def setUp(self):
        setup_database(self)
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
        self.resolver = PassThroughResolver()
        self.client_context = TestClient(create_app(self.manager, self.deals, deal_resolver=self.resolver))
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
