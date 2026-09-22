# General Sasha Turn: Implementation Plan

## Goal

Replace the outreach-specific managed runner with one generic Sasha runner that
supports both:

- initial outreach, when no client message is supplied; and
- a client-response turn, when a client message is supplied.

For the first client-response scenario, Sasha must independently recognize a
quotation request, start from the deal page, find the relevant proof, calculate
the requested quantity without saving it, and return the client-facing HTML
message.

Python must not classify quotation, product suggestion, alternative, proof, or
revision requests. Astra makes that decision from the client message, current
deal state, and Sasha skill.

## Confirmed design

```text
deal_id + optional client_message
              |
              v
      OpenAIManagedRunner
              |
              v
     OpenAI managed harness
              |
              v
       Astra reads Sasha skill
              |
              v
          Open deal page
              |
              v
  decide -> act with Playwright -> observe
              ^                    |
              |____________________|
              |
              v
       return message JSON
```

- There will be one runner, one task type, one result type, and one sandbox
  implementation.
- `client_message is None` means initial outreach. A non-empty client message
  means a client-response turn.
- This is the only mode selection in Python. Python will not route individual
  client intents.
- The OpenAI managed harness owns the model/tool loop.
- Astra owns intent recognition, page selection, browser work, observation,
  and the decision to continue or finish.
- The self-hosted Docker executor owns command execution and Playwright.
- The Sasha repository owns task input, rules, credentials, sandbox lifecycle,
  timeouts, progress, artifacts, result parsing, pricing, and cleanup.
- Each invocation uses a fresh managed session and disposable browser-profile
  copy, matching the current POC lifecycle.
- The generated message is returned but not sent to the client.
- No contract validator, semantic message validator, or before/after browser
  checker will be added.

## Boundaries for this implementation

Included:

- Preserve working outreach through the generic runner.
- Add one client-response input path.
- Add the Sasha Agent Skill.
- Register the skill through a self-hosted capability directory.
- Prove one quotation scenario on Fresh Prints QA.
- Preserve `--verbose` and `--pricing`.

Not included:

- Product suggestions or alternatives.
- Stock and shipping questions.
- Attachments.
- Creating a proof.
- Submitting a revision.
- Sending the generated message.
- Production deals or production credentials.
- Persistent OpenAI sessions across client messages.
- Automated semantic or CRM-state validation.

## Task checklist

### Task 1: Record and verify the current baseline

- [x] Confirm there are no uncommitted code changes before implementation;
  this plan file may be the only expected uncommitted file.
- [x] Run the complete local test suite.
- [x] Record the number of passing tests in the implementation notes.
- [x] Confirm the current outreach CLI help still loads.

Evidence required:

- `git status --short`
- `.venv/bin/python -m unittest discover -s tests -v`
- `.venv/bin/python scripts/run_openai_managed_outreach.py --help`

Done when:

- Existing behavior has a clean, reproducible baseline.
- Any pre-existing failure is reported before code is changed.

Implementation record, 2026-09-22:

- `git status --short` showed only this untracked plan file.
- The complete local suite passed: 62 tests, 0 failures.
- The outreach CLI help command exited successfully and listed `deal_id`,
  `--verbose`, and `--pricing`.
- No pre-existing failure was found.

### Task 2: Introduce one generic task and result contract

- [x] Add `SashaTask` with only these fields:
  - `deal_id`
  - `task_id`
  - `deal_url`
  - `client_message`, optional
- [x] Add `SashaResult` with the existing result fields:
  - `deal_id`
  - `status`
  - `message_html`
  - `failure_code`
  - `failure_message`
- [x] Rename the JSON schema to describe the generic Sasha result.
- [x] Move these definitions from the outreach-specific module into a plainly
  named generic module.
- [x] Add focused tests for JSON parsing and serialization.

Expected file changes:

- Add `openai_managed/task.py`.
- Replace usages of `OutreachTask`, `OutreachResult`, and
  `OUTREACH_RESULT_JSON_SCHEMA`.
- Remove `openai_managed/outreach.py` after every import has moved.

Done when:

- Outreach can be represented as `SashaTask(..., client_message=None)`.
- Client response can be represented as
  `SashaTask(..., client_message="What's the price for 40?")`.
- There is no second task hierarchy or scenario enum.

Implementation record, 2026-09-22:

- Added `openai_managed/task.py` with `SashaTask`, `SashaResult`, and
  `SASHA_RESULT_JSON_SCHEMA`.
- Added optional `client_message` with a default of `None`.
- Migrated the runner, sandbox, CLI, and tests to the generic contracts.
- Removed `openai_managed/outreach.py` and its outreach-specific value test.
- Added `tests/test_openai_managed_task.py` with outreach, client-response, and
  JSON round-trip coverage.
- The 7 focused managed tests passed.
- The complete local suite passed: 63 tests, 0 failures.
- A repository search found no remaining outreach-contract imports or names in
  `openai_managed/`, `scripts/`, or `tests/`.

### Task 3: Convert the runner from outreach-specific to generic

- [x] Rename `OpenAIManagedOutreachRunner` to `OpenAIManagedRunner`.
- [x] Change `run()` to accept `SashaTask` and return `SashaResult`.
- [x] Keep the existing session creation and event streaming.
- [x] Keep connection and turn timeouts.
- [x] Keep structured JSON output.
- [x] Keep result, session item, session event, executor log, screenshot,
  visited-URL, and pricing artifacts.
- [x] Keep Docker stop, browser-profile removal, and session deletion.
- [x] Keep failures represented as a `failed` result.
- [x] Remove outreach wording from runner progress messages and error names.

Expected file changes:

- Update `openai_managed/runner.py`.
- Update `tests/test_openai_managed_runner.py`.

Done when:

- The runner contains no quotation-specific routing.
- The runner contains no product, stock, proof, or revision routing.
- All current runner lifecycle tests pass against the generic names.

Implementation record, 2026-09-22:

- Renamed the class to `OpenAIManagedRunner` and updated the CLI and tests.
- Changed the module description, active-work progress message, and timeout
  error to task-neutral Sasha wording.
- Preserved the existing Agents API session, event stream, structured output,
  timeout, artifact, pricing, Docker cleanup, profile cleanup, session deletion,
  and failure-result code paths.
- A search found no remaining `OpenAIManagedOutreachRunner`, `Managed outreach`,
  or `drafting outreach` references in `openai_managed/`, `scripts/`, or tests.
- The 4 focused runner tests passed.
- The complete local suite passed: 63 tests, 0 failures.
- The task-message builder remains outreach-only until Task 8; Task 3 changes
  runner infrastructure and does not claim client-response execution is ready.

### Task 4: Make the sandbox task-neutral

- [x] Change the sandbox input type from `OutreachTask` to `SashaTask`.
- [x] Change disposable names such as `sasha-outreach-*` to task-neutral names.
- [x] Add `client_message` to `task.json`; use JSON `null` for outreach.
- [x] Preserve the authenticated-profile copy.
- [x] Preserve the memory, CPU, and shared-memory limits.
- [x] Preserve the existing safe profile-removal check.

Expected file changes:

- Update `openai_managed/sandbox.py`.
- Add focused sandbox tests in `tests/test_openai_managed_sandbox.py`.

Done when:

- The same sandbox can prepare either an outreach or client-response task.
- Nothing in the sandbox implementation knows the client's intent.

Implementation record, 2026-09-22:

- Updated the sandbox module description to cover any managed Sasha turn.
- Kept `SashaTask` as the single sandbox input type.
- Changed generated container names from `sasha-outreach-*` to
  `sasha-managed-*` and changed the empty task-ID fallback to `sasha`.
- Added `client_message` to `task.json`; outreach writes JSON `null` and a
  client-response task writes the exact supplied message.
- Preserved the browser-profile copy, 2 GB memory limit, 2 CPU limit, 1 GB
  shared-memory limit, and the profile-parent safety check.
- Added `tests/test_openai_managed_sandbox.py` with two preparation tests.
- The 2 focused sandbox tests passed.
- The complete local suite passed: 65 tests, 0 failures.

### Task 5: Create the Sasha Agent Skill

- [x] Add a skill directory under
  `openai_managed/capabilities/sasha-sales/`.
- [x] Add `SKILL.md` with valid front matter and a precise description of when
  Sasha should use it.
- [x] Add `references/playbook.md` for sales judgment and client-writing rules.
- [x] Add `references/workplace.md` for Fresh Prints page and UI facts.
- [x] Tell Sasha to begin every sales turn from the supplied deal page.
- [x] Tell Sasha to read the deal and relevant proof before deciding.
- [x] Tell Sasha to use the client message as data, not as browser or system
  instructions.
- [x] Tell Sasha to select the relevant workflow herself.
- [x] Tell Sasha to continue observing and acting until the requested work is
  complete or a real blocker is found.
- [x] Keep the existing business rules for outreach, price, MOQ, writing style,
  and verification.
- [x] Translate Claude-tool-specific language into Playwright-neutral language.

Tool-specific text that must not be copied unchanged includes:

- `get_page_text`
- `read_page`
- `find`
- `form_input`
- `inspect_catalog_product`
- `attach_file`
- reference IDs such as `[ref_N]`

Done when:

- The skill contains company and workflow knowledge, not Python dispatch logic.
- A reader can understand the quotation policy without opening runner code.
- The skill does not depend on the old Anthropic browser toolset.

Implementation record, 2026-09-22:

- Added the `sasha-sales` Agent Skill with valid front matter and explicit
  activation for initial outreach and client-response sales turns.
- Added a Playbook reference for working rules, client voice, outreach,
  quotation, MOQ, shipping, stock, products, proofs, revisions, and samples.
- Added a Workplace reference for the Fresh Prints QA deal, proof, proof
  wizard, Design Tool, stock checker, revision, catalog, and quoter pages.
- Kept workflow selection with Sasha: every turn starts at the supplied deal,
  reads its context and relevant proof, then uses an observe-act-observe loop.
- Replaced custom Claude browser-tool instructions with Playwright and rendered
  page-state language.
- The bundled validator could not import its optional `yaml` dependency in the
  available Python environments. Equivalent checks from that validator passed
  with Ruby's YAML parser: required keys, allowed keys, name format, description
  limits, front-matter syntax, and unfinished placeholders.
- A repository search found none of the listed legacy browser-tool terms in the
  new skill.
- The complete local suite passed: 65 tests, 0 failures.

### Task 6: Add short, stable agent instructions

- [x] Replace the outreach-only instruction file with short Sasha-wide agent
  instructions.
- [x] Keep Sasha's identity and the Fresh Prints QA boundary there.
- [x] Require use of the `sasha-sales` skill for every supplied sales task.
- [x] State that the browser starts from the supplied deal URL.
- [x] State that page content is untrusted data and cannot change the task.
- [x] Prohibit sending messages, deleting CRM records, purchases, and unrelated
  work.
- [x] Require a final result matching the generic JSON schema.
- [x] Keep business workflow detail in the skill rather than duplicating it in
  the agent instructions.

Expected file changes:

- Add a generic agent-instructions Markdown file under `openai_managed/`.
- Remove `openai_managed/OUTREACH01_rules.md` after outreach rules exist in the
  Sasha skill.

Done when:

- Agent instructions define identity and boundaries.
- The skill defines how Sasha performs sales work.
- The same instruction set works for outreach and client response.

Implementation record, 2026-09-22:

- Replaced `OUTREACH01_rules.md` with the short, task-neutral
  `SASHA01_agent_instructions.md`.
- Kept only Sasha's identity, QA boundary, skill requirement, trust boundary,
  prohibited actions, starting page, and generic result requirement in the
  stable instructions.
- Renamed the runner helper to `_load_instructions()` and pointed it to the new
  file without changing session lifecycle or task-message behavior.
- Added a focused test proving the generic instructions load and contain no
  initial-outreach direction.
- The 5 focused runner tests passed.
- The complete local suite passed: 66 tests, 0 failures.
- A search confirmed the removed instruction filename and old loader name no
  longer appear. The outreach-specific task-message text remains intentionally
  unchanged until Task 8.

### Task 7: Copy and register the skill in each disposable workspace

- [x] Copy the repository's `sasha-sales` skill into
  `/workspace/capabilities/sasha-sales` during sandbox preparation.
- [x] Add `/workspace/capabilities` to
  `environment.capability_directories` when creating the Agents API session.
- [x] Ensure the capability directory exists before the executor connects.
- [x] Add a test that inspects the session-creation payload.
- [x] Add a test that confirms `SKILL.md` and both reference files are copied.
- [x] Keep the copied skill in the run workspace as evidence of the exact rules
  available to Astra for that run.

Official API basis:

- Agents API self-hosted environments accept `capability_directories`.
- The directories must exist inside the sandbox before skill discovery.

Done when:

- Astra can discover the skill through the managed harness.
- The task prompt does not paste the full Playbook and Workplace into every
  invocation.

Implementation record, 2026-09-22:

- Sandbox preparation now creates `/workspace/capabilities` and copies the
  repository's complete `sasha-sales` directory beneath it before session
  creation and executor connection.
- Session creation now registers `/workspace/capabilities` through
  `environment.capability_directories`.
- The copied skill remains in the run workspace after browser-profile cleanup,
  preserving the exact `SKILL.md`, Playbook, and Workplace used for that run.
- Added one sandbox test for the three copied files and one runner test for the
  session-creation payload.
- The 9 focused sandbox and runner tests passed.
- The complete local suite passed: 68 tests, 0 failures.

### Task 8: Build one generic task message

- [x] Create one task-message builder.
- [x] For `client_message is None`, say this is initial outreach.
- [x] For a supplied message, say this is a client-response turn and include
  the exact message in a clearly delimited data block.
- [x] Include the deal ID and exact deal URL in both cases.
- [x] Explicitly tell Astra to use the `sasha-sales` skill.
- [x] Tell Astra to start at the deal page and determine the required work.
- [x] Require screenshots and visited URLs as artifacts.
- [x] Require the browser to close before the final result.
- [x] Do not mention quotation, catalog, stock, proof, or revision routes in the
  generic client-response task message.

Done when:

- Python distinguishes only outreach from client response.
- The client-response prompt does not tell Astra which page to open after the
  deal page.
- Astra must infer the quotation route from the message and skill.

Implementation record, 2026-09-22:

- Replaced the outreach-only task text with one generic task-message builder.
- The only Python mode decision is whether `client_message` is `None`.
- Initial outreach is named explicitly when no message is supplied. A supplied
  client message is preserved inside a clearly marked untrusted-data block.
- Both modes include the exact deal ID and URL, require the `sasha-sales` skill,
  start at the deal, retain browser artifacts, close the browser, and return the
  generic Sasha result.
- The client-response message contains no hard-coded route or downstream page.
- Added focused tests for both message modes and the absence of routing terms.
- The 8 focused runner tests passed.
- The complete local suite passed: 70 tests, 0 failures.

### Task 9: Replace the outreach-only CLI with one Sasha CLI

- [x] Add one generic entry point named for Sasha rather than outreach.
- [x] Keep the positional QA deal ID.
- [x] Add optional `--message` for the inbound client response.
- [x] Keep `--verbose`.
- [x] Keep `--pricing`.
- [x] Print only result JSON to stdout.
- [x] Keep progress, pricing, and artifact path on stderr.
- [x] Remove the outreach-only CLI after documentation and tests use the new
  command.
- [x] Do not add a compatibility wrapper during this POC.

Proposed commands:

```bash
# Initial outreach
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID --verbose

# Client response
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID \
  --message "What's the price for 40?" \
  --verbose \
  --pricing
```

Done when:

- Both commands call the same runner class.
- There is no second outreach runner or client-response runner.

Implementation record, 2026-09-22:

- Replaced `run_openai_managed_outreach.py` with the single generic entry point
  `run_openai_managed_sasha.py`; no compatibility wrapper was left behind.
- Kept the QA deal ID positional and added optional `--message` without
  interpreting or changing the client's text.
- Both initial outreach and client response create `SashaTask` and call the same
  `OpenAIManagedRunner`.
- Preserved `--verbose`, `--pricing`, result JSON on stdout, and progress,
  pricing, and artifact paths on stderr.
- Updated the current README commands and added focused argument-parsing tests
  for both modes.
- Added a no-network CLI execution test proving the inbound message reaches
  `SashaTask`, stdout remains valid result JSON, and diagnostics remain on
  stderr.
- The 3 focused CLI tests passed, and the new CLI help command exited
  successfully with all four arguments.
- The complete local suite passed: 73 tests, 0 failures.

### Task 10: Preserve outreach with local tests

- [x] Update the existing fake-client tests to use `OpenAIManagedRunner`.
- [x] Test that outreach creates a task with `client_message=None`.
- [x] Test that the outreach task message still invokes initial-outreach rules.
- [x] Test successful structured output parsing.
- [x] Test failed turn handling.
- [x] Test connection timeout and turn timeout behavior.
- [x] Test cleanup after success and failure.
- [x] Test verbose progress remains optional.
- [x] Test pricing remains optional and best effort.
- [x] Run the complete local suite.

Done when:

- All local tests pass.
- No OpenAI request, Docker container, or Fresh Prints QA page is needed for
  these tests.
- Existing outreach behavior is preserved through the generic runner.

Implementation record, 2026-09-22:

- Kept one fake-session and fake-sandbox harness around `OpenAIManagedRunner`
  and made its event stream configurable for success, failure, and timeout
  cases.
- Confirmed an outreach task has `client_message=None`, invokes the initial
  outreach skill path, and parses successful structured output.
- Added coverage for a failed turn, connection timeout, turn timeout and cancel,
  and cleanup after success and failure.
- Confirmed progress output and pricing are optional. Confirmed a usage lookup
  failure reports pricing as unavailable without failing a completed turn.
- The 19 focused managed runner, task contract, and CLI tests passed locally.
- The complete local suite passed: 78 tests, 0 failures.
- These tests used only local fakes; no OpenAI request, Docker container, or
  Fresh Prints QA page was used.

### Task 11: Add focused tests for autonomous client-response input

- [x] Test that `--message` reaches `SashaTask.client_message` unchanged.
- [x] Test that the client message is delimited as untrusted input data.
- [x] Test that the client-response task prompt says to start from the deal.
- [x] Test that the prompt requires the Sasha skill.
- [x] Test that the prompt contains no hard-coded quotation route.
- [x] Test that result JSON uses the same `SashaResult` shape as outreach.
- [x] Search the generic runner for intent-routing terms and inspect any match.

Suggested inspection command:

```bash
rg -n "quotation|quote|catalog|alternative|stock|proof|revision" \
  openai_managed/runner.py openai_managed/task.py
```

A match is acceptable only for a generic field, artifact name, or error message;
it must not dispatch the request.

Done when:

- The application passes the client's words to Astra without deciding their
  intent.

Implementation record, 2026-09-22:

- The generic CLI test confirms `--message` reaches
  `SashaTask.client_message` unchanged.
- The generic task-message test confirms the message is delimited as untrusted
  data, the browser starts at the deal, the Sasha skill is required, and no
  quotation route is embedded in Python.
- Added an explicit test confirming outreach and client response return the same
  five-field `SashaResult` shape required by the shared JSON schema.
- The required routing-term search returned no matches in
  `openai_managed/runner.py` or `openai_managed/task.py`.
- The 20 focused CLI, runner, and task-contract tests passed.
- The complete local suite passed: 79 tests, 0 failures.

### Task 12: Prepare the first quotation QA scenario

- [x] Obtain an explicitly authorized Fresh Prints QA deal ID.
- [x] Use a deal with one clear, existing proof.
- [x] Use a proof whose quantity and price controls are available.
- [x] Confirm no production data is involved.
- [x] Use the client message `What's the price for 40?` unless the selected QA
  deal requires a different quantity to remain within its product minimum.
- [x] Record the chosen deal ID and message in the implementation notes before
  running.

Done when:

- The input is concrete, authorized, and reproducible.

Implementation record, 2026-09-22:

- Selected QA deal `303896`, which the user explicitly supplied earlier and
  authorized for read-only OpenAI testing.
- Existing local run evidence records the QA URL
  `https://v4-qa.internal-fp.com/dashboard/sales-pipeline/deal?id=303896`.
- The deal artifact shows exactly one linked proof, `576496`, with `Proof Done`
  status.
- The proof artifact identifies a Comfort Colors C1717 item and its screenshot
  shows an editable quantity input in the Price panel, current price
  placeholders, and an order minimum of 12.
- Selected client message: `What's the price for 40?`. Quantity 40 is above the
  displayed minimum of 12.
- No API request, live browser session, or QA mutation was performed while
  preparing the scenario; Task 12 used existing local read-only artifacts.

### Task 13: Run and inspect the quotation scenario

- [x] Run the generic CLI with `--message`, `--verbose`, and `--pricing`.
- [x] Confirm verbose events show Astra's tool steps.
- [x] Inspect `session-items.json` and `session-events.json`.
- [x] Inspect `visited_urls.json` and screenshots.
- [x] Confirm Astra started from the deal page.
- [x] Confirm Astra found the proof from deal context.
- [x] Confirm Astra chose the proof quotation workflow without Python routing.
- [x] Confirm Astra entered quantity 40 only to calculate the price.
- [x] Confirm Astra read the recalculated price and cancelled instead of saving.
- [x] Confirm the final HTML answers the price question and follows Sasha's
  writing rules.
- [x] Confirm no message was sent.
- [x] Record the pricing estimate when OpenAI returns usage.

This inspection is manual for the POC. Do not add semantic validation or a
before/after CRM checker to automate it.

Done when:

- The run artifacts show the autonomous route and browser loop.
- The returned message contains the observed price.
- No persistent CRM change was intentionally made.

Implementation record, 2026-09-22:

- Ran the generic CLI for QA deal `303896` with client message
  `What's the price for 40?`, `--verbose`, and `--pricing` under the user's
  explicit authorization for the acknowledged reversible-write risk.
- The first sandboxed attempt failed with an Agents API connection error before
  browser execution. The authorized network-enabled retry completed.
- Verbose output showed Astra invoke `sasha-sales`, inspect the deal, recover
  from an initial command failure, and continue through the browser task.
- `visited_urls.json` contains only the supplied deal URL followed by proof
  `576496`. The browser transcript shows Astra discovered and clicked that proof
  link from the deal page.
- The transcript and `quote_40.png` show quantity 40, unit price `$14.66`, item
  total `$586.40`, sales tax `TBD`, and free standard shipping.
- Astra clicked `Cancel`, did not click `Save Price`, and verified the quantity
  returned to `0`; `proof_cancelled.png` records the cancelled state.
- The final HTML directly answers the price question, describes the total as
  before tax, asks one next-step question, and signs off as Sasha.
- No message-send action appeared in the command transcript or visited pages.
  Browser cleanup completed and removed the disposable profile.
- The Agents API marked the turn completed. Its interactive Node command item
  was reported as incomplete because the REPL remained interactive, but the
  command transcript contains the completed quote, cancel, screenshot, URL-save,
  and browser-close operations.
- OpenAI returned no token usage, so `pricing.json` records pricing status as
  `unavailable` with no estimated cost.
- Successful run artifacts:
  `runs/openai-managed/sasha-303896-0132f288-409f6cd9/`.

### Task 14: Update project documentation

- [x] Replace the outreach-only managed POC description in `README.md` with the
  single-runner model.
- [x] Document both CLI examples.
- [x] Document the skill directory and the difference between agent
  instructions, Playbook, and Workplace.
- [x] State explicitly that Astra chooses client-response routes.
- [x] State that the POC generates messages but does not send them.
- [x] State that only the quotation client-response scenario has been tested at
  this phase.
- [x] Update the existing managed architecture document so it no longer depicts
  outreach as a separate runner.

Done when:

- A developer can understand and run both modes without reading Python first.

Implementation record, 2026-09-22:

- Rewrote the managed POC section in `README.md` around one runner, one task
  contract, one result contract, and two CLI input modes.
- Added the managed architecture flow, required environment values, both CLI
  examples, output channels, artifacts, and the current tested boundary.
- Documented the distinct roles of stable agent instructions, the skill entry
  point, Playbook sales judgment, and Workplace UI facts.
- Stated explicitly that Astra chooses client-response workflows and that the
  POC returns but does not send the generated message.
- Replaced the outreach-specific architecture document with
  `SASHA02_openai_managed_sasha_architecture.md`, covering the generic managed
  loop, component ownership, instruction layers, artifacts, and tested scope.

### Task 15: Final verification and commit

- [x] Run the complete local test suite one final time.
- [x] Run both CLI `--help` and a no-network argument-parsing check.
- [x] Review `git diff --check`.
- [x] Review `git status --short`.
- [x] Confirm generated run artifacts, browser profiles, and secrets are not
  staged.
- [x] Confirm the outreach-specific runner, types, rules, and CLI have been
  removed rather than left as a second implementation.
- [x] Commit the completed implementation only after the checks pass.

Suggested commit message:

```text
feat: add generic managed Sasha turns
```

Done when:

- The commit contains one runner, one task contract, one result contract, one
  skill, updated tests, and updated documentation.

Implementation record, 2026-09-22:

- Ran all 79 local tests successfully.
- Confirmed the generic CLI help and all three no-network CLI tests pass.
- Confirmed `git diff --check` passes and the working tree was clean before
  recording this task.
- Confirmed no run artifacts, authenticated browser profiles, or `.env` files
  are tracked or staged.
- Confirmed the current implementation and documentation contain no references
  to the removed outreach runner, outreach task/result types, outreach rules,
  or outreach CLI.
- Confirmed the generic implementation has one `OpenAIManagedRunner`, one
  `SashaTask`, one `SashaResult`, and one `sasha-sales` skill.

## Phase acceptance checklist

The phase is complete only when all of these are true:

- [x] Initial outreach still works through the generic runner.
- [x] A client message reaches the same runner through `--message`.
- [x] Astra discovers and uses the Sasha skill.
- [x] Astra starts from the deal page.
- [x] Astra independently chooses the quotation path.
- [x] Python contains no quotation intent router.
- [x] The proof price is calculated and returned without intentionally saving.
- [x] The generated HTML is returned but not sent.
- [x] Verbose output, artifacts, pricing, timeouts, and cleanup still work.
- [x] All local tests pass.
- [x] The live run uses an explicitly supplied QA deal ID.

## What comes after this phase

### Task 16: Internally managed per-deal conversation context

The CLI must continue to accept only the deal ID and optional current client
message. The user will not provide a conversation JSON filename or path.

- [x] Add one internal conversation JSON file per deal under the managed runs
  directory, using a safe filename derived from the deal ID.
- [x] Create the deal file automatically when it does not exist.
- [x] Load the deal's previous `conversation_history` before creating the Astra
  task message.
- [x] Keep previous conversation history separate from the current client
  message and label both as data, not instructions.
- [x] After a completed run, append the current client message when present and
  append Sasha's exact `message_html`.
- [x] Record the POC response with `delivery_status: generated`; do not claim it
  was sent because this runner does not deliver messages.
- [x] Do not append a Sasha conversation entry when the run fails.
- [x] Preserve the complete ordered history so follow-up references such as
  “the second option” can be resolved from Sasha's earlier response.
- [x] Write updates atomically so an interrupted write cannot leave partial
  JSON.
- [x] Keep the conversation files out of Git with the existing ignored `runs/`
  directory.
- [x] Show the internally selected conversation file in verbose output for
  debugging, without adding a CLI path argument.
- [x] Add tests for automatic creation, same-deal continuity, different-deal
  isolation, completed-run appends, failed-run behavior, and malformed stored
  JSON.
- [x] Run the complete local test suite and one two-turn QA scenario before
  marking the task complete.

The internal POC shape is:

```json
{
  "deal_id": "303896",
  "conversation_history": [
    {
      "role": "client",
      "content_type": "text",
      "content": "Can you suggest some green polos?"
    },
    {
      "role": "sasha",
      "content_type": "text/html",
      "content": "<ol>...</ol>",
      "delivery_status": "generated"
    }
  ]
}
```

Done when:

- Running the CLI twice with the same deal ID automatically gives the second
  Astra turn the first turn's exact client message and Sasha response.
- Running a different deal ID uses a separate history without requiring any
  filename or path from the user.

Implementation record, 2026-09-22:

- Added `ConversationStore`, which derives
  `runs/openai-managed/conversations/DEAL<deal_id>_conversation.json`, creates
  missing files, loads ordered history, and replaces files atomically.
- Added previous conversation history to `SashaTask`, the saved task artifact,
  and the Astra task message as a separate untrusted-data section.
- A completed turn appends the current client message and exact Sasha HTML with
  `delivery_status: generated`. Failed turns leave prior history unchanged.
- Added local coverage for creation, safe deal filenames, deal isolation,
  two-turn continuity, completed and failed runs, malformed JSON, and verbose
  path reporting. All 85 local tests passed.
- Ran two fresh managed sessions for authorized QA deal `303896`. The first
  generated a 40-piece quote for a Comfort Colors C1717 tee in Blue Jean at
  $14.66 each and $586.40 before tax. The second received that exact response
  through the internal JSON and repeated the same product, color, quantity,
  unit price, and total without saving changes.

After conversation continuity passes, keep the same runner and task contract.
Add and test scenarios by improving the Sasha skill and Workplace knowledge in
this order:

1. Product suggestions and alternatives using catalog plus quoter.
2. Stock and shipping questions.
3. Proof creation from exact client-supplied text.
4. Attachment handling and proof creation from artwork.
5. Revision submission and saved-state verification.

New application code is justified only when a scenario requires a capability
that the current executor does not possess. A new client intent by itself does
not justify a new runner or Python route.

## Evidence used for this plan

- The current runner creates one self-hosted session, connects the executor,
  collects results, and performs cleanup in
  `openai_managed/runner.py:190-290`.
- The current runner hard-codes outreach rules and task wording in
  `openai_managed/runner.py:316-336`.
- The current sandbox copies the authenticated browser profile and creates the
  disposable workspace in `openai_managed/sandbox.py:44-85`.
- Existing quotation policy is in `prompts/playbook.md:74-86`.
- Existing deal, proof, catalog, quoter, and stock page facts are in
  `prompts/workplace.md:6-19`.
- OpenAI documents the managed harness, application server, and self-hosted
  environment responsibilities in the
  [Agents API architecture guide](https://developers.openai.com/api/docs/guides/agents-api/architecture).
- OpenAI documents Agents API skill discovery through
  `environment.capability_directories` in the
  [Skills guide](https://developers.openai.com/api/docs/guides/tools-skills#agents-api).
- The current Agents API reference lists `capability_directories` on
  self-hosted environment parameters in the
  [Agents API reference](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/agents).
