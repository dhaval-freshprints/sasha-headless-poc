"""Live QA smoke tests for the kept-open Sasha browser.

These tests sign in to Fresh Prints QA inside the managed Docker image, start the
kept-open browser, and attach to it from separate commands the way Sasha does.
They are skipped unless SASHA_LIVE_QA=1 and the FP_* login variables are set
(for example in .env).

Run with:
    SASHA_LIVE_QA=1 .venv/bin/python -m unittest tests.test_openai_managed_browser
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from dotenv import load_dotenv

from openai_managed.sandbox import DockerSandbox, SandboxConfig
from openai_managed.task import SashaTask


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LIVE_QA_ENABLED = os.environ.get("SASHA_LIVE_QA") == "1"
DEAL_ID = os.environ.get("SASHA_LIVE_QA_DEAL_ID", "303931")
COMMAND_TIMEOUT_SECONDS = 90

# A tiny stand-in for a Sasha command: attach, do one thing, print JSON, disconnect.
ATTACH_SCRIPT = r"""
const { chromium } = require("playwright");
(async () => {
  const [action, value] = process.argv.slice(2);
  const browser = await chromium.connectOverCDP("http://127.0.0.1:9222");
  const context = browser.contexts()[0];
  const page = context.pages()[0] || (await context.newPage());
  const styleBox = page.getByTestId("quoter-product-select").getByRole("textbox");
  if (action === "open") {
    await page.goto(value, { waitUntil: "domcontentloaded" });
  }
  if (action === "open-quoter-and-type") {
    await page.goto(value, { waitUntil: "domcontentloaded" });
    await styleBox.pressSequentially("G500", { delay: 60 });
  }
  const result = { url: page.url() };
  if (action === "read-style") {
    result.style = await styleBox.inputValue();
  }
  if (action === "open") {
    result.signedIn = await page
      .getByText("Deal Manager", { exact: true })
      .first()
      .waitFor({ state: "visible", timeout: 30000 })
      .then(() => true, () => false);
  }
  await browser.close();
  process.stdout.write(JSON.stringify(result));
})().catch((error) => {
  process.stdout.write(JSON.stringify({ error: error.message.split("\n")[0] }));
  process.exit(1);
});
"""


@unittest.skipUnless(LIVE_QA_ENABLED, "set SASHA_LIVE_QA=1 to run live QA smoke tests")
class KeptOpenBrowserLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs_directory = tempfile.TemporaryDirectory()
        cls.sandbox = DockerSandbox(
            SandboxConfig(
                image=os.environ.get(
                    "OPENAI_MANAGED_SANDBOX_IMAGE", "sasha-openai-managed:local"
                ),
                runs_directory=Path(cls.runs_directory.name),
            ),
            executor_api_key="not-used-by-smoke-tests",
        )
        base_url = os.environ["FP_BASE_URL"].rstrip("/")
        cls.deal_url = f"{base_url}/dashboard/sales-pipeline/deal?id={DEAL_ID}"
        cls.quoter_url = f"{base_url}/dashboard/quoter"
        cls.handle = cls.sandbox.prepare(
            SashaTask(DEAL_ID, f"browser-smoke-{uuid.uuid4().hex[:8]}", cls.deal_url, workflow="outreach")
        )
        (cls.handle.workspace_directory / "attach.js").write_text(
            ATTACH_SCRIPT, encoding="utf-8"
        )
        try:
            cls.sandbox.start_container()
            cls.sandbox.authenticate(
                cls.deal_url,
                os.environ["FP_LOGIN_URL"],
                os.environ["FP_USER"],
                os.environ["FP_PASSWORD"],
            )
            cls.sandbox.start_browser()
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.stop()
        cls.runs_directory.cleanup()

    def run_in_container(self, *command):
        return subprocess.run(
            ["docker", "exec", self.handle.container_name, *command],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )

    def attach(self, *arguments):
        process = self.run_in_container("node", "/workspace/attach.js", *arguments)
        return process.returncode, json.loads(process.stdout)

    def start_browser(self):
        process = self.run_in_container("node", "/opt/sasha/start_browser.js")
        return process.returncode, json.loads(process.stdout)

    def test_attached_command_opens_deal_while_signed_in(self):
        exit_code, result = self.attach("open", self.deal_url)

        self.assertEqual(exit_code, 0, result)
        self.assertEqual(result["url"], self.deal_url)
        self.assertTrue(result["signedIn"])

    def test_page_state_carries_over_between_commands(self):
        exit_code, first = self.attach("open-quoter-and-type", self.quoter_url)
        self.assertEqual(exit_code, 0, first)

        exit_code, second = self.attach("read-style")

        self.assertEqual(exit_code, 0, second)
        self.assertEqual(second["url"], self.quoter_url)
        self.assertEqual(second["style"], "G500")

    def test_start_script_is_safe_to_run_again(self):
        self.attach("url")

        exit_code, result = self.start_browser()

        self.assertEqual(exit_code, 0)
        self.assertEqual(result, {"status": "running"})

    def test_restart_after_crash_is_still_signed_in(self):
        self.run_in_container("pkill", "-f", "remote-debugging-port=9222")
        exit_code, result = self.attach("url")
        self.assertEqual(exit_code, 1, "attach should fail while Chrome is down")

        exit_code, result = self.start_browser()
        self.assertEqual(exit_code, 0, result)
        self.assertEqual(result, {"status": "started"})

        exit_code, result = self.attach("open", self.deal_url)
        self.assertEqual(exit_code, 0, result)
        self.assertTrue(result["signedIn"])


if __name__ == "__main__":
    unittest.main()
