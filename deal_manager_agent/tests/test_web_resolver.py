"""Resolver contracts, isolation, lifecycle and refreshable web jobs."""

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from deal_manager_agent.models import LookupRequest, LookupResult
from deal_manager_agent.runner import DealManagerAgent
from deal_manager_agent.sandbox import DealManagerSandbox
from deal_manager_agent.settings import DealManagerSettings
from webapp.deal_manager_jobs import DealManagerJobs
from webapp.deals import DealStore
from webapp.jobs import JobManager
from webapp.tests.helpers import PassThroughResolver


# Importing the legacy app creates its default managers. Keep those out of live lookup storage.
with tempfile.TemporaryDirectory(prefix="import_") as import_directory:
    with patch.dict(os.environ, {"DEAL_MANAGER_DIRECTORY": import_directory}):
        from webapp.app import create_app


def decision():
    return {
        "status": "matched", "deal_id": "123", "deal_title": "Example",
        "confidence": "high", "reason": "Verified proof relationship",
        "candidate_deals": [{"deal_id": "123", "supports": ["Proof"], "conflicts": []}],
        "evidence": [{"query_id": "Q1", "table": "test-main.deals", "primary_key": "123",
                      "fields": [{"name": "deleted_at", "value": "null"}],
                      "interpretation": "Verified deal"}],
        "limitations": [], "next_check": None,
    }


def request():
    return LookupRequest(sender_email="client@example.com", subject="Proof", body="Where is my proof?")


def wait_for_job(manager, lookup_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get(lookup_id)
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("Lookup did not finish")


class ReadyEvents:
    def __init__(self, *args):
        self.connection_settled = threading.Event()
        self.connected = threading.Event()
        self.turn_finished = threading.Event()
        for event in (self.connection_settled, self.connected, self.turn_finished):
            event.set()
        self.error = None
        self.terminal_type = "agent.session.turn.completed"

    def start(self):
        pass

    def join(self):
        pass

    def has_terminal_turn(self):
        return True


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = DealManagerSettings(
            model="configured-model", reasoning_effort="medium", executor_api_key="executor-secret",
            image="test-image", directory=self.root / "executions",
            database={"NON_PROD_PASS": "database-secret",
                      "DEAL_MANAGER_MAIN_SCHEMA": "test-main",
                      "DEAL_MANAGER_OPERATIONS_SCHEMA": "test_operations",
                      "DEAL_MANAGER_CRM_SCHEMA": "test_crm"}, certificate=self.root / "ca.pem",
            connection_timeout=0.01, turn_timeout=0.01,
        )

    def test_decision_contract_rejects_unsupported_matches(self):
        for changes in (
            {"confidence": "low"}, {"evidence": []}, {"deal_id": "999"},
            {"status": "needs_review"}, {"deal_id": 123},
            {"candidate_deals": [{"deal_id": "123", "supports": [], "conflicts": ["Other proof"]}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                LookupResult.model_validate({**decision(), **changes}, context={"main_schema": "test-main"})

    def test_match_requires_evidence_from_the_configured_main_schema(self):
        result = decision()
        LookupResult.model_validate(result, context={"main_schema": "test-main"})
        with self.assertRaises(ValidationError):
            LookupResult.model_validate(result, context={"main_schema": "another_main"})
        with self.assertRaises(ValidationError):
            LookupResult.model_validate(result)
        result["evidence"][0]["table"] = "another_main.deals"
        LookupResult.model_validate(result, context={"main_schema": "another_main"})

    def test_missing_metadata_is_accepted_without_inventing_timestamp(self):
        sandbox = DealManagerSandbox(self.settings)
        handle = sandbox.prepare(request())
        message = json.loads((handle.workspace_directory / "message.json").read_text())
        self.assertIsNone(message["received_at"])
        skills = {path.name for path in (handle.workspace_directory / "capabilities").iterdir()}
        self.assertEqual(skills, {"deal-manager-database", "deal-manager-resolve-deal"})
        self.assertFalse((handle.workspace_directory / "task.json").exists())
        self.assertNotIn("database-secret", (handle.workspace_directory / "message.json").read_text())
        self.assertEqual(json.loads((handle.workspace_directory / "database_schemas.json").read_text()), {
            "main": "test-main", "operations": "test_operations", "crm": "test_crm",
        })
        self.assertFalse(any(path.name.endswith("verified_findings.json")
                             for path in handle.workspace_directory.rglob("*")))
        self.assertFalse((handle.workspace_directory / "test_results").exists())

    def test_database_secret_is_passed_over_stdin_not_docker_arguments(self):
        sandbox = DealManagerSandbox(self.settings)
        handle = sandbox.prepare(request())
        with patch('deal_manager_agent.sandbox.subprocess.run') as process:
            sandbox.start_container()
        self.assertEqual(process.call_count, 2)
        for call in process.call_args_list:
            self.assertNotIn('database-secret', ' '.join(call.args[0]))
        self.assertIn('database-secret', process.call_args.kwargs['input'])
        self.assertFalse(any(path.name == 'database.json' for path in handle.workspace_directory.rglob('*')))

    def make_runner(self, result=None):
        sandbox = DealManagerSandbox(self.settings)
        sandbox.start_container = Mock()
        sandbox.connect_executor = Mock()
        sandbox.stop = Mock()
        client = Mock()
        sessions = client.beta.agents.sessions
        sessions.create.return_value = SimpleNamespace(
            id="session1", environment=SimpleNamespace(id="env1", remote_url="https://example.com"),
        )
        sessions.items.list.return_value = [{"role": "assistant", "phase": "final_answer",
            "content": [{"type": "output_text", "text": json.dumps(result or decision())}]}]
        sessions.retrieve.return_value = SimpleNamespace(usage={"input_tokens": 10})
        runner = DealManagerAgent(client, self.settings, sandbox_factory=lambda _: sandbox, events_factory=ReadyEvents)
        return runner, sandbox, sessions

    def test_separate_agent_uses_configured_model_and_cleans_up(self):
        runner, sandbox, sessions = self.make_runner()
        result = runner.run(request(), "lookup1")
        self.assertEqual(result.deal_id, "123")
        agent = sessions.create.call_args.kwargs["agent"]
        self.assertEqual(agent["model"], "configured-model")
        self.assertNotIn("sasha-sales", agent["instructions"])
        self.assertEqual(sessions.create.call_args.kwargs["metadata"]["agent"], "deal-manager-agent")
        sessions.delete.assert_called_once_with("session1")
        sandbox.stop.assert_called_once()

    def test_invalid_result_fails_and_still_cleans_up(self):
        runner, sandbox, sessions = self.make_runner({**decision(), "confidence": "low"})
        with self.assertRaises(RuntimeError):
            runner.run(request(), "lookup1")
        sandbox.stop.assert_called_once()
        sessions.delete.assert_called_once()

    def test_timeout_cancels_session_and_stops_container(self):
        runner, sandbox, sessions = self.make_runner()
        events = ReadyEvents()
        events.turn_finished.clear()
        events.has_terminal_turn = lambda: False
        runner.events_factory = lambda *args: events
        with self.assertRaisesRegex(RuntimeError, "time limit"):
            runner.run(request(), "lookup1")
        self.assertEqual(sessions.events.create.call_args.kwargs["events"][0]["type"], "agent.session.input.cancel")
        sandbox.stop.assert_called_once()

    def test_errors_redact_credentials(self):
        runner, sandbox, sessions = self.make_runner()
        sandbox.start_container.side_effect = RuntimeError("Failed database-secret executor-secret")
        with self.assertRaisesRegex(RuntimeError, r"Failed \[redacted\] \[redacted\]"):
            runner.run(request(), "lookup1")

    def test_incomplete_environment_is_deleted(self):
        runner, sandbox, sessions = self.make_runner()
        sessions.create.return_value = SimpleNamespace(id="session1", environment=None)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            runner.run(request(), "lookup1")
        sessions.delete.assert_called_once_with("session1")


class WebLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = Mock()
        self.runner.run.return_value = LookupResult.model_validate(decision(), context={"main_schema": "test-main"})
        self.runner.usage = None
        self.lookups = DealManagerJobs(self.root / "lookups", lambda write: self.runner)
        self.sales = JobManager(self.root / "sales")
        self.deals = DealStore(self.root / "deals")
        self.client = TestClient(create_app(self.sales, self.deals, self.lookups, deal_resolver=PassThroughResolver()))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_full_api_round_trip_persists_without_sales_side_effects(self):
        page = self.client.get("/deal-manager")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Deal Manager Agent", page.text)
        self.assertEqual(self.client.get("/static/deal_manager.js").status_code, 200)
        response = self.client.post("/api/deal-manager/lookups", json=request().model_dump())
        self.assertEqual(response.status_code, 202)
        lookup_id = response.json()["lookup_id"]
        finished = wait_for_job(self.lookups, lookup_id)
        self.assertEqual(finished["result"]["deal_id"], "123")
        self.assertEqual(self.client.get(f"/api/deal-manager/lookups/{lookup_id}").json(), finished)
        self.assertEqual(self.client.get(f"/deal-manager/lookups/{lookup_id}").status_code, 200)
        self.assertEqual(self.sales.list_all(), [])
        self.assertEqual(self.deals.list_all(), [])
        reloaded = DealManagerJobs(self.root / "lookups")
        self.addCleanup(reloaded.shutdown)
        self.assertEqual(reloaded.get(lookup_id)["result"], finished["result"])

    def test_rejects_invalid_inputs_and_unknown_lookup(self):
        for changes in ({"sender_email": "bad"}, {"body": " "}, {"deal_id": "123"}):
            response = self.client.post("/api/deal-manager/lookups", json={**request().model_dump(), **changes})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/deal-manager/lookups/unknown").status_code, 404)

    def test_review_is_a_completed_lookup_with_no_selected_deal(self):
        self.runner.run.return_value = LookupResult.model_validate({**decision(),
            "status": "needs_review", "deal_id": None, "deal_title": None,
            "confidence": "low", "next_check": "Supply the subject"})
        job = self.lookups.submit(request())
        finished = wait_for_job(self.lookups, job["lookup_id"])
        self.assertEqual(finished["status"], "completed")
        self.assertIsNone(finished["result"]["deal_id"])

    def test_restart_marks_interrupted_lookup_failed(self):
        lookup_id = "a" * 32
        job = {"lookup_id": lookup_id, "input": request().model_dump(), "status": "running"}
        (self.root / "lookups" / f"{lookup_id}.json").write_text(json.dumps(job))
        reloaded = DealManagerJobs(self.root / "lookups")
        self.addCleanup(reloaded.shutdown)
        self.assertEqual(reloaded.get(lookup_id)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
