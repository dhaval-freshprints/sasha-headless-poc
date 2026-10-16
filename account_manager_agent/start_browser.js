// Start the kept-open Sasha browser if it is not running, then wait until it answers.
// The runner runs this once after login. Sasha / browser_exec run it again if attaching fails.
// Use --restart to terminate any stale keeper/Chrome on port 9222 before starting fresh.
// Prints one JSON object: {"status":"running"}, {"status":"started"}, {"status":"restarted"}
// or {"status":"failed","reason":"..."}. Exit code 0 on success, 1 on failure.

const fs = require("fs");
const path = require("path");
const { spawn, execFileSync } = require("child_process");

const VERSION_URL = process.env.SASHA_BROWSER_CDP_URL || "http://127.0.0.1:9222/json/version";
const KEEPER_SCRIPT = process.env.SASHA_BROWSER_KEEPER_SCRIPT || path.join(__dirname, "browser_keeper.js");
const ARTIFACTS = process.env.SASHA_BROWSER_ARTIFACTS || "/workspace/artifacts";
const KEEPER_LOG = `${ARTIFACTS}/browser_keeper.log`;
const PID_PATH = `${ARTIFACTS}/browser_keeper.pid`;
const STATE_PATH = `${ARTIFACTS}/browser_keeper.state.json`;
const START_TIMEOUT_MS = Number(process.env.SASHA_BROWSER_START_TIMEOUT_MS || 30000);
const POLL_INTERVAL_MS = 250;
const REQUEST_TIMEOUT_MS = 1000;
const PORT_CLEAR_TIMEOUT_MS = Number(process.env.SASHA_BROWSER_PORT_CLEAR_MS || 10000);

function ensureArtifacts() {
  fs.mkdirSync(ARTIFACTS, { recursive: true });
}

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

function pidIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return false;
  }
}

function readKeeperPid() {
  try {
    return Number.parseInt(fs.readFileSync(PID_PATH, "utf8").trim(), 10);
  } catch (error) {
    return null;
  }
}

function listChromePids() {
  try {
    const output = execFileSync("ps", ["-eo", "pid=,args="], { encoding: "utf8" });
    return output
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.includes("remote-debugging-port=9222") || line.includes("chrome-headless-shell"))
      .map((line) => Number.parseInt(line.split(/\s+/)[0], 10))
      .filter((pid) => Number.isInteger(pid) && pid > 0 && pid !== process.pid);
  } catch (error) {
    return [];
  }
}

function terminatePid(pid, signal) {
  try {
    process.kill(pid, signal);
  } catch (error) {
    // Already gone.
  }
}

async function terminateStaleBrowser() {
  ensureArtifacts();
  const targets = new Set();
  const keeperPid = readKeeperPid();
  if (pidIsAlive(keeperPid)) {
    targets.add(keeperPid);
  }
  for (const pid of listChromePids()) {
    targets.add(pid);
  }
  for (const pid of targets) {
    terminatePid(pid, "SIGTERM");
  }
  const deadline = Date.now() + PORT_CLEAR_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const alive = [...targets].filter((pid) => pidIsAlive(pid));
    const answering = await isBrowserAnswering();
    if (!alive.length && !answering) {
      break;
    }
    await sleep(200);
  }
  for (const pid of [...targets].filter((pid) => pidIsAlive(pid))) {
    terminatePid(pid, "SIGKILL");
  }
  const clearDeadline = Date.now() + PORT_CLEAR_TIMEOUT_MS;
  while (Date.now() < clearDeadline && (await isBrowserAnswering())) {
    await sleep(200);
  }
  try {
    fs.unlinkSync(PID_PATH);
  } catch (error) {
    // Absent is fine.
  }
  fs.writeFileSync(
    STATE_PATH,
    `${JSON.stringify({ status: "stopped", updated_at: new Date().toISOString() })}\n`,
  );
  if (await isBrowserAnswering()) {
    return "port 9222 still answers after terminating the previous browser";
  }
  return "";
}

function startKeeperInBackground() {
  ensureArtifacts();
  const log = fs.openSync(KEEPER_LOG, "a");
  const keeper = spawn(process.execPath, [KEEPER_SCRIPT], {
    detached: true,
    stdio: ["ignore", log, log],
  });
  const state = { exitCode: null, pid: keeper.pid };
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
  const restart = process.argv.includes("--restart");
  if (!restart && (await isBrowserAnswering())) {
    printResult({ status: "running" });
    return 0;
  }
  if (restart || !(await isBrowserAnswering())) {
    const stopProblem = await terminateStaleBrowser();
    if (stopProblem) {
      printResult({ status: "failed", reason: stopProblem });
      return 1;
    }
  }
  // Guard against two keepers racing after a crash: refuse if CDP came back meanwhile.
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
  printResult({ status: restart ? "restarted" : "started", pid: keeperState.pid });
  return 0;
}

if (require.main === module) {
  main().then((exitCode) => {
    process.exit(exitCode);
  });
}

module.exports = {
  ARTIFACTS,
  PID_PATH,
  STATE_PATH,
  pidIsAlive,
  readKeeperPid,
  terminateStaleBrowser,
  startKeeperInBackground,
  main,
};
