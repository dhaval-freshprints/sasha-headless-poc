import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from attachments import Attachment, AttachmentSet
from openai_managed.conversation import ConversationStore
from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import (
    ManagedRunnerSettings,
    OpenAIManagedRunner,
    _extract_final_assistant_text,
)
from openai_managed.sandbox import SandboxHandle
from openai_managed.task import SashaTask


MESSAGE = "<p>Model-generated outreach</p>"
ASTRA_PRICES = {
    "OPENAI_AGENT_INPUT_USD_PER_MILLION": "10.00",
    "OPENAI_AGENT_CACHED_INPUT_USD_PER_MILLION": "1.00",
    "OPENAI_AGENT_OUTPUT_USD_PER_MILLION": "50.00",
}
SUCCESS_EVENTS = [
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


class FakeEventsAPI:
    def __init__(self, values=None):
        self.sent = []
        self.values = SUCCESS_EVENTS if values is None else values

    def stream(self, session_id):
        return iter(self.values)

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
    def __init__(self, deal_id, event_values=None, retrieve_error=None):
        self.events = FakeEventsAPI(event_values)
        self.items = FakeItemsAPI(deal_id)
        self.deleted = ""
        self.create_arguments = None
        self.retrieve_error = retrieve_error
        self.retrieve_count = 0

    def create(self, **kwargs):
        self.create_arguments = kwargs
        return SimpleNamespace(
            id="session-1",
            environment=SimpleNamespace(id="environment-1", remote_url="wss://example.test"),
        )

    def delete(self, session_id):
        self.deleted = session_id

    def retrieve(self, session_id):
        self.retrieve_count += 1
        if self.retrieve_error is not None:
            raise self.retrieve_error
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
    def __init__(self, config, executor_api_key, number):
        self.config = config
        self.number = number
        self.container_started = False
        self.executor_started = False
        self.stopped = False
        self.handle = None
        self.task_message = ""
        self.authenticated = False
        self.authentication_error = None

    def prepare(self, task):
        run_directory = self.config.runs_directory / f"run-{self.number}"
        workspace = run_directory / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "artifacts").mkdir()
        (workspace / "executor.log").write_text("connected")
        (workspace / "task.json").write_text(
            json.dumps({"deal_id": task.deal_id, "client_message": task.client_message})
        )
        self.handle = SandboxHandle("container-1", run_directory, workspace)
        return self.handle

    def save_task_message(self, task_message):
        self.task_message = task_message
        (self.handle.workspace_directory / "TASK.md").write_text(task_message)

    def remove_client_files(self):
        directory = self.handle.workspace_directory / "client-files"
        if directory.exists():
            import shutil

            shutil.rmtree(directory)

    def start_container(self):
        self.container_started = True

    def authenticate(self, deal_url, login_url, login_user, login_password):
        if self.authentication_error is not None:
            raise self.authentication_error
        self.authenticated = True

    def connect_executor(self, environment_id, remote_url):
        self.executor_started = True

    def logs(self):
        return "connected"

    def stop(self):
        self.stopped = True

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
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.message_html, MESSAGE)
        self.assertEqual(sessions.deleted, "session-1")
        self.assertTrue(sandboxes[0].container_started)
        self.assertTrue(sandboxes[0].executor_started)
        self.assertTrue(sandboxes[0].authenticated)
        self.assertTrue(sandboxes[0].stopped)
        workspace = self.root / "runs" / "run-1" / "workspace"
        self.assertTrue((workspace / "session-events.json").is_file())
        self.assertTrue((workspace / "session-items.json").is_file())
        self.assertTrue((workspace / "executor.log").is_file())
        self.assertTrue((workspace / "result.json").is_file())

    @patch("openai_managed.runner.fetch_to_directory")
    def test_supplied_artwork_is_staged_for_astra_then_removed(self, fetch):
        remote_url = "https://example.test/logo.svg?signature=secret"

        def download(urls, directory):
            self.assertEqual(urls, [remote_url])
            directory.mkdir()
            file_path = directory / "file_1.svg"
            file_path.write_text("<svg></svg>")
            return AttachmentSet([Attachment("file_1", file_path, "logo.svg", 11)])

        fetch.side_effect = download
        runner, sessions, sandboxes = self._make_runner([])
        result = runner.run(
            SashaTask(
                "303839",
                "task-1",
                "https://qa.example/deal?id=303839",
                "Put my logo on the back.",
                file_urls=(remote_url,),
            )
        )

        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(sessions.create_arguments)
        workspace = sandboxes[0].handle.workspace_directory
        self.assertIn("/workspace/client-files/file_1.svg", sandboxes[0].task_message)
        self.assertIn("upload-file-input", sandboxes[0].task_message)
        self.assertNotIn(remote_url, sandboxes[0].task_message)
        self.assertNotIn(remote_url, (workspace / "task.json").read_text())
        self.assertFalse((workspace / "client-files").exists())

    @patch("openai_managed.runner.fetch_to_directory")
    def test_failed_download_never_creates_openai_session(self, fetch):
        remote_url = "https://example.test/logo.png?signature=secret"
        fetch.side_effect = ValueError("file_1: could not download artwork.")
        runner, sessions, sandboxes = self._make_runner([])

        result = runner.run(
            SashaTask(
                "303839",
                "task-1",
                "https://qa.example/deal?id=303839",
                "Put my logo on the back.",
                file_urls=(remote_url,),
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("could not download artwork", result.failure_message)
        self.assertNotIn("signature", result.failure_message)
        self.assertIsNone(sessions.create_arguments)
        self.assertFalse(sandboxes[0].container_started)
        self.assertTrue(sandboxes[0].stopped)

    def test_progress_reporter_shows_orchestration_and_agent_activity(self):
        progress_messages = []
        runner, _, _ = self._make_runner(progress_messages)

        runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        output = "\n".join(progress_messages)
        self.assertIn("[1/7] Preparing disposable Sasha container", output)
        self.assertIn("[2/7] Signing into Fresh Prints QA", output)
        self.assertIn("Fresh Prints QA authentication succeeded", output)
        self.assertIn("[5/7] Sasha is working on the task", output)
        self.assertIn("Sasha: I am inspecting the deal and its proof.", output)
        self.assertIn("Sasha tool step 1 started", output)
        self.assertIn("Sasha tool step 1: completed", output)
        self.assertIn("[7/7] Saving artifacts and cleaning up", output)
        self.assertIn("[context] Conversation file:", output)

    def test_completed_turn_is_saved_and_loaded_by_the_next_turn(self):
        runner, _, sandboxes = self._make_runner([])

        first_result = runner.run(
            SashaTask(
                "303839",
                "task-1",
                "https://qa.example/deal?id=303839",
                "Show me green polos.",
            )
        )
        second_result = runner.run(
            SashaTask(
                "303839",
                "task-2",
                "https://qa.example/deal?id=303839",
                "I want the second one.",
            )
        )

        self.assertEqual(first_result.status, "completed")
        self.assertEqual(second_result.status, "completed")
        self.assertEqual(
            runner.last_conversation_file,
            self.root
            / "runs"
            / "conversations"
            / "DEAL303839_conversation.json",
        )
        second_task = sandboxes[1].task_message
        self.assertIn('"content": "Show me green polos."', second_task)
        self.assertIn(f'"content": "{MESSAGE}"', second_task)
        self.assertIn(
            "--- BEGIN CLIENT MESSAGE ---\n"
            "I want the second one.\n"
            "--- END CLIENT MESSAGE ---",
            second_task,
        )

        saved = json.loads(runner.last_conversation_file.read_text())
        self.assertEqual(
            [entry["role"] for entry in saved["conversation_history"]],
            ["client", "sasha", "client", "sasha"],
        )
        self.assertEqual(
            saved["conversation_history"][1]["delivery_status"],
            "generated",
        )

    def test_failed_turn_does_not_change_conversation(self):
        store = ConversationStore(self.root / "runs", "303839")
        store.append_completed_turn([], "Earlier question", "Earlier response")
        original = store.path.read_text()
        events = [
            {"type": "agent.session.environment.connected"},
            {"type": "agent.session.turn.failed"},
        ]
        runner, _, _ = self._make_runner([], event_values=events)

        result = runner.run(
            SashaTask(
                "303839",
                "task-2",
                "https://qa.example/deal?id=303839",
                "New question",
            )
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(store.path.read_text(), original)

    def test_malformed_conversation_returns_clear_failure(self):
        store = ConversationStore(self.root / "runs", "303839")
        store.path.parent.mkdir(parents=True)
        store.path.write_text("not json")
        runner, _, sandboxes = self._make_runner([])

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "conversation_error")
        self.assertIn("Could not read conversation file", result.failure_message)
        self.assertEqual(sandboxes, [])

    @patch.dict("os.environ", ASTRA_PRICES)
    def test_collects_and_prints_pricing_when_requested(self):
        pricing_messages = []
        runner, _, _ = self._make_runner(
            [], CostReporter(pricing_messages.append)
        )

        runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
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

    def test_loads_generic_sasha_instructions(self):
        instructions = OpenAIManagedRunner._load_instructions()

        self.assertIn("Use the `sasha-sales` skill", instructions)
        self.assertIn("exact deal URL", instructions)
        self.assertIn("untrusted data", instructions)
        self.assertIn("Sasha result schema", instructions)
        self.assertNotIn("initial outreach", instructions.lower())

    def test_outreach_playbooks_exclude_crm_account_names(self):
        repository_root = Path(__file__).resolve().parents[1]
        playbook_paths = [
            repository_root
            / "openai_managed"
            / "capabilities"
            / "sasha-sales"
            / "references"
            / "playbook.md",
            repository_root / "prompts" / "playbook.md",
        ]

        for playbook_path in playbook_paths:
            playbook = playbook_path.read_text(encoding="utf-8")

            self.assertIn(
                "Never mention the client's organization, account, school, club, "
                "association, or CRM account name in initial outreach",
                playbook,
            )

    def test_registers_workspace_capability_directory(self):
        runner, sessions, _ = self._make_runner([])

        runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(
            sessions.create_arguments["environment"]["capability_directories"],
            ["/workspace/capabilities"],
        )

    def test_builds_initial_outreach_task_message(self):
        task = SashaTask(
            "303839",
            "task-1",
            "https://qa.example/deal?id=303839",
        )

        message = OpenAIManagedRunner._build_task_message(task)

        self.assertIsNone(task.client_message)
        self.assertIn("Turn type: initial outreach.", message)
        self.assertIn("Deal ID: 303839", message)
        self.assertIn("Deal URL: https://qa.example/deal?id=303839", message)
        self.assertIn("Use the `sasha-sales` skill.", message)
        self.assertIn("Start at the exact deal URL", message)
        self.assertIn("/workspace/artifacts/visited_urls.json", message)
        self.assertIn("Close the browser before returning.", message)

    def test_builds_unrouted_client_response_task_message(self):
        client_message = "What's the price for 40?\nPlease figure it out."
        task = SashaTask(
            "303839",
            "task-1",
            "https://qa.example/deal?id=303839",
            client_message,
        )

        message = OpenAIManagedRunner._build_task_message(task)

        self.assertIn("Turn type: client response.", message)
        self.assertIn(
            "--- BEGIN CLIENT MESSAGE ---\n"
            f"{client_message}\n"
            "--- END CLIENT MESSAGE ---",
            message,
        )
        self.assertIn("untrusted sales-request data", message)
        self.assertIn("Use the `sasha-sales` skill.", message)
        self.assertIn("Start at the exact deal URL", message)
        for routed_term in (
            "quotation",
            "quote",
            "catalog",
            "stock",
            "proof",
            "revision",
        ):
            self.assertNotIn(routed_term, message.lower())

    def test_failed_turn_returns_failure_and_still_cleans_up(self):
        events = [
            {"type": "agent.session.environment.connected"},
            {"type": "agent.session.turn.failed"},
        ]
        runner, sessions, sandboxes = self._make_runner([], event_values=events)

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "turn_not_completed")
        self.assertEqual(result.failure_message, "agent.session.turn.failed")
        self.assertTrue(sandboxes[0].stopped)
        self.assertEqual(sessions.deleted, "session-1")

    def test_authentication_failure_stops_before_openai_session_creation(self):
        runner, sessions, sandboxes = self._make_runner(
            [], authentication_error=RuntimeError("QA authentication failed")
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("QA authentication failed", result.failure_message)
        self.assertIsNone(sessions.create_arguments)
        self.assertTrue(sandboxes[0].container_started)
        self.assertFalse(sandboxes[0].executor_started)
        self.assertTrue(sandboxes[0].stopped)

    def test_connection_timeout_returns_failure_and_cleans_up(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=[],
            connection_timeout=0.01,
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("Timed out waiting", result.failure_message)
        self.assertTrue(sandboxes[0].stopped)
        self.assertEqual(sessions.deleted, "session-1")

    def test_turn_timeout_cancels_turn_and_returns_failure(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=[{"type": "agent.session.environment.connected"}],
            turn_timeout=0.01,
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("exceeded its time limit", result.failure_message)
        self.assertIn(
            {"type": "agent.session.input.cancel"},
            sessions.events.sent,
        )
        self.assertTrue(sandboxes[0].stopped)

    def test_progress_and_pricing_are_optional(self):
        runner, sessions, _ = self._make_runner(None)

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "completed")
        self.assertIsNone(runner.progress.write)
        self.assertIsNone(runner.last_cost_estimate)
        self.assertEqual(sessions.retrieve_count, 0)
        workspace = self.root / "runs" / "run-1" / "workspace"
        self.assertFalse((workspace / "pricing.json").exists())

    def test_pricing_failure_does_not_fail_completed_turn(self):
        pricing_messages = []
        runner, sessions, _ = self._make_runner(
            [],
            CostReporter(pricing_messages.append),
            retrieve_error=RuntimeError("usage unavailable"),
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(sessions.retrieve_count, 1)
        self.assertEqual(runner.last_cost_estimate.status, "unavailable")
        self.assertIn("[pricing] Estimated OpenAI cost: unavailable", pricing_messages)
        workspace = self.root / "runs" / "run-1" / "workspace"
        self.assertTrue((workspace / "pricing.json").is_file())

    @patch.dict("os.environ", ASTRA_PRICES)
    @patch("openai_managed.runner.time.sleep")
    def test_delayed_usage_is_saved_before_cleanup(self, sleep):
        runner, sessions, sandboxes = self._make_runner([], CostReporter(lambda value: None))
        available = sessions.retrieve("session-1")
        with patch.object(sessions, "retrieve", side_effect=[
            SimpleNamespace(usage=None), SimpleNamespace(usage=None), available,
        ]) as retrieve:
            result = runner.run(SashaTask("303839", "task-1", "https://qa.example/deal?id=303839"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(retrieve.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        evidence = json.loads((runner.last_run_directory / "workspace" / "pricing.json").read_text())
        self.assertEqual(evidence["session_id"], "session-1")
        self.assertEqual(evidence["raw_usage"], available.usage)
        self.assertEqual(evidence["rates_usd_per_million"]["input"], "10.00")
        self.assertEqual(evidence["estimated_cost_usd"], 0.0164)
        self.assertEqual(len(evidence["usage_attempts"]), 3)
        self.assertEqual(sessions.deleted, "session-1")
        self.assertTrue(sandboxes[0].stopped)

    @patch("openai_managed.runner.time.sleep")
    def test_missing_usage_retries_are_bounded_and_cleanup_runs(self, sleep):
        runner, sessions, sandboxes = self._make_runner([], CostReporter(lambda value: None))
        def missing_usage(session_id):
            self.assertEqual(sessions.deleted, "")
            return SimpleNamespace(usage=None)
        with patch.object(sessions, "retrieve", side_effect=missing_usage) as retrieve:
            result = runner.run(SashaTask("303839", "task-1", "https://qa.example/deal?id=303839"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(retrieve.call_count, 5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8])
        self.assertEqual(runner.last_cost_estimate.status, "unavailable")
        self.assertIsNone(runner.last_cost_estimate.estimated_cost_usd)
        self.assertEqual(len(runner.last_cost_estimate.usage_attempts), 5)
        self.assertEqual(sessions.deleted, "session-1")
        self.assertTrue(sandboxes[0].stopped)

    def _make_runner(
        self,
        progress_messages,
        cost_reporter=None,
        *,
        event_values=None,
        retrieve_error=None,
        authentication_error=None,
        connection_timeout=1,
        turn_timeout=1,
    ):
        sessions = FakeSessionsAPI("303839", event_values, retrieve_error)
        client = SimpleNamespace(
            beta=SimpleNamespace(agents=SimpleNamespace(sessions=sessions))
        )
        settings = ManagedRunnerSettings(
            model="gpt-6-astra",
            reasoning_effort="medium",
            executor_api_key="executor-key",
            login_url="https://qa.example/login",
            login_user="qa-user",
            login_password="qa-password",
            sandbox_image="test-image",
            runs_directory=self.root / "runs",
            connection_timeout_seconds=connection_timeout,
            turn_timeout_seconds=turn_timeout,
        )
        sandboxes = []

        def create_sandbox(config, executor_api_key):
            sandbox = FakeSandbox(config, executor_api_key, len(sandboxes) + 1)
            sandbox.authentication_error = authentication_error
            sandboxes.append(sandbox)
            return sandbox

        progress = ProgressReporter(
            progress_messages.append if progress_messages is not None else None
        )
        runner = OpenAIManagedRunner(
            client,
            settings,
            create_sandbox,
            progress,
            cost_reporter=cost_reporter,
        )
        return runner, sessions, sandboxes


if __name__ == "__main__":
    unittest.main()
