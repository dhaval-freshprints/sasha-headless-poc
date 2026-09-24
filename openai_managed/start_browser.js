// Start the kept-open Sasha browser if it is not running, then wait until it answers.
// The runner runs this once after login. Sasha runs it again if attaching fails.
// Prints one JSON object: {"status":"running"}, {"status":"started"} or
// {"status":"failed","reason":"..."}. Exit code 0 on success, 1 on failure.

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const VERSION_URL = "http://127.0.0.1:9222/json/version";
const KEEPER_SCRIPT = path.join(__dirname, "browser_keeper.js");
const KEEPER_LOG = "/workspace/artifacts/browser_keeper.log";
const START_TIMEOUT_MS = 30000;
const POLL_INTERVAL_MS = 250;
const REQUEST_TIMEOUT_MS = 1000;

async function isBrowserAnswering() {
  try {
    const response = await fetch(VERSION_URL, {
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

// detached: true gives the keeper its own session, so it keeps running after
// this script (and the command that ran it) has finished.
function startKeeperInBackground() {
  const log = fs.openSync(KEEPER_LOG, "a");
  const keeper = spawn(process.execPath, [KEEPER_SCRIPT], {
    detached: true,
    stdio: ["ignore", log, log],
  });
  const state = { exitCode: null };
  keeper.on("exit", (code) => {
    state.exitCode = code;
  });
  keeper.unref();
  return state;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitUntilAnswering(keeperState) {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await isBrowserAnswering()) {
      return "";
    }
    if (keeperState.exitCode !== null) {
      return `browser keeper exited with code ${keeperState.exitCode}; see ${KEEPER_LOG}`;
    }
    await sleep(POLL_INTERVAL_MS);
  }
  return `browser did not answer within ${START_TIMEOUT_MS / 1000} seconds; see ${KEEPER_LOG}`;
}

function printResult(result) {
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

async function main() {
  if (await isBrowserAnswering()) {
    printResult({ status: "running" });
    return 0;
  }
  const keeperState = startKeeperInBackground();
  const problem = await waitUntilAnswering(keeperState);
  if (problem) {
    printResult({ status: "failed", reason: problem });
    return 1;
  }
  printResult({ status: "started" });
  return 0;
}

main().then((exitCode) => {
  process.exit(exitCode);
});
