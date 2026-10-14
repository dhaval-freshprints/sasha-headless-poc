import time
from pathlib import Path

from openai_managed.task import SashaResult
from openai_managed.additional_messages import AdditionalMessageInbox


class FakeRunner:
    def __init__(self, write, root: Path, release=None):
        self.write = write
        self.root = root
        self.release = release
        self.last_run_directory = None
        self.last_cost_estimate = None
        self.additional_messages = None

    def enable_additional_messages(self, report):
        self.additional_messages = AdditionalMessageInbox(report)

    @property
    def accepting_messages(self):
        return bool(self.additional_messages and self.additional_messages.accepting)

    def submit_message(self, submission_id, text):
        self.additional_messages.submit(submission_id, text)

    def run(self, task):
        self.last_task = task
        if self.additional_messages:
            self.additional_messages.open()
        self.write("Fake Sasha inspected the deal.")
        if self.release is not None:
            self.release.wait(2)
        if self.additional_messages:
            while not self.additional_messages.close_if_empty():
                message = self.additional_messages.take()
                self.additional_messages.update(message, "accepted")
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


class PassThroughResolver:
    """Deal check that lets every deal through unchanged (no database or CRM)."""

    def __init__(self):
        self.calls = []

    def resolve(self, deal_id, workflow="client_response"):
        from deal_manager_agent.deal_resolution import Resolution

        self.calls.append((deal_id, workflow))
        return Resolution("proceed", "single_active_deal", deal_id, workflow=workflow)
