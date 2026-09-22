import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from openai_managed.outreach import OutreachTask
from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import (
    ManagedRunnerSettings,
    OpenAIManagedOutreachRunner,
    _extract_final_assistant_text,
)
from openai_managed.sandbox import SandboxHandle


MESSAGE = "<p>Model-generated outreach</p>"


class FakeEventsAPI:
    def __init__(self):
        self.sent = []

    def stream(self, session_id):
        return iter(
            [
                {"type": "agent.session.environment.connected"},
                {
                    "type": "agent.session.turn.item.added",
                    "item": {
                        "id": "commentary-1",
                        "type": "message",
                        "phase": "commentary",
                    },
                },
                {
                    "type": "agent.session.turn.output_text.done",
                    "item_id": "commentary-1",
                    "text": "I am inspecting the deal and its proof.",
                },
                {
                    "type": "agent.session.turn.item.added",
                    "item": {"id": "command-1", "type": "command_execution"},
                },
                {
                    "type": "agent.session.turn.item.done",
                    "item": {
                        "id": "command-1",
                        "type": "command_execution",
                        "status": "completed",
                    },
                },
                {"type": "agent.session.turn.completed"},
            ]
        )

    def create(self, session_id, events, idempotency_key=None):
        self.sent.extend(events)


class FakeItemsAPI:
    def __init__(self, deal_id):
        self.deal_id = deal_id

    def list(self, session_id, order, limit):
        result = {
            "deal_id": self.deal_id,
            "status": "completed",
            "message_html": MESSAGE,
            "failure_code": "",
            "failure_message": "",
        }
        item = {
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": json.dumps(result)}],
        }
        return SimpleNamespace(data=[item])


class FakeSessionsAPI:
    def __init__(self, deal_id):
        self.events = FakeEventsAPI()
        self.items = FakeItemsAPI(deal_id)
        self.deleted = ""

    def create(self, **kwargs):
        return SimpleNamespace(
            id="session-1",
            environment=SimpleNamespace(id="environment-1", remote_url="wss://example.test"),
        )

    def delete(self, session_id):
        self.deleted = session_id

    def retrieve(self, session_id):
        return SimpleNamespace(
            usage={
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 400},
                "output_tokens": 200,
                "output_tokens_details": {"reasoning_tokens": 50},
                "total_tokens": 1200,
            }
        )


class FakeSandbox:
    def __init__(self, config, executor_api_key):
        self.config = config
        self.started = False
        self.stopped = False
        self.profile_removed = False
        self.handle = None

    def prepare(self, task, task_message):
        run_directory = self.config.runs_directory / "run-1"
        workspace = run_directory / "workspace"
        profile = workspace / "browser-profile"
        profile.mkdir(parents=True)
        (workspace / "artifacts").mkdir()
        self.handle = SandboxHandle("container-1", run_directory, workspace, profile)
        return self.handle

    def start(self, environment_id, remote_url):
        self.started = True

    def logs(self):
        return "connected"

    def stop(self):
        self.stopped = True

    def remove_browser_profile(self):
        self.profile_removed = True


class ManagedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_runs_session_without_browser_checker_or_message_validation(self):
        progress_messages = []
        runner, sessions, sandboxes = self._make_runner(progress_messages)

        result = runner.run(
            OutreachTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.message_html, MESSAGE)
        self.assertEqual(sessions.deleted, "session-1")
        self.assertTrue(sandboxes[0].started)
        self.assertTrue(sandboxes[0].stopped)
        self.assertTrue(sandboxes[0].profile_removed)
        workspace = self.root / "runs" / "run-1" / "workspace"
        self.assertTrue((workspace / "session-events.json").is_file())
        self.assertTrue((workspace / "session-items.json").is_file())
        self.assertTrue((workspace / "executor.log").is_file())
        self.assertTrue((workspace / "result.json").is_file())

    def test_progress_reporter_shows_orchestration_and_agent_activity(self):
        progress_messages = []
        runner, _, _ = self._make_runner(progress_messages)

        runner.run(
            OutreachTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        output = "\n".join(progress_messages)
        self.assertIn("[1/6] Preparing disposable browser workspace", output)
        self.assertIn("Sasha: I am inspecting the deal and its proof.", output)
        self.assertIn("Sasha tool step 1 started", output)
        self.assertIn("Sasha tool step 1: completed", output)
        self.assertIn("[6/6] Saving artifacts and cleaning up", output)

    def test_collects_and_prints_pricing_when_requested(self):
        pricing_messages = []
        runner, _, _ = self._make_runner(
            [], CostReporter(pricing_messages.append)
        )

        runner.run(
            OutreachTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(runner.last_cost_estimate.status, "estimated")
        self.assertEqual(runner.last_cost_estimate.estimated_cost_usd, 0.0164)
        self.assertIn("[pricing] Estimated OpenAI cost: $0.01640000", pricing_messages)
        workspace = self.root / "runs" / "run-1" / "workspace"
        self.assertTrue((workspace / "pricing.json").is_file())

    def test_extracts_final_answer_instead_of_earlier_text(self):
        items = [
            {"role": "assistant", "content": [{"type": "output_text", "text": "one"}]},
            {
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "two"}],
            },
        ]
        self.assertEqual(_extract_final_assistant_text(items), "two")

    def _make_runner(self, progress_messages, cost_reporter=None):
        sessions = FakeSessionsAPI("303839")
        client = SimpleNamespace(
            beta=SimpleNamespace(agents=SimpleNamespace(sessions=sessions))
        )
        settings = ManagedRunnerSettings(
            model="gpt-6-astra",
            reasoning_effort="medium",
            executor_api_key="executor-key",
            sandbox_image="test-image",
            auth_directory=self.root / "auth",
            runs_directory=self.root / "runs",
            connection_timeout_seconds=1,
            turn_timeout_seconds=1,
        )
        sandboxes = []

        def create_sandbox(config, executor_api_key):
            sandbox = FakeSandbox(config, executor_api_key)
            sandboxes.append(sandbox)
            return sandbox

        progress = ProgressReporter(progress_messages.append)
        runner = OpenAIManagedOutreachRunner(
            client,
            settings,
            create_sandbox,
            progress,
            cost_reporter=cost_reporter,
        )
        return runner, sessions, sandboxes


if __name__ == "__main__":
    unittest.main()
