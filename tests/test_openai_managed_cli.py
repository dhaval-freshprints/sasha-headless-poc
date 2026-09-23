import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from openai_managed.task import SashaResult
from scripts import run_openai_managed_sasha as cli


class ManagedSashaCliTests(unittest.TestCase):
    def test_outreach_arguments_leave_client_message_empty(self):
        arguments = cli.parse_arguments(["303839", "--verbose"])

        self.assertEqual(arguments.deal_id, "303839")
        self.assertIsNone(arguments.message)
        self.assertTrue(arguments.verbose)
        self.assertFalse(arguments.pricing)

    def test_client_response_arguments_preserve_message(self):
        client_message = "What's the price for 40?"

        arguments = cli.parse_arguments(
            ["303839", "--message", client_message, "--pricing"]
        )

        self.assertEqual(arguments.deal_id, "303839")
        self.assertEqual(arguments.message, client_message)
        self.assertFalse(arguments.verbose)
        self.assertTrue(arguments.pricing)

    def test_client_response_prints_json_and_keeps_diagnostics_on_stderr(self):
        client_message = "What's the price for 40?"
        fake_runner = FakeRunner()
        arguments = argparse.Namespace(
            deal_id="303839",
            message=client_message,
            verbose=True,
            pricing=True,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch.object(cli, "parse_arguments", return_value=arguments),
            patch.object(cli, "load_dotenv"),
            patch.dict(os.environ, {"FP_BASE_URL": "https://qa.example"}),
            patch.object(
                cli.OpenAIManagedRunner,
                "from_environment",
                side_effect=fake_runner.connect,
            ),
            patch.object(cli.time, "perf_counter", side_effect=[100.0, 163.25]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "completed")
        self.assertEqual(fake_runner.task.client_message, client_message)
        self.assertIn("Sasha is working", stderr.getvalue())
        self.assertIn("[pricing] test estimate", stderr.getvalue())
        self.assertIn("[timing] Total run: 1 min 03 sec", stderr.getvalue())
        self.assertIn("Artifacts: /tmp/sasha-run", stderr.getvalue())

    def test_failed_run_also_prints_elapsed_time(self):
        fake_runner = FakeRunner(status="failed")
        arguments = argparse.Namespace(
            deal_id="303839",
            message=None,
            verbose=False,
            pricing=False,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch.object(cli, "parse_arguments", return_value=arguments),
            patch.object(cli, "load_dotenv"),
            patch.dict(os.environ, {"FP_BASE_URL": "https://qa.example"}),
            patch.object(
                cli.OpenAIManagedRunner,
                "from_environment",
                side_effect=fake_runner.connect,
            ),
            patch.object(cli.time, "perf_counter", side_effect=[10.0, 12.5]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = cli.main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")
        self.assertIn("[timing] Total run: 0 min 03 sec", stderr.getvalue())


class FakeRunner:
    def __init__(self, status="completed"):
        self.status = status
        self.task = None
        self.progress = None
        self.cost_reporter = None
        self.last_run_directory = Path("/tmp/sasha-run")

    def connect(self, progress, cost_reporter):
        self.progress = progress
        self.cost_reporter = cost_reporter
        return self

    def run(self, task):
        self.task = task
        self.progress.report("Sasha is working")
        if self.cost_reporter is not None:
            self.cost_reporter.write("[pricing] test estimate")
        return SashaResult(
            deal_id=task.deal_id,
            status=self.status,
            message_html="<p>Draft</p>" if self.status == "completed" else "",
            failure_code="test_failure" if self.status == "failed" else "",
            failure_message="Test failed" if self.status == "failed" else "",
        )


if __name__ == "__main__":
    unittest.main()
