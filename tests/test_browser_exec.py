"""Serialized browser_exec wrapper behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from account_manager_agent import browser_exec


class BrowserExecTests(unittest.TestCase):
    def test_workspace_script_rejects_paths_outside_workspace(self):
        with self.assertRaises(browser_exec.BrowserExecError) as error:
            browser_exec.workspace_script("/tmp/evil.js")
        self.assertEqual(error.exception.code, "invalid_script")

    def test_execute_runs_script_when_cdp_is_healthy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "step.js"
            script.write_text("console.log('ok')\n", encoding="utf-8")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            with patch.object(browser_exec, "WORKSPACE", root), \
                    patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", artifacts / "browser.lock"), \
                    patch.object(browser_exec, "DIAGNOSTICS_PATH", artifacts / "browser_diagnostics.json"), \
                    patch.object(browser_exec, "cdp_health", return_value={"ok": True}), \
                    patch.object(
                        browser_exec,
                        "run_script",
                        return_value=type("P", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})(),
                    ):
                result = browser_exec.execute(script, timeout_seconds=5)
            self.assertTrue(result["ok"])
            self.assertFalse(result["restarted"])
            self.assertFalse((artifacts / "browser.lock").exists())

    def test_execute_restarts_once_when_cdp_is_down(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "step.js"
            script.write_text("console.log('ok')\n", encoding="utf-8")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            health = iter([{"ok": False}, {"ok": True}, {"ok": True}])
            with patch.object(browser_exec, "WORKSPACE", root), \
                    patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", artifacts / "browser.lock"), \
                    patch.object(browser_exec, "DIAGNOSTICS_PATH", artifacts / "browser_diagnostics.json"), \
                    patch.object(browser_exec, "cdp_health", side_effect=lambda: next(health)), \
                    patch.object(
                        browser_exec,
                        "restart_browser",
                        return_value={"status": "restarted", "exit_code": 0},
                    ) as restart, \
                    patch.object(
                        browser_exec,
                        "run_script",
                        return_value=type("P", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})(),
                    ):
                result = browser_exec.execute(script, timeout_seconds=5)
            self.assertTrue(result["ok"])
            self.assertTrue(result["restarted"])
            restart.assert_called_once()

    def _run_with_failures(self, stderr):
        """Run a script whose first attempt fails with stderr while the HTTP health check passes."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "step.js"
            script.write_text("console.log('ok')\n", encoding="utf-8")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            attempts = iter([
                type("P", (), {"returncode": 1, "stdout": "", "stderr": stderr})(),
                type("P", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})(),
            ])
            with patch.object(browser_exec, "WORKSPACE", root), \
                    patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", artifacts / "browser.lock"), \
                    patch.object(browser_exec, "DIAGNOSTICS_PATH", artifacts / "browser_diagnostics.json"), \
                    patch.object(browser_exec, "cdp_health", return_value={"ok": True}), \
                    patch.object(browser_exec, "process_snapshot", return_value=[]), \
                    patch.object(
                        browser_exec,
                        "restart_browser",
                        return_value={"status": "restarted", "exit_code": 0},
                    ) as restart, \
                    patch.object(browser_exec, "run_script", side_effect=lambda *_: next(attempts)) as run:
                result = browser_exec.execute(script, timeout_seconds=5)
            return result, restart, run

    def test_cdp_attach_timeout_restarts_once_and_reruns_even_when_health_passes(self):
        # A hung tab can block the DevTools attach while /json/version still answers.
        result, restart, run = self._run_with_failures(
            "browserType.connectOverCDP: Timeout 30000ms exceeded.\nCall log: ...")
        self.assertTrue(result["ok"])
        self.assertTrue(result["restarted"])
        restart.assert_called_once()
        self.assertEqual(run.call_count, 2)

    def test_page_timeout_does_not_restart_the_browser(self):
        result, restart, run = self._run_with_failures("locator.click: Timeout 30000ms exceeded.")
        self.assertFalse(result["ok"])
        self.assertFalse(result["restarted"])
        restart.assert_not_called()
        self.assertEqual(run.call_count, 1)

    def test_stale_lock_is_cleared_when_owner_is_dead(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(directory)
            lock = artifacts / "browser.lock"
            lock.write_text("999999\n", encoding="utf-8")
            with patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", lock), \
                    patch.object(browser_exec.os, "kill", side_effect=ProcessLookupError):
                fd = browser_exec.acquire_lock(timeout_seconds=1)
                browser_exec.release_lock(fd)
            self.assertFalse(lock.exists())

    def test_failed_command_writes_diagnostic_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "step.js"
            script.write_text("console.log('fail')\n", encoding="utf-8")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            diagnostics = artifacts / "browser_diagnostics.json"
            with patch.object(browser_exec, "WORKSPACE", root), \
                    patch.object(browser_exec, "ARTIFACTS", artifacts), \
                    patch.object(browser_exec, "LOCK_PATH", artifacts / "browser.lock"), \
                    patch.object(browser_exec, "DIAGNOSTICS_PATH", diagnostics), \
                    patch.object(browser_exec, "cdp_health", return_value={"ok": True}), \
                    patch.object(
                        browser_exec,
                        "run_script",
                        return_value=type(
                            "P",
                            (),
                            {"returncode": 1, "stdout": "", "stderr": "boom"},
                        )(),
                    ), \
                    patch.object(browser_exec, "process_snapshot", return_value=["chrome 1"]):
                result = browser_exec.execute(script, timeout_seconds=5)
            self.assertFalse(result["ok"])
            history = json.loads(diagnostics.read_text(encoding="utf-8"))
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["exit_code"], 1)
            self.assertEqual(history[0]["processes"], ["chrome 1"])
            self.assertIn("cdp", history[0])


if __name__ == "__main__":
    unittest.main()
