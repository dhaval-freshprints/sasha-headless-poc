"""Artwork suitability routes in task text, guidance, and upload enforcement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from account_manager_agent.attachments import (
    Attachment,
    AttachmentSet,
    artwork_quality_guidance,
)
from account_manager_agent.artwork.inspection import inspect_artwork
from account_manager_agent.artwork.settings import ArtworkSettings
from account_manager_agent.runner import AccountManagerAgent
from account_manager_agent.task import SashaTask
from account_manager_agent import browser_exec
from account_manager_agent.sandbox import DockerSandbox, SandboxConfig
from subprocess import CompletedProcess
from tests.test_account_manager_agent_environment import application_environment


class ArtworkDecisionGuidanceTests(unittest.TestCase):
    def test_quality_guidance_lists_all_routes_and_ppi(self):
        text = artwork_quality_guidance(
            ArtworkSettings(api_key="k", min_print_ppi=150, target_print_ppi=300)
        )
        for route in ("direct", "extract", "preserve_photo", "request_source"):
            self.assertIn(route, text)
        self.assertIn("150", text)
        self.assertIn("300", text)
        self.assertIn("artwork_decisions.json", text)
        self.assertIn("Never create print artwork by cropping", text)
        self.assertIn("/workspace/client-files", text)

    def test_task_message_includes_inspection_and_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file_1.png"
            Image.new("RGBA", (413, 457), (255, 0, 0, 200)).save(path, "PNG")
            files = AttachmentSet(
                [
                    Attachment(
                        "file_1",
                        path,
                        "shirt.png",
                        path.stat().st_size,
                        inspection=inspect_artwork(path),
                    )
                ]
            )
            task = SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
            with patch.dict("os.environ", {"ARTWORK_MIN_PRINT_PPI": "150", "ARTWORK_TARGET_PRINT_PPI": "300"}):
                message = AccountManagerAgent._build_task_message(
                    task, application_environment(), files
                )
            self.assertIn("413x457", message)
            self.assertIn("transparent", message)
            self.assertIn("`direct`", message)
            self.assertIn("`extract`", message)
            self.assertIn("`preserve_photo`", message)
            self.assertIn("`request_source`", message)
            self.assertIn("artwork_decisions.json", message)
            self.assertIn("Pillow", message)

    def test_agent_instructions_and_guide_prohibit_local_upscale(self):
        root = Path(__file__).resolve().parents[1] / "account_manager_agent"
        instructions = (root / "agent_instructions.md").read_text(encoding="utf-8")
        guide = (
            root
            / "capabilities/account-manager/references/artwork_from_photos.md"
        ).read_text(encoding="utf-8")
        for text in (instructions, guide):
            self.assertIn("artwork_decisions.json", text)
            self.assertIn("preserve_photo", text)
            self.assertIn("request_source", text)
            self.assertIn("upscaling", text.lower())
            self.assertIn("/workspace/client-files", text)


class UploadEnforcementTests(unittest.TestCase):
    def test_set_input_files_allows_client_files_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "upload.js"
            script.write_text(
                "await page.locator('#upload-file-input')"
                ".setInputFiles('/workspace/client-files/file_1.png');\n",
                encoding="utf-8",
            )
            browser_exec.validate_upload_paths(script)

    def test_set_input_files_rejects_artifacts_path(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "upload.js"
            script.write_text(
                "await locator.setInputFiles('/workspace/artifacts/chest_art.png');\n",
                encoding="utf-8",
            )
            with self.assertRaises(browser_exec.BrowserExecError) as error:
                browser_exec.validate_upload_paths(script)
            self.assertEqual(error.exception.code, "invalid_upload_path")

    def test_set_input_files_rejects_buffer_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "upload.js"
            script.write_text(
                "await locator.setInputFiles({ name: 'x.png', mimeType: 'image/png', "
                "buffer: Buffer.from(data) });\n",
                encoding="utf-8",
            )
            with self.assertRaises(browser_exec.BrowserExecError) as error:
                browser_exec.validate_upload_paths(script)
            self.assertEqual(error.exception.code, "invalid_upload_path")

    def test_set_input_files_rejects_dynamic_variable(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "upload.js"
            script.write_text(
                "const path = builtPath;\nawait locator.setInputFiles(path);\n",
                encoding="utf-8",
            )
            with self.assertRaises(browser_exec.BrowserExecError) as error:
                browser_exec.validate_upload_paths(script)
            self.assertEqual(error.exception.code, "invalid_upload_path")

    def test_execute_blocks_before_running_disallowed_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "step.js"
            script.write_text(
                "await locator.setInputFiles('/workspace/tmp/upscaled.png');\n",
                encoding="utf-8",
            )
            artifacts = root / "artifacts"
            artifacts.mkdir()
            with patch.object(browser_exec, "WORKSPACE", root), \
                    patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", artifacts / "browser.lock"), \
                    patch.object(browser_exec, "DIAGNOSTICS_PATH", artifacts / "browser_diagnostics.json"), \
                    patch.object(browser_exec, "run_script") as run_script:
                with self.assertRaises(browser_exec.BrowserExecError) as error:
                    browser_exec.execute(script, timeout_seconds=5)
            self.assertEqual(error.exception.code, "invalid_upload_path")
            run_script.assert_not_called()

    @patch("account_manager_agent.sandbox.subprocess.run")
    def test_sandbox_mounts_client_files_read_only(self, run):
        run.return_value = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            config = SandboxConfig(
                image="sasha-test",
                runs_directory=Path(directory),
                hq_base_url="https://hq.example",
            )
            sandbox = DockerSandbox(config, "executor-key")
            handle = sandbox.prepare(
                SashaTask("303839", "task-1", "https://qa.example/deal?id=303839")
            )
            self.assertTrue((handle.workspace_directory / "client-files").is_dir())
            sandbox.start_container()
            command = " ".join(run.call_args.args[0])
            self.assertIn(
                f"dst=/workspace/client-files,readonly",
                command,
            )
            self.assertIn(str(handle.workspace_directory / "client-files"), command)


if __name__ == "__main__":
    unittest.main()
