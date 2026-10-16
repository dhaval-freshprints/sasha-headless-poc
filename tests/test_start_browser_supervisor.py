"""Unit coverage for stale-browser termination and duplicate-keeper prevention."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread


ROOT = Path(__file__).resolve().parent.parent
START_BROWSER = ROOT / "account_manager_agent" / "start_browser.js"
KEEPER = ROOT / "account_manager_agent" / "browser_keeper.js"


class _CdpHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/json/version":
            body = b'{"Browser":"test","webSocketDebuggerUrl":"ws://127.0.0.1/devtools"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A003
        return


class StartBrowserSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.artifacts = Path(self.temporary.name) / "artifacts"
        self.artifacts.mkdir()
        self.env = {
            **os.environ,
            "SASHA_BROWSER_ARTIFACTS": str(self.artifacts),
            "SASHA_BROWSER_KEEPER_SCRIPT": str(KEEPER),
            "SASHA_BROWSER_PORT_CLEAR_MS": "500",
            "SASHA_BROWSER_START_TIMEOUT_MS": "1000",
        }

    def run_node(self, source: str, extra_env: dict | None = None) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            ["node", "-e", source],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_terminate_stale_clears_dead_pid_and_state(self):
        pid_path = self.artifacts / "browser_keeper.pid"
        state_path = self.artifacts / "browser_keeper.state.json"
        pid_path.write_text("999999\n", encoding="utf-8")
        state_path.write_text(json.dumps({"status": "running"}) + "\n", encoding="utf-8")
        self.env["SASHA_BROWSER_CDP_URL"] = "http://127.0.0.1:1/json/version"

        process = self.run_node(
            "const m = require('./account_manager_agent/start_browser.js');\n"
            "m.terminateStaleBrowser().then((problem) => {\n"
            "  process.stdout.write(JSON.stringify({ problem, pidExists: "
            "require('fs').existsSync(m.PID_PATH) }));\n"
            "});\n"
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        payload = json.loads(process.stdout)
        self.assertEqual(payload["problem"], "")
        self.assertFalse(payload["pidExists"])
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "stopped")

    def test_terminate_stale_stops_live_placeholder_process(self):
        placeholder = subprocess.Popen(
            ["sleep", "30"],
            start_new_session=True,
        )
        self.addCleanup(lambda: placeholder.poll() is None and placeholder.kill())
        (self.artifacts / "browser_keeper.pid").write_text(f"{placeholder.pid}\n", encoding="utf-8")
        self.env["SASHA_BROWSER_CDP_URL"] = "http://127.0.0.1:1/json/version"

        process = self.run_node(
            "const m = require('./account_manager_agent/start_browser.js');\n"
            "m.terminateStaleBrowser().then((problem) => {\n"
            "  process.stdout.write(JSON.stringify({ problem }));\n"
            "});\n"
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["problem"], "")
        deadline = time.time() + 2
        while time.time() < deadline and placeholder.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(placeholder.poll())

    def test_duplicate_keeper_exits_when_pid_file_owner_is_alive(self):
        holder = subprocess.Popen(["sleep", "30"], start_new_session=True)

        def stop_holder():
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=5)

        self.addCleanup(stop_holder)
        (self.artifacts / "browser_keeper.pid").write_text(f"{holder.pid}\n", encoding="utf-8")

        process = subprocess.run(
            ["node", str(KEEPER)],
            cwd=str(ROOT),
            env={
                **self.env,
                "SASHA_BROWSER_PROFILE": str(Path(self.temporary.name) / "profile"),
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("Another browser keeper already holds the PID file", process.stderr)

    def test_main_reports_running_without_starting_second_keeper_when_cdp_answers(self):
        server = HTTPServer(("127.0.0.1", 0), _CdpHandler)
        port = server.server_address[1]
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop_server():
            server.shutdown()
            server.server_close()

        self.addCleanup(stop_server)

        process = subprocess.run(
            ["node", str(START_BROWSER)],
            cwd=str(ROOT),
            env={
                **self.env,
                "SASHA_BROWSER_CDP_URL": f"http://127.0.0.1:{port}/json/version",
            },
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), {"status": "running"})


if __name__ == "__main__":
    unittest.main()
