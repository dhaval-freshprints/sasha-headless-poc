import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openai_managed.attachments import Attachment, AttachmentSet
from openai_managed.conversation import ConversationStore
from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import (
    STREAM_ERROR_EVENT,
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
CONNECTED_ONLY = [{"type": "agent.session.environment.connected"}]
CANCEL_EVENT = {"type": "agent.session.input.cancel"}
TASK_URL = "https://qa.example/deal?id=303839"


def _command_added(item_id, command):
    return {
        "type": "agent.session.turn.item.added",
        "item": {"id": item_id, "type": "command_execution", "command": command},
    }


def _command_done(item_id, command, output, exit_code):
    return {
        "type": "agent.session.turn.item.done",
        "item": {
            "id": item_id,
            "type": "command_execution",
            "command": command,
            "output": output,
            "exit_code": exit_code,
            "status": "completed",
        },
    }


class FakeConflictError(Exception):
    status_code = 409


class FakeEventsAPI:
    def __init__(self, values, operations):
        self.sent = []
        self.values = SUCCESS_EVENTS if values is None else values
        self.operations = operations
        self.stays_open = False
        self.stream_error = None
        self.send_error = None
        self.cancel_error = None
        self.released = threading.Event()

    def stream(self, session_id):
        yield from self.values
        if self.stream_error is not None:
            raise self.stream_error
        if self.stays_open:
            self.released.wait(5)
            if CANCEL_EVENT in self.sent:
                yield {"type": "agent.session.turn.cancelled"}

    def create(self, session_id, events, idempotency_key=None):
        if CANCEL_EVENT in events:
            self._cancel()
        elif self.send_error is not None:
            raise self.send_error
        self.sent.extend(events)

    def _cancel(self):
        self.operations.append("cancel")
        if self.cancel_error is not None:
            raise self.cancel_error
        self.released.set()


class FakeCursorPage:
    """Like the SDK page: data is the first page, iterating walks every page."""

    def __init__(self, items, limit):
        self.items = items
        self.data = items[:limit]

    def __iter__(self):
        return iter(self.items)


class FakeItemsAPI:
    def __init__(self, deal_id):
        self.deal_id = deal_id
        self.commentary_count = 0

    def list(self, session_id, order, limit):
        items = [self._commentary_item() for _ in range(self.commentary_count)]
        items.append(self._final_item())
        return FakeCursorPage(items, limit)

    @staticmethod
    def _commentary_item():
        return {
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking the catalog."}],
        }

    def _final_item(self):
        result = {
            "deal_id": self.deal_id,
            "status": "completed",
            "message_html": MESSAGE,
            "failure_code": "",
            "failure_message": "",
        }
        return {
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": json.dumps(result)}],
        }


class FakeSessionsAPI:
    def __init__(self, deal_id, event_values=None, retrieve_error=None, operations=None):
        self.operations = [] if operations is None else operations
        self.events = FakeEventsAPI(event_values, self.operations)
        self.items = FakeItemsAPI(deal_id)
        self.deleted = ""
        self.delete_outcomes = []
        self.delete_attempts = 0
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
        self.operations.append("delete")
        self.delete_attempts += 1
        self.events.released.set()
        if self.delete_outcomes:
            error = self.delete_outcomes.pop(0)
            if error is not None:
                raise error
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
    def __init__(self, config, executor_api_key, number, operations=None):
        self.config = config
        self.number = number
        self.operations = [] if operations is None else operations
        self.container_started = False
        self.executor_started = False
        self.stopped = False
        self.handle = None
        self.task_message = ""
        self.authenticated = False
        self.authentication_error = None
        self.browser_started = False
        self.browser_error = None

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

    def start_browser(self):
        if self.browser_error is not None:
            raise self.browser_error
        self.browser_started = True

    def connect_executor(self, environment_id, remote_url):
        self.executor_started = True

    def logs(self):
        return "connected"

    def executor_exit_code(self):
        return None

    def stop(self):
        self.operations.append("stop")
        self.stopped = True

class ManagedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_environment_defaults_to_twenty_minute_turn_limit(self):
        environment = {
            "OPENAI_EXECUTOR_API_KEY": "executor-key",
            "FP_LOGIN_URL": "https://qa.example/login",
            "FP_USER": "qa-user",
            "FP_PASSWORD": "qa-password",
        }

        with patch.dict("os.environ", environment, clear=True):
            settings = ManagedRunnerSettings.from_environment()

        self.assertEqual(settings.turn_timeout_seconds, 1200)

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
        self.assertNotIn(CANCEL_EVENT, sessions.events.sent)
        self.assertEqual(sessions.operations, ["delete", "stop"])
        cleanup = json.loads((workspace / "cleanup.json").read_text())
        self.assertEqual(cleanup["session_id"], "session-1")
        self.assertEqual(cleanup["delete_attempts"], 1)
        self.assertTrue(cleanup["session_deleted"])
        self.assertFalse(cleanup["cancel_sent"])
        self.assertEqual(cleanup["errors"], [])

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

    def test_reads_final_answer_after_first_hundred_items(self):
        runner, sessions, _ = self._make_runner([])
        sessions.items.commentary_count = 100

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.message_html, MESSAGE)
        workspace = self.root / "runs" / "run-1" / "workspace"
        saved_items = json.loads((workspace / "session-items.json").read_text())
        self.assertEqual(len(saved_items), 101)

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
            / "playbook.md"
        ]

        for playbook_path in playbook_paths:
            playbook = playbook_path.read_text(encoding="utf-8")

            self.assertIn(
                "Never mention the client's organization, account, school, club, "
                "association, or CRM account name in initial outreach",
                playbook,
            )

    def test_playbooks_use_natural_product_references(self):
        repository_root = Path(__file__).resolve().parents[1]
        playbook_paths = [
            repository_root
            / "openai_managed"
            / "capabilities"
            / "sasha-sales"
            / "references"
            / "playbook.md"
        ]

        for playbook_path in playbook_paths:
            playbook = playbook_path.read_text(encoding="utf-8")

            self.assertIn(
                "Use full catalog names when first presenting or comparing products",
                playbook,
            )
            self.assertIn(
                "After the client selects a product, use the shortest natural name",
                playbook,
            )
            self.assertIn(
                "Never call a garment",
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
        self.assertIn("Do not launch Chrome yourself.", message)

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
        self.assertNotIn("cancel", sessions.operations)

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

    def test_browser_start_failure_stops_before_openai_session_creation(self):
        runner, sessions, sandboxes = self._make_runner(
            [], browser_error=RuntimeError("Browser did not start: keeper exited")
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "browser_start_failed")
        self.assertIn("keeper exited", result.failure_message)
        self.assertIsNone(sessions.create_arguments)
        self.assertFalse(sandboxes[0].executor_started)
        self.assertTrue(sandboxes[0].stopped)

    def test_browser_starts_after_login_and_task_tells_sasha_to_attach(self):
        progress_messages = []
        runner, _, sandboxes = self._make_runner(progress_messages)

        runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertTrue(sandboxes[0].browser_started)
        self.assertIn(
            "      Signed-in browser is open for the whole turn", progress_messages
        )
        task_message = sandboxes[0].task_message
        self.assertIn("chromium.connectOverCDP('http://127.0.0.1:9222')", task_message)
        self.assertIn("Do not launch Chrome yourself", task_message)
        self.assertIn("node /opt/sasha/start_browser.js", task_message)
        self.assertNotIn("Close the browser", task_message)

    def test_cleanup_reports_workspace_scripts_that_launch_chrome(self):
        progress_messages = []
        runner, _, _ = self._make_runner(progress_messages)
        workspace = self.root / "workspace-with-scripts"
        (workspace / "capabilities" / "sasha-sales").mkdir(parents=True)
        (workspace / "inspect.js").write_text(
            "const c = await chromium.launchPersistentContext('/browser-profile', {});"
        )
        (workspace / "attach.js").write_text(
            "const b = await chromium.connectOverCDP('http://127.0.0.1:9222');"
        )
        (workspace / "capabilities" / "sasha-sales" / "tool.js").write_text(
            "chromium.launch({})"
        )

        runner._report_scripts_that_launch_chrome(workspace)

        self.assertEqual(
            progress_messages,
            ["[cleanup] WARNING: Sasha's script inspect.js launches its own Chrome"],
        )

    def test_progress_reports_browser_restarts_and_own_chrome_launches(self):
        restart = "/bin/bash -lc 'node /opt/sasha/start_browser.js'"
        attach = (
            "/bin/bash -lc \"node -e \\\"const b=await chromium.connectOverCDP("
            "'http://127.0.0.1:9222')\\\"\""
        )
        own_chrome = (
            "/bin/bash -lc \"node -e \\\"chromium.launchPersistentContext("
            "'/browser-profile')\\\"\""
        )
        events = [
            {"type": "agent.session.environment.connected"},
            _command_added("command-1", restart),
            _command_done("command-1", restart, '{"status":"started"}', 0),
            _command_added("command-2", attach),
            _command_done("command-2", attach, "READY", 0),
            _command_added("command-3", own_chrome),
            _command_done("command-3", own_chrome, "", 0),
            {"type": "agent.session.turn.completed"},
        ]
        progress_messages = []
        runner, _, _ = self._make_runner(progress_messages, event_values=events)

        runner.run(SashaTask("303839", "task-1", TASK_URL))

        browser_lines = [
            line.strip()
            for line in progress_messages
            if "restarted the browser" in line or "own Chrome" in line
        ]
        self.assertEqual(
            browser_lines,
            [
                "Sasha restarted the browser: started",
                "WARNING: Sasha launched its own Chrome",
            ],
        )

    def test_connection_timeout_returns_failure_and_cleans_up(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=[],
            connection_timeout=0.01,
            stays_open=True,
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("Timed out waiting", result.failure_message)
        self.assertTrue(sandboxes[0].stopped)
        self.assertEqual(sessions.deleted, "session-1")
        self.assertEqual(sessions.operations, ["delete", "stop"])

    def test_turn_timeout_cancels_turn_and_returns_failure(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=CONNECTED_ONLY,
            turn_timeout=0.01,
            stays_open=True,
        )

        result = runner.run(
            SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("exceeded its time limit", result.failure_message)
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])
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

    @patch.dict("os.environ", ASTRA_PRICES)
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

    def test_stream_exception_cancels_turn_and_keeps_stream_error(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=CONNECTED_ONLY,
            stream_error=ConnectionError("stream dropped"),
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("Session event stream failed: stream dropped", result.failure_message)
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])
        workspace = runner.last_run_directory / "workspace"
        saved_events = json.loads((workspace / "session-events.json").read_text())
        self.assertIn(
            {
                "type": STREAM_ERROR_EVENT,
                "error_type": "ConnectionError",
                "message": "stream dropped",
            },
            saved_events,
        )
        cleanup = json.loads((workspace / "cleanup.json").read_text())
        self.assertTrue(cleanup["cancel_sent"])

    def test_error_event_fails_turn_fast_and_keeps_its_message(self):
        error_event = {
            "type": "error",
            "error": {"type": "server_error", "message": "model crashed"},
        }
        runner, sessions, _ = self._make_runner(
            [],
            event_values=CONNECTED_ONLY + [error_event],
            turn_timeout=30,
            stays_open=True,
        )

        started = time.monotonic()
        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.failure_code, "turn_not_completed")
        self.assertEqual(result.failure_message, "error: model crashed")
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])

    def test_environment_failure_keeps_its_message(self):
        failed_event = {
            "type": "agent.session.environment.failed",
            "environment": {
                "status": "failed",
                "error": {"type": "executor_error", "message": "executor rejected"},
            },
        }
        runner, sessions, _ = self._make_runner(
            [],
            event_values=[failed_event],
            connection_timeout=30,
            stays_open=True,
        )

        started = time.monotonic()
        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn(
            "failed to connect (agent.session.environment.failed: executor rejected)",
            result.failure_message,
        )
        self.assertEqual(sessions.operations, ["delete", "stop"])

    def test_stream_ending_without_terminal_event_fails_fast(self):
        runner, sessions, _ = self._make_runner(
            [],
            event_values=CONNECTED_ONLY,
            turn_timeout=30,
        )

        started = time.monotonic()
        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("ended before the turn finished", result.failure_message)
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])
        workspace = runner.last_run_directory / "workspace"
        saved_events = json.loads((workspace / "session-events.json").read_text())
        self.assertEqual(saved_events[-1]["error_type"], "StreamEnded")

    @patch("openai_managed.runner.time.sleep")
    def test_delete_conflicts_are_retried_before_docker_stops(self, sleep):
        progress_messages = []
        runner, sessions, sandboxes = self._make_runner(
            progress_messages,
            delete_outcomes=[FakeConflictError("not settled"), FakeConflictError("not settled")],
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.status, "completed")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(sessions.operations, ["delete", "delete", "delete", "stop"])
        self.assertIn(
            "[cleanup] Session deletion succeeded after 3 attempts", progress_messages
        )
        cleanup = json.loads(
            (runner.last_run_directory / "workspace" / "cleanup.json").read_text()
        )
        self.assertEqual(cleanup["delete_attempts"], 3)
        self.assertTrue(cleanup["session_deleted"])

    @patch("openai_managed.runner.time.sleep")
    def test_delete_conflict_retries_are_bounded_and_keep_primary_failure(self, sleep):
        events = CONNECTED_ONLY + [{"type": "agent.session.turn.failed"}]
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=events,
            delete_outcomes=[FakeConflictError("not settled")] * 5,
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.failure_code, "turn_not_completed")
        self.assertTrue(result.failure_message.startswith("agent.session.turn.failed; "))
        self.assertIn("Cleanup errors: session deletion: not settled", result.failure_message)
        self.assertEqual(sessions.delete_attempts, 5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8])
        self.assertTrue(sandboxes[0].stopped)
        cleanup = json.loads(
            (runner.last_run_directory / "workspace" / "cleanup.json").read_text()
        )
        self.assertFalse(cleanup["session_deleted"])

    @patch("openai_managed.runner.time.sleep")
    def test_non_conflict_delete_error_is_not_retried(self, sleep):
        events = CONNECTED_ONLY + [{"type": "agent.session.turn.failed"}]
        runner, sessions, _ = self._make_runner(
            [],
            event_values=events,
            delete_outcomes=[RuntimeError("permission denied")],
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.failure_code, "turn_not_completed")
        self.assertIn("session deletion: permission denied", result.failure_message)
        self.assertEqual(sessions.delete_attempts, 1)
        sleep.assert_not_called()

    def test_cancel_failure_still_deletes_session_and_stops_docker(self):
        runner, sessions, sandboxes = self._make_runner(
            [],
            event_values=CONNECTED_ONLY,
            turn_timeout=0.01,
            stays_open=True,
            cancel_error=RuntimeError("cancel rejected"),
            delete_outcomes=[RuntimeError("delete rejected")],
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("exceeded its time limit", result.failure_message)
        self.assertIn("turn cancellation: cancel rejected", result.failure_message)
        self.assertIn("session deletion: delete rejected", result.failure_message)
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])
        self.assertTrue(sandboxes[0].stopped)

    def test_failed_task_send_still_cancels_turn(self):
        runner, sessions, _ = self._make_runner(
            [],
            event_values=CONNECTED_ONLY,
            stays_open=True,
            send_error=RuntimeError("response lost"),
        )

        result = runner.run(SashaTask("303839", "task-1", TASK_URL))

        self.assertEqual(result.failure_code, "managed_runner_error")
        self.assertIn("response lost", result.failure_message)
        self.assertEqual(sessions.operations, ["cancel", "delete", "stop"])

    def test_completed_turn_stays_completed_when_cleanup_fails(self):
        progress_messages = []
        runner, _, _ = self._make_runner(
            progress_messages,
            delete_outcomes=[RuntimeError("delete rejected")],
        )

        result = runner.run(
            SashaTask("303839", "task-1", TASK_URL, "Show me green polos.")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.message_html, MESSAGE)
        saved = json.loads(runner.last_conversation_file.read_text())
        self.assertEqual(len(saved["conversation_history"]), 2)
        self.assertIn(
            "[cleanup] WARNING: session deletion: delete rejected", progress_messages
        )
        workspace = runner.last_run_directory / "workspace"
        cleanup = json.loads((workspace / "cleanup.json").read_text())
        self.assertEqual(cleanup["errors"], ["session deletion: delete rejected"])
        saved_result = json.loads((workspace / "result.json").read_text())
        self.assertEqual(saved_result["status"], "completed")

    def _make_runner(
        self,
        progress_messages,
        cost_reporter=None,
        *,
        event_values=None,
        retrieve_error=None,
        authentication_error=None,
        browser_error=None,
        connection_timeout=1,
        turn_timeout=1,
        stays_open=False,
        stream_error=None,
        send_error=None,
        cancel_error=None,
        delete_outcomes=(),
    ):
        operations = []
        sessions = FakeSessionsAPI("303839", event_values, retrieve_error, operations)
        sessions.events.stays_open = stays_open
        sessions.events.stream_error = stream_error
        sessions.events.send_error = send_error
        sessions.events.cancel_error = cancel_error
        sessions.delete_outcomes = list(delete_outcomes)
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
            sandbox = FakeSandbox(
                config, executor_api_key, len(sandboxes) + 1, operations
            )
            sandbox.authentication_error = authentication_error
            sandbox.browser_error = browser_error
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
