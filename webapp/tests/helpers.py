import time
from pathlib import Path

from openai_managed.task import SashaResult


class FakeRunner:
    def __init__(self, write, root: Path, release=None):
        self.write = write
        self.root = root
        self.release = release
        self.last_run_directory = None
        self.last_cost_estimate = None

    def run(self, task):
        self.write("Fake Sasha inspected the deal.")
        if self.release is not None:
            self.release.wait(2)
        self.last_run_directory = self.root / f"artifact-{task.task_id}"
        artifact_directory = self.last_run_directory / "workspace" / "artifacts"
        artifact_directory.mkdir(parents=True)
        (artifact_directory / "proof.txt").write_text("proof", encoding="utf-8")
        return SashaResult(
            deal_id=task.deal_id,
            status="completed",
            message_html="<p>Test response</p>",
        )


def wait_for_finish(manager, run_id, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = manager.get(run_id)
        if run.status in {"completed", "failed"}:
            return run
        time.sleep(0.01)
    raise AssertionError("Run did not finish before the test timeout")
