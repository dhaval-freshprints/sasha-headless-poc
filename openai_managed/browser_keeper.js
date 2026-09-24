// Keep one signed-in Chrome running for the whole Sasha turn.
// Sasha's scripts attach to it with chromium.connectOverCDP on port 9222.
// Started in the background by start_browser.js; it runs until the container stops.

const { chromium } = require("playwright");

const PROFILE_DIRECTORY = "/browser-profile";
const DEBUGGING_PORT = 9222;
const KEEP_ALIVE_INTERVAL_MS = 60 * 60 * 1000;

async function main() {
  const context = await chromium.launchPersistentContext(PROFILE_DIRECTORY, {
    headless: true,
    viewport: { width: 1440, height: 900 },
    args: ["--no-sandbox", `--remote-debugging-port=${DEBUGGING_PORT}`],
  });
  context.on("close", () => process.exit(1));
  setInterval(() => {}, KEEP_ALIVE_INTERVAL_MS);
  process.stdout.write(`Browser is running on port ${DEBUGGING_PORT}\n`);
}

main().catch((error) => {
  process.stderr.write(`Browser did not start: ${error.message}\n`);
  process.exit(1);
});
