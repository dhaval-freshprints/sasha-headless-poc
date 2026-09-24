import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

from openai_managed.sandbox import DockerSandbox, SandboxConfig
from openai_managed.task import SashaTask


class DockerSandboxTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.config = SandboxConfig(
            image="test-image",
            runs_directory=self.root / "runs",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_prepares_outreach_without_a_saved_browser_profile(self):
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "outreach-303839",
            "https://qa.example/deal?id=303839",
        )

        handle = sandbox.prepare(task)
        sandbox.save_task_message("outreach task")

        task_data = json.loads(
            (handle.workspace_directory / "task.json").read_text()
        )
        self.assertIsNone(task_data["client_message"])
        self.assertTrue(handle.container_name.startswith("sasha-managed-"))
        self.assertFalse((handle.workspace_directory / "browser-profile").exists())
        self.assertTrue((handle.workspace_directory / "executor.log").is_file())
        self.assertEqual(
            (handle.workspace_directory / "TASK.md").read_text(), "outreach task"
        )

    def test_prepares_client_response_with_supplied_message(self):
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "What's the price for 40?",
        )

        handle = sandbox.prepare(task)

        task_data = json.loads(
            (handle.workspace_directory / "task.json").read_text()
        )
        self.assertEqual(task_data["client_message"], "What's the price for 40?")

    def test_copies_sasha_skill_into_run_workspace(self):
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "What's the price for 40?",
        )

        handle = sandbox.prepare(task)

        skill_directory = (
            handle.workspace_directory / "capabilities" / "sasha-sales"
        )
        self.assertTrue((skill_directory / "SKILL.md").is_file())
        self.assertTrue((skill_directory / "references" / "playbook.md").is_file())
        self.assertTrue((skill_directory / "references" / "workplace.md").is_file())

    @patch("openai_managed.sandbox.subprocess.Popen")
    @patch("openai_managed.sandbox.subprocess.run")
    def test_authenticates_in_the_same_container_without_exposing_credentials(
        self, run, popen
    ):
        run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="container-id\n", stderr=""),
            CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "status": "authenticated",
                        "deal_url": "https://qa.example/deal?id=303839",
                    }
                ),
                stderr="",
            ),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        popen.return_value = MagicMock()
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "Show me orange polos.",
        )
        sandbox.prepare(task)

        sandbox.start_container()
        sandbox.authenticate(
            task.deal_url,
            "https://qa.example/login",
            "qa-user",
            "qa-password",
        )
        sandbox.connect_executor("environment-1", "wss://executor.example/connect")

        container_command = run.call_args_list[0].args[0]
        authentication_call = run.call_args_list[1]
        authentication_command = authentication_call.args[0]
        executor_command = popen.call_args.args[0]
        credentials = json.loads(authentication_call.kwargs["input"])

        self.assertIn("/browser-profile:rw,nosuid,nodev,noexec,size=512m", container_command)
        self.assertEqual(authentication_command[:3], ["docker", "exec", "--interactive"])
        self.assertEqual(credentials["login_user"], "qa-user")
        self.assertEqual(credentials["login_password"], "qa-password")
        for command in (container_command, authentication_command, executor_command):
            self.assertNotIn("qa-user", command)
            self.assertNotIn("qa-password", command)
            self.assertNotIn("FP_USER", command)
            self.assertNotIn("FP_PASSWORD", command)
        self.assertIn("CODEX_API_KEY", executor_command)
        sandbox.stop()

    @patch("openai_managed.sandbox.subprocess.run")
    def test_container_mounts_browser_scripts_read_only(self, run):
        run.return_value = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        sandbox = DockerSandbox(self.config, "executor-key")
        sandbox.prepare(SashaTask("303839", "task-1", "https://qa.example/deal?id=303839"))

        sandbox.start_container()

        container_command = " ".join(run.call_args.args[0])
        self.assertIn("dst=/opt/sasha/browser_keeper.js,readonly", container_command)
        self.assertIn("dst=/opt/sasha/start_browser.js,readonly", container_command)

    @patch("openai_managed.sandbox.subprocess.run")
    def test_start_browser_runs_start_script_in_the_container(self, run):
        for status in ("started", "running"):
            with self.subTest(status=status):
                run.return_value = CompletedProcess(
                    args=[], returncode=0, stdout=json.dumps({"status": status}), stderr=""
                )
                sandbox = DockerSandbox(self.config, "executor-key")
                handle = sandbox.prepare(
                    SashaTask("303839", f"task-{status}", "https://qa.example/deal?id=303839")
                )

                sandbox.start_browser()

                self.assertEqual(
                    run.call_args.args[0],
                    [
                        "docker",
                        "exec",
                        handle.container_name,
                        "node",
                        "/opt/sasha/start_browser.js",
                    ],
                )

    @patch("openai_managed.sandbox.subprocess.run")
    def test_start_browser_raises_when_browser_does_not_start(self, run):
        run.return_value = CompletedProcess(
            args=[],
            returncode=1,
            stdout=json.dumps({"status": "failed", "reason": "browser keeper exited with code 1"}),
            stderr="",
        )
        sandbox = DockerSandbox(self.config, "executor-key")
        sandbox.prepare(SashaTask("303839", "task-1", "https://qa.example/deal?id=303839"))

        with self.assertRaisesRegex(RuntimeError, "browser keeper exited with code 1"):
            sandbox.start_browser()


if __name__ == "__main__":
    unittest.main()
