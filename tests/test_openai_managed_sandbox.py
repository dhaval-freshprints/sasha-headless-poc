import json
import tempfile
import unittest
from pathlib import Path

from openai_managed.sandbox import DockerSandbox, SandboxConfig
from openai_managed.task import SashaTask


class DockerSandboxPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.auth_directory = self.root / "auth"
        self.auth_directory.mkdir()
        (self.auth_directory / "profile-marker").write_text("authenticated")
        self.config = SandboxConfig(
            image="test-image",
            auth_directory=self.auth_directory,
            runs_directory=self.root / "runs",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_prepares_outreach_with_null_client_message(self):
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "outreach-303839",
            "https://qa.example/deal?id=303839",
        )

        handle = sandbox.prepare(task, "outreach task")

        task_data = json.loads(
            (handle.workspace_directory / "task.json").read_text()
        )
        self.assertIsNone(task_data["client_message"])
        self.assertTrue(handle.container_name.startswith("sasha-managed-"))
        self.assertTrue(
            (handle.browser_profile_directory / "profile-marker").is_file()
        )

    def test_prepares_client_response_with_supplied_message(self):
        sandbox = DockerSandbox(self.config, "executor-key")
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "What's the price for 40?",
        )

        handle = sandbox.prepare(task, "client-response task")

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

        handle = sandbox.prepare(task, "client-response task")

        skill_directory = (
            handle.workspace_directory / "capabilities" / "sasha-sales"
        )
        self.assertTrue((skill_directory / "SKILL.md").is_file())
        self.assertTrue((skill_directory / "references" / "playbook.md").is_file())
        self.assertTrue((skill_directory / "references" / "workplace.md").is_file())


if __name__ == "__main__":
    unittest.main()
