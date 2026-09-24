import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp.jobs import JobManager, WebRun
from webapp.tests.helpers import FakeRunner, wait_for_finish


class JobManagerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = patch.dict(
            os.environ,
            {"FP_BASE_URL": "https://qa.example"},
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
        reloaded.shutdown()

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
