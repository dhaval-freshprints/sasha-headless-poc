# BROWSER01 — One signed-in browser for the whole Sasha turn

## Status (2026-09-25)

| Build step | Status |
|---|---|
| 1. `browser_keeper.js`, `start_browser.js` | Done: [browser_keeper.js](openai_managed/browser_keeper.js), [start_browser.js](openai_managed/start_browser.js) |
| 2. Sandbox and runner, offline tests | Done: [sandbox.py](openai_managed/sandbox.py) `start_browser()`, [runner.py](openai_managed/runner.py) `browser_start_failed`; 73 offline tests pass |
| 3. Task message and SKILL.md | Done |
| 4. Live QA smoke tests | Done: [test_openai_managed_browser.py](tests/test_openai_managed_browser.py), 4 of 4 pass (33.6 s) |
| 5. Crash eval (the risk) | Done: **risk does not occur**; see below |
| 6. A/B eval | Done: both scenarios faster, identical prices; see below |
| Logging | Done, plus one blind spot found and fixed; see below |

### Step 5: crash eval — does a browser Sasha restarts survive its command?

A scratch copy of the runner skipped starting the browser. C1717 message on deal 303931.
The seed history file was already gone from `runs/`, so this run had no history.

1. Sasha's first attach failed: `connectOverCDP: connect ECONNREFUSED 127.0.0.1:9222`.
2. Sasha ran `node /opt/sasha/start_browser.js`, which returned `{"status":"started"}` in 697 ms,
   and that command ended.
3. **All 20 later browser commands attached to that same browser** through `connectOverCDP`.
   `browser_keeper.log` shows `Browser is running on port 9222`.
4. 0 Chrome launches of Sasha's own. The reply was $79.07 each, $3,795.36, the same as every
   earlier run of this message.

Conclusion: the managed executor does **not** stop background processes when a command
ends. The fallback "runner watches and restarts the browser" is not needed.

Turn: 156 s, 23 model calls, 22 commands, 618,618 input tokens, $0.245.

### Step 6: A/B eval

All four runs started at the same moment, one per arm and scenario, with seeded history in
scratch runs directories. Arm A = committed code (`git archive HEAD`, `f26ced8`). Arm B = kept-open
browser.

| Scenario | Measure | A: launch per command | B: kept-open browser |
|---|---|---|---|
| C1717 Screen Print price (deal 303931) | Sasha turn | 382 s | **134 s** |
| | Total run | 7 m 41 s | **3 m 25 s** |
| | Model calls | 29 | 21 |
| | Input tokens | 1,953,845 | **581,961** |
| | Cost | $0.539 | **$0.230** |
| | Chrome launched by Sasha | 16 (inside `inspect.js`) | **0** (13 attaches) |
| | Price | $79.07 / $3,795.36 | $79.07 / $3,795.36 |
| Nike embroidery + Miami marks (deal 303933) | Sasha turn | 213 s | **118 s** |
| | Total run | 4 m 46 s | **3 m 12 s** |
| | Model calls | 18 | 15 |
| | Input tokens | 754,044 | **329,130** |
| | Cost | $0.266 | **$0.158** |
| | Chrome launched by Sasha | 11 (inside `quote.js` and `inspect.js`) | **0** (13 attaches) |
| | Price | $97.21 / $4,082.82 | $97.21 / $4,082.82 |

Model calls fell only a little. The saving is mostly command time: in Arm A each
`node inspect.js` run took about 5–12 s to launch Chrome and replay the page, while in
Arm B commands took about 0.2–5 s. Fewer long outputs also means much less input.

One run per arm, so these show a direction, not an average.

### Logging, and a blind spot found in Step 6

- Progress lines work: `Sasha restarted the browser: started` appeared in the crash eval.
- **Blind spot:** Arm A had 0 launches in command text, yet launched Chrome 27 times. Sasha
  wrote `inspect.js` and `quote.js` with an editing tool (no command item) and then ran
  `node /workspace/inspect.js`, so the launch was only inside the file.
- **Fix:** at cleanup, the runner also scans the workspace's `.js`, `.cjs` and `.mjs` files,
  skipping `capabilities/` and `client-files/`, and prints
  `[cleanup] WARNING: Sasha's script <name> launches its own Chrome`. Checked on the real
  runs: it flags both Arm A runs and nothing in Arm B. Limit: a script written outside
  `/workspace`, for example in `/tmp`, is not scanned.

### Not checked

- Chrome memory over a long turn (`docker stats`) was not measured. All runs completed within
  the 2 GB container limit.

## Goal

Stop Sasha from starting a new Chrome for every command. The runner opens one
signed-in Chrome after login and keeps it running for the whole turn. Each of Sasha's
commands **attaches** to that browser, works on the page that is already open, and
detaches.

## Scope

In scope:

- A runner-owned script that starts the kept-open browser, used by the runner at the
  start and by Sasha to restart it after a crash.
- Runner and sandbox changes to start it after login.
- Task message and skill wording: attach, do not launch; restart if attaching fails.
- Logging when Sasha restarts the browser or launches its own Chrome anyway.
- Offline tests, live QA smoke tests, and a managed A/B eval.

Out of scope:

- `quote.js` and any other workflow scripts. That work is stashed as
  `quote-js-work-explore-alternatives-20260924` (commit `4b2f82dd`).
- A selector map.
- A browser command-line tool for Sasha. Sasha keeps writing its own Playwright code.
- Other speed work (`--init`, parallel login, earlier result return).

## Why

Today each Sasha command is a short Node program that launches Chrome, walks from the
start to the screen it needs, does one new thing, and closes Chrome.

| Fact | Proof |
|---|---|
| Every Sasha script launches its own Chrome | Run `sasha-303931-3b497d9a`: each `.cjs` script starts with `chromium.launchPersistentContext('/browser-profile', …)` |
| Each attempt replays all earlier steps | Same run: `quote2.cjs` was edited and re-run 8 times, taking about 8–38 s each; only the last line changed each time |
| The login script closes its browser | [setup_auth.js](openai_managed/setup_auth.js) calls `context.close()` in `finally` |
| We tell Sasha to launch Chrome and close it | [runner.py:576-580](openai_managed/runner.py:576): "Use Node.js Playwright in headless mode with the authenticated profile at /browser-profile … Close the browser before returning." [SKILL.md:23](openai_managed/capabilities/sasha-sales/SKILL.md:23): "Close the browser and return …" |
| A kept-open browser works in our image | Test on 2026-09-24 in `sasha-openai-managed:local`, signed in to QA: attach + open Quoter 4,099 ms; attach + type `G500` 392 ms; attach + look 57 ms, and the style box still showed `G500` |
| Disconnecting does not kill the kept-open browser | Same test: each command called `browser.close()` on its `connectOverCDP` connection, and the next command still found the page and its state |
| Two Chromes can share `/browser-profile` | Test on 2026-09-24: a second Chrome started and worked while another held the profile; no Singleton lock files |
| A fresh Chrome on `/browser-profile` is still signed in | Every script in run `sasha-303931-3b497d9a` launched a new Chrome on the profile and reached the Quoter without logging in |

### Assumptions

- (Assumption) Sasha will follow "attach, do not launch" in every command. **Confirm or
  break:** count Chrome launches in Sasha's commands during the evals. Target: 0.
- (Assumption) A browser that Sasha restarts from inside one of its commands keeps
  running after that command ends. The managed executor might stop background
  processes when a command finishes. **Confirm or break:** Step 5 (crash eval). If it
  breaks, the restart must be done differently (see Risks).
- (Assumption) Attaching removes most of the per-step cost, so quoting turns get much
  faster. **Confirm or break:** Step 6 (A/B eval).

## Design

### How it works

```
Runner                                    Container
──────                                    ─────────
start container
sign in           ──docker exec──▶  setup_auth.js       (unchanged; closes its browser)
start browser     ──docker exec──▶  start_browser.js
                                      └─ spawns browser_keeper.js in the background
                                           └─ Chrome on /browser-profile,
                                              debugging port 127.0.0.1:9222
                                      └─ waits until the port answers, exits 0
create session, connect executor
Sasha works:
  command 1  ─────────────────────▶  node script → connectOverCDP(9222) → act → disconnect
  command 2  ─────────────────────▶  node script → connectOverCDP(9222) → act → disconnect
  (attach fails)
  command n  ─────────────────────▶  node /opt/sasha/start_browser.js → attach again
stop container    ───────────────▶  everything, including Chrome, is removed
```

Port 9222 exists only inside the disposable container. It is not published to the host.

### New file 1: `openai_managed/browser_keeper.js`

Keeps Chrome running. About 20 lines.

1. `chromium.launchPersistentContext("/browser-profile", …)` with the same options as
   [setup_auth.js](openai_managed/setup_auth.js) (`headless: true`, viewport 1440×900,
   `--no-sandbox`) plus `--remote-debugging-port=9222`.
2. Stay alive until the container stops.

It does not log in. The profile is already signed in by `setup_auth.js`.

### New file 2: `openai_managed/start_browser.js`

Starts the keeper if needed and waits until it answers. Used by the runner at the start
and by Sasha after a crash, so start and restart share one code path.

1. If `http://127.0.0.1:9222/json/version` answers, print `{"status":"running"}` and
   exit 0. Running it twice is safe.
2. Otherwise spawn `browser_keeper.js` detached (`detached: true`, output to
   `/workspace/artifacts/browser_keeper.log`, `unref()`), so it outlives this script.
3. Poll the port every 250 ms for up to 30 s.
4. Print `{"status":"started"}` and exit 0, or `{"status":"failed","reason":…}` and exit 1.

Both files are mounted read-only at `/opt/sasha/`, the same way `setup_auth.js` is today
([sandbox.py:121](openai_managed/sandbox.py:121)).

### Runner and sandbox changes

- [sandbox.py](openai_managed/sandbox.py):
  - mount `browser_keeper.js` and `start_browser.js` read-only in `start_container`;
  - add `start_browser()`: runs `docker exec <container> node /opt/sasha/start_browser.js`
    and raises a clear error if it does not return `started` or `running`.
- [runner.py](openai_managed/runner.py):
  - call `sandbox.start_browser()` right after authentication, with the progress line
    `      Signed-in browser is open for the whole turn`;
  - if it fails, the turn fails with `failure_code="browser_start_failed"` before any
    OpenAI session is created;
  - replace the browser sentence in the task message ([runner.py:576-580](openai_managed/runner.py:576))
    with the wording below.

### Wording for Sasha

Task message, replacing "Use Node.js Playwright in headless mode … Close the browser
before returning.":

```text
A signed-in headless Chrome is already running for this turn. In every Playwright
script, attach to it with chromium.connectOverCDP('http://127.0.0.1:9222') and use
browser.contexts()[0]. Do not launch Chrome yourself. The page keeps its state
between commands, so check page.url() before acting. At the end of each script,
call browser.close() on the connection; this only disconnects. If attaching fails,
run `node /opt/sasha/start_browser.js`, then attach again. If it fails twice, return
a failed result.
```

The rest of the task message is unchanged: start at the exact deal URL, save
screenshots and `visited_urls.json`, and return only the result JSON.

[SKILL.md](openai_managed/capabilities/sasha-sales/SKILL.md) changes:

- Step 5: "Use Playwright through the supplied Node environment" becomes "Use Playwright
  through the supplied Node environment, attached to the already-running browser
  described in the task."
- Step 10: "Close the browser and return …" becomes "Disconnect from the browser and
  return …".

### Logging

In [runner.py](openai_managed/runner.py) `SessionEvents._report_event`, next to the
existing tool-step lines:

| Sasha's command contains | Progress line |
|---|---|
| `node /opt/sasha/start_browser.js` | `      Sasha restarted the browser: <status from its JSON>` |
| `launchPersistentContext(` or `chromium.launch(` | `      WARNING: Sasha launched its own Chrome` |

The second line tells us quickly whether Sasha is following the new instruction.

## Risks

| Risk | What we do |
|---|---|
| Sasha still launches its own Chrome sometimes | The warning line shows it. Nothing breaks: that command is just slow, as today |
| The executor stops Sasha's background processes, so a restart from Sasha dies when its command ends | Found by Step 5. Fallback: the runner restarts the browser. The runner watches the port during the turn and runs `start_browser.js` itself if it stops answering |
| Sasha acts on the wrong page because state carries over | The wording tells Sasha to check `page.url()` first. Watch for it in the eval transcripts |
| Design Tool opens a new tab | It appears in `browser.contexts()[0].pages()`. Sasha already has to pick the right page today (workplace.md, "General interface behavior") |
| Chrome memory grows over a long turn | The container is limited to 2 GB and turns are at most 10 minutes. Check `docker stats` during the evals |

## Tests

### Offline (always run)

- Sandbox: `start_container` mounts both new files read-only; `start_browser` runs
  `docker exec … node /opt/sasha/start_browser.js`; a `failed` status raises.
- Runner:
  - the task message contains `connectOverCDP('http://127.0.0.1:9222')` and no longer
    says "Close the browser";
  - a failed browser start returns `browser_start_failed` and creates no session;
  - the two new progress lines appear for matching commands and not for others.

### Live QA smoke tests (`SASHA_LIVE_QA=1`)

New file `tests/test_openai_managed_browser.py`, using the real `DockerSandbox` like the
earlier live tests:

| Test | Expect |
|---|---|
| Start | After `authenticate` + `start_browser`, a script can attach and open the deal page without logging in |
| State carries over | Command 1 opens the Quoter and types `G500`; command 2 attaches and reads `G500` in the style box |
| Disconnect keeps it alive | After a script calls `browser.close()`, the next script still attaches |
| Idempotent | Running `start_browser.js` again returns `running` |
| Restart after crash | Kill Chrome, attaching fails, run `start_browser.js`, attach again, deal page opens signed in |

## Build order

1. `browser_keeper.js` and `start_browser.js`.
2. Sandbox and runner changes, with the offline tests.
3. Task message and SKILL.md wording.
4. Live QA smoke tests.
5. **Crash eval (managed).** Run from a scratch copy where the runner skips starting the
   browser, so Sasha's first attach fails. Pass: Sasha runs `start_browser.js`, later
   commands attach, the turn completes, and no Chrome launches of its own are logged.
   This tests the second assumption.
6. **A/B eval (managed).** Arm A: committed code today. Arm B: kept-open browser. Both
   arms run at the same time on the same deal state, with seeded conversation history
   and scratch runs directories so real conversation files are not changed:

   | Scenario | Deal | Message |
   |---|---|---|
   | Screen Print price | 303931 | "Can you give me a price for 48 black Comfort Colors C1717 tees with a 2-color front screen print?" |
   | Embroidery + licensed follow-up (the web run `09ab4d52…`) | 303933 | "How much Nike one cost if I purchase 42 pieces?" with that run's history |

## How we know it worked

| Measure | Reference today | Target |
|---|---|---|
| Chrome launches by Sasha's own commands | one per browser command | 0 |
| Sasha turn, C1717 price | 339 s (Arm A measured 2026-09-24) | re-measured Arm A vs Arm B; Arm B clearly lower |
| Sasha turn, Nike embroidery follow-up | 268 s total run (web run `09ab4d52…`) | re-measured Arm A vs Arm B; Arm B clearly lower |
| Prices in the reply | — | identical in both arms |
| Crash eval | — | completes via `start_browser.js` |
| Smoke tests | — | all pass |

One run per arm shows a direction, not an average. If the result is close, run each
arm twice more before deciding.
