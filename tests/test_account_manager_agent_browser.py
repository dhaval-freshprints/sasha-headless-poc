"""Live QA smoke tests for the kept-open Sasha browser.

These tests sign in to Fresh Prints QA inside the managed Docker image, start the
kept-open browser, and attach to it from separate commands the way Sasha does.
They are skipped unless SASHA_LIVE_QA=1 and the FP_* login variables are set
(for example in .env).

Run with:
    SASHA_LIVE_QA=1 .venv/bin/python -m unittest tests.test_account_manager_agent_browser
"""

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from dotenv import load_dotenv

from account_manager_agent.sandbox import DockerSandbox, SandboxConfig
from account_manager_agent.task import SashaTask


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LIVE_QA_ENABLED = os.environ.get("SASHA_LIVE_QA") == "1"
DEAL_ID = os.environ.get("SASHA_LIVE_QA_DEAL_ID", "397030")
# Disposable QA proof for the new-item workflow. Leave unset to skip persistence.
PROOF_ID = os.environ.get("SASHA_LIVE_QA_PROOF_ID", "").strip()
COMMAND_TIMEOUT_SECONDS = 120

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
                    "ACCOUNT_MANAGER_AGENT_SANDBOX_IMAGE", "sasha-account-manager-agent:local"
                ),
                runs_directory=Path(cls.runs_directory.name),
            ),
            executor_api_key="not-used-by-smoke-tests",
        )
        base_url = os.environ["FP_BASE_URL"].rstrip("/")
        cls.deal_url = f"{base_url}/dashboard/sales-pipeline/deal?id={DEAL_ID}"
        cls.quoter_url = f"{base_url}/dashboard/quoter"
        cls.handle = cls.sandbox.prepare(
            SashaTask(DEAL_ID, f"browser-smoke-{uuid.uuid4().hex[:8]}", cls.deal_url)
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
        self.assertEqual(result.get("status"), "started")

        exit_code, result = self.attach("open", self.deal_url)
        self.assertEqual(exit_code, 0, result)
        self.assertTrue(result["signedIn"])

    def test_explicit_restart_recovers_unhealthy_browser(self):
        exit_code, result = self.run_restart()
        self.assertEqual(exit_code, 0, result)
        self.assertEqual(result.get("status"), "restarted")

        exit_code, result = self.attach("open", self.deal_url)
        self.assertEqual(exit_code, 0, result)
        self.assertTrue(result["signedIn"])

    def test_browser_exec_serializes_a_workspace_script(self):
        script = (
            self.handle.workspace_directory / "browser_step.js"
        )
        script.write_text(
            'const { chromium } = require("playwright");\n'
            "(async () => {\n"
            '  const browser = await chromium.connectOverCDP("http://127.0.0.1:9222");\n'
            "  const pages = browser.contexts()[0].pages();\n"
            '  process.stdout.write(JSON.stringify({ pages: pages.length }));\n'
            "  await browser.close();\n"
            "})().catch((error) => { console.error(error); process.exit(1); });\n",
            encoding="utf-8",
        )
        process = self.run_in_container(
            "python", "/opt/sasha/browser_exec.py", "/workspace/browser_step.js"
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        self.assertIn('"pages"', process.stdout)

    def test_multiple_heavy_pages_then_close_extras(self):
        """Open several app pages, then close extras so only the deal stays."""
        base = os.environ["FP_BASE_URL"].rstrip("/")
        urls = json.dumps(
            [
                self.deal_url,
                self.quoter_url,
                f"{base}/dashboard/catalog",
                f"{base}/dashboard/designs",
            ]
        )
        script = self.handle.workspace_directory / "heavy_pages.js"
        script.write_text(
            "const { chromium } = require('playwright');\n"
            "(async () => {\n"
            "  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');\n"
            "  const context = browser.contexts()[0];\n"
            f"  const urls = {urls};\n"
            "  const opened = [];\n"
            "  for (const url of urls) {\n"
            "    const page = await context.newPage();\n"
            "    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });\n"
            "    opened.push(page.url());\n"
            "  }\n"
            "  const pages = context.pages();\n"
            "  const before = pages.length;\n"
            "  for (const page of pages.slice(1)) {\n"
            "    await page.close();\n"
            "  }\n"
            "  process.stdout.write(JSON.stringify({\n"
            "    opened,\n"
            "    before,\n"
            "    after: context.pages().length,\n"
            "  }));\n"
            "  await browser.close();\n"
            "})().catch((error) => {\n"
            "  process.stdout.write(JSON.stringify({ error: error.message.split('\\n')[0] }));\n"
            "  process.exit(1);\n"
            "});\n",
            encoding="utf-8",
        )
        process = self.run_in_container(
            "python", "/opt/sasha/browser_exec.py", "/workspace/heavy_pages.js",
            "--timeout-seconds", "90",
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        result = json.loads(process.stdout)
        self.assertGreaterEqual(result["before"], 4)
        self.assertEqual(result["after"], 1)
        self.assertEqual(len(result["opened"]), 4)

    def run_restart(self):
        process = self.run_in_container("node", "/opt/sasha/start_browser.js", "--restart")
        return process.returncode, json.loads(process.stdout)


@unittest.skipUnless(LIVE_QA_ENABLED, "set SASHA_LIVE_QA=1 to run live QA smoke tests")
@unittest.skipUnless(bool(PROOF_ID), "set SASHA_LIVE_QA_PROOF_ID for disposable proof workflow")
class DesignToolNewProofItemLiveTests(unittest.TestCase):
    """Reach save-ready on a new proof item. Persist only when PROOF_ID is disposable QA."""

    @classmethod
    def setUpClass(cls):
        cls.runs_directory = tempfile.TemporaryDirectory()
        cls.sandbox = DockerSandbox(
            SandboxConfig(
                image=os.environ.get(
                    "ACCOUNT_MANAGER_AGENT_SANDBOX_IMAGE", "sasha-account-manager-agent:local"
                ),
                runs_directory=Path(cls.runs_directory.name),
            ),
            executor_api_key="not-used-by-smoke-tests",
        )
        base_url = os.environ["FP_BASE_URL"].rstrip("/")
        cls.proof_url = f"{base_url}/dashboard/proof/{PROOF_ID}"
        cls.handle = cls.sandbox.prepare(
            SashaTask(DEAL_ID, f"proof-smoke-{uuid.uuid4().hex[:8]}", cls.proof_url)
        )
        try:
            cls.sandbox.start_container()
            cls.sandbox.authenticate(
                cls.proof_url,
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

    def test_opens_new_proof_item_and_reaches_save_ready(self):
        persist = os.environ.get("SASHA_LIVE_QA_PERSIST_PROOF") == "1"
        persist_js = "true" if persist else "false"
        proof_url = json.dumps(self.proof_url)
        script = self.handle.workspace_directory / "new_proof_item.js"
        script.write_text(
            "const { chromium } = require('playwright');\n"
            "(async () => {\n"
            "  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');\n"
            "  const context = browser.contexts()[0];\n"
            "  const page = context.pages()[0] || await context.newPage();\n"
            f"  await page.goto({proof_url}, {{ waitUntil: 'domcontentloaded', timeout: 60000 }});\n"
            "  await page.keyboard.press('Escape').catch(() => {});\n"
            "  const create = page.getByTestId('proof-create-new-item-button');\n"
            "  const visible = await create.isVisible().catch(() => false);\n"
            "  const beforePages = context.pages().map((p) => p.url());\n"
            "  if (visible) {\n"
            "    await create.click({ timeout: 15000 });\n"
            "  } else {\n"
            "    await page.getByText(/Create in Design Tool/i).first().click({ timeout: 15000 });\n"
            "  }\n"
            "  await page.waitForTimeout(2000);\n"
            "  const pages = context.pages();\n"
            "  const editor = pages.find((p) => /proofItemId=/i.test(p.url()) || /design/i.test(p.url()))\n"
            "    || pages[pages.length - 1];\n"
            "  await editor.bringToFront();\n"
            "  for (const other of pages) {\n"
            "    if (other !== editor && /editor|design|proofItemId=/i.test(other.url())) {\n"
            "      await other.close();\n"
            "    }\n"
            "  }\n"
            "  const save = editor.getByRole('button', { name: /^Save$/i }).first();\n"
            "  const saveReady = await save.isVisible().catch(() => false);\n"
            f"  const persist = {persist_js};\n"
            "  if (persist && saveReady) {\n"
            "    await save.click();\n"
            "    await editor.getByText(/Save as new revision|Save$/i).last()"
            ".click({ timeout: 15000 }).catch(() => {});\n"
            "  }\n"
            "  process.stdout.write(JSON.stringify({\n"
            "    proofUrl: page.url(),\n"
            "    editorUrl: editor.url(),\n"
            "    beforePages,\n"
            "    saveReady,\n"
            "    persisted: persist,\n"
            "    createVisible: visible,\n"
            "  }));\n"
            "  await browser.close();\n"
            "})().catch((error) => {\n"
            "  process.stdout.write(JSON.stringify({ error: error.message.split('\\n')[0] }));\n"
            "  process.exit(1);\n"
            "});\n",
            encoding="utf-8",
        )
        process = subprocess.run(
            [
                "docker",
                "exec",
                self.handle.container_name,
                "python",
                "/opt/sasha/browser_exec.py",
                "/workspace/new_proof_item.js",
                "--timeout-seconds",
                "120",
            ],
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        result = json.loads(process.stdout)
        self.assertTrue(result.get("saveReady"), result)
        self.assertIn("proof", result.get("editorUrl", "").lower() + result.get("proofUrl", "").lower())


if __name__ == "__main__":
    unittest.main()
