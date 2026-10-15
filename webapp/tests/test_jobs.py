from tests.database_helpers import setup_database
import json
import os
import tempfile
import threading
import time
import unittest
from tests.test_account_manager_agent_environment import APPLICATION_VALUES
from pathlib import Path
from unittest.mock import patch

from webapp.jobs import JobManager, WebRun
from webapp.tests.helpers import FakeRunner, wait_for_finish


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        setup_database(self)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = patch.dict(
            os.environ,
            APPLICATION_VALUES,
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_completed_run_is_persisted_and_survives_a_new_manager(self):
        manager = self._manager()
        run = manager.submit("303839", "Price 40 shirts", [])
        completed = wait_for_finish(manager, run.run_id)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.result["message_html"], "<p>Test response</p>")
        self.assertIn("Fake Sasha inspected the deal.", completed.progress)
        self.assertEqual(completed.artifacts, ["proof.txt"])
        manager.shutdown()

        reloaded = self._manager()
        saved = reloaded.get(run.run_id)
        self.assertEqual(saved.status, "completed")
        self.assertEqual(saved.result, completed.result)
        self.assertIsNone(saved.workflow)
        self.assertFalse(hasattr(reloaded._make_task(saved), "workflow"))
        reloaded.shutdown()

    def test_legacy_completed_run_remains_readable_without_guessing_workflow(self):
        jobs_directory = self.root / "jobs"
        jobs_directory.mkdir()
        legacy = WebRun(run_id="b" * 32, deal_id="303839", client_message=None, file_urls=[], status="completed").to_dict()
        legacy.pop("workflow")
        (jobs_directory / f"{legacy['run_id']}.json").write_text(json.dumps(legacy))
        manager = self._manager()
        try:
            saved = manager.get(legacy["run_id"])
            self.assertEqual(saved.status, "completed")
            self.assertIsNone(saved.workflow)
            self.assertFalse(hasattr(manager._make_task(saved), "workflow"))
        finally:
            manager.shutdown()

    def test_historical_workflow_labels_remain_readable_without_rewriting(self):
        directory = self.root / "jobs"
        directory.mkdir()
        for index, workflow in enumerate(("outreach", "client-response-orchestrator")):
            run = WebRun(run_id=str(index + 1) * 32, deal_id="303839", client_message=None,
                         file_urls=[], workflow=workflow, status="completed")
            path = directory / f"{run.run_id}.json"
            original = json.dumps(run.to_dict())
            path.write_text(original)
            manager = self._manager()
            try:
                saved = manager.get(run.run_id)
                self.assertEqual(saved.workflow, workflow)
                self.assertFalse(hasattr(manager._make_task(saved), "workflow"))
                self.assertEqual(path.read_text(), original)
            finally:
                manager.shutdown()

    def test_reading_a_running_job_does_not_interrupt_it(self):
        release = threading.Event()
        manager = JobManager(
            self.root / "jobs",
            lambda write: FakeRunner(write, self.root, release),
        )
        run = manager.submit("303839", None, [])
        self._wait_for_status(manager, run.run_id, "running")

        first_refresh = manager.get(run.run_id)
        second_refresh = manager.get(run.run_id)
        self.assertEqual(first_refresh.status, "running")
        self.assertEqual(second_refresh.status, "running")

        release.set()
        self.assertEqual(wait_for_finish(manager, run.run_id).status, "completed")
        manager.shutdown()

    def test_server_restart_marks_an_unfinished_saved_job_failed(self):
        run = WebRun(
            run_id="a" * 32,
            deal_id="303839",
            client_message=None,
            file_urls=[],
            status="running",
        )
        jobs_directory = self.root / "jobs"
        jobs_directory.mkdir()
        (jobs_directory / f"{run.run_id}.json").write_text(
            json.dumps(run.to_dict()),
            encoding="utf-8",
        )

        manager = self._manager()
        recovered = manager.get(run.run_id)

        self.assertEqual(recovered.status, "failed")
        self.assertIn("web server stopped", recovered.error_message)
        manager.shutdown()

    def test_artifacts_cannot_escape_the_artifact_directory(self):
        manager = self._manager()
        run = manager.submit("303839", None, [])
        wait_for_finish(manager, run.run_id)

        self.assertEqual(manager.artifact_path(run.run_id, "proof.txt").read_text(), "proof")
        with self.assertRaises(FileNotFoundError):
            manager.artifact_path(run.run_id, "../task.json")
        manager.shutdown()

    def test_follow_up_submit_passes_stage_mocks_to_task(self):
        runners = []

        def factory(write):
            runner = FakeRunner(write, self.root)
            runners.append(runner)
            return runner

        manager = JobManager(self.root / "jobs", factory)
        run = manager.submit(
            "303839",
            None,
            [],
            turn_type="follow_up",
            as_of_date="2026-10-07",
            days_since_client_reply=14,
        )
        completed = wait_for_finish(manager, run.run_id)

        self.assertEqual(completed.turn_type, "follow_up")
        self.assertIsNone(completed.follow_up_stage)
        task = runners[0].last_task
        self.assertEqual(task.turn_type, "follow_up")
        self.assertEqual(task.as_of_date, "2026-10-07")
        self.assertEqual(task.days_since_client_reply, 14)
        self.assertIsNone(task.client_message)
        manager.shutdown()

    def test_legacy_turn_labels_are_read_without_rewriting(self):
        directory = self.root / "jobs"
        directory.mkdir()
        for index, turn_type in enumerate(("outreach", "response", "follow_up")):
            run = WebRun(run_id=str(index + 1) * 32, deal_id="303839", client_message=None,
                         file_urls=[], turn_type=turn_type, status="completed")
            path = directory / f"{run.run_id}.json"
            original = json.dumps(run.to_dict())
            path.write_text(original)
            manager = self._manager()
            try:
                self.assertEqual(manager.get(run.run_id).turn_type, turn_type)
                self.assertEqual(path.read_text(), original)
            finally:
                manager.shutdown()

    def _manager(self):
        return JobManager(
            self.root / "jobs",
            lambda write: FakeRunner(write, self.root),
        )

    def _wait_for_status(self, manager, run_id, status):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if manager.get(run_id).status == status:
                return
            time.sleep(0.01)
        self.fail(f"Run did not reach {status}")


if __name__ == "__main__":
    unittest.main()
