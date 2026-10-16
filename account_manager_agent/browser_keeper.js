// Keep one signed-in Chrome running for the whole Sasha turn.
// Sasha's scripts attach to it with chromium.connectOverCDP on port 9222.
// Started in the background by start_browser.js; it runs until the container stops.

const fs = require("fs");

const PROFILE_DIRECTORY = process.env.SASHA_BROWSER_PROFILE || "/browser-profile";
const DEBUGGING_PORT = Number(process.env.SASHA_BROWSER_PORT || 9222);
const KEEP_ALIVE_INTERVAL_MS = 60 * 60 * 1000;
const ARTIFACTS = process.env.SASHA_BROWSER_ARTIFACTS || "/workspace/artifacts";
const STATE_PATH = `${ARTIFACTS}/browser_keeper.state.json`;
const PID_PATH = `${ARTIFACTS}/browser_keeper.pid`;

function writeState(state) {
  try {
    fs.mkdirSync(ARTIFACTS, { recursive: true });
    fs.writeFileSync(STATE_PATH, `${JSON.stringify({ ...state, updated_at: new Date().toISOString() })}\n`);
    fs.writeFileSync(PID_PATH, `${process.pid}\n`);
  } catch (error) {
    process.stderr.write(`Browser keeper could not write state: ${error.message}\n`);
  }
}

function existingKeeperIsAlive() {
  try {
    const pid = Number.parseInt(fs.readFileSync(PID_PATH, "utf8").trim(), 10);
    if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) {
      return false;
    }
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return false;
  }
}

async function main() {
  if (existingKeeperIsAlive()) {
    process.stderr.write("Another browser keeper already holds the PID file; exiting.\n");
    process.exit(2);
  }
  const { chromium } = require("playwright");
  writeState({ status: "starting", pid: process.pid, port: DEBUGGING_PORT });
  const context = await chromium.launchPersistentContext(PROFILE_DIRECTORY, {
    headless: true,
    viewport: { width: 1440, height: 900 },
    args: ["--no-sandbox", `--remote-debugging-port=${DEBUGGING_PORT}`],
  });
  context.on("close", () => {
    writeState({ status: "closed", pid: process.pid, port: DEBUGGING_PORT });
    process.exit(1);
  });
  setInterval(() => {}, KEEP_ALIVE_INTERVAL_MS);
  writeState({ status: "running", pid: process.pid, port: DEBUGGING_PORT });
  process.stdout.write(`Browser is running on port ${DEBUGGING_PORT}\n`);
}

main().catch((error) => {
  writeState({ status: "failed", pid: process.pid, port: DEBUGGING_PORT, error: error.message });
  process.stderr.write(`Browser did not start: ${error.message}\n`);
  process.exit(1);
});
