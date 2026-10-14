# OpenAI-managed Account Manager POC

Account Manager handles one Fresh Prints sales turn through an OpenAI managed agent
session, using Sasha as its client-facing persona. It chooses outreach, client response,
or no reply from local conversation history and incoming client messages.

The POC generates a client-facing HTML message. It does not send the message,
purchase anything, or delete CRM records.

## How it works

```text
deal ID + optional client message
              |
              v
scripts/run_openai_managed_sasha.py
              |
              v
OpenAIManagedRunner
              |
              +--> sign in to the configured Fresh Prints environment
              +--> start a disposable Docker browser environment
              +--> keep one signed-in browser open for the whole turn
              +--> connect the OpenAI managed agent session
              +--> collect structured result JSON and artifacts
```

Every turn uses a new container and an empty browser profile. The runner signs
in before the agent starts, passes credentials to the fixed login script over
standard input, and removes the container when the turn ends.

After sign-in, the runner starts one headless Chrome on the signed-in profile
(`openai_managed/start_browser.js`) and keeps it running for the whole turn.
Sasha's Playwright scripts attach to it with
`chromium.connectOverCDP('http://127.0.0.1:9222')` instead of launching Chrome,
so page state carries over between commands. The port exists only inside the
disposable container. If attaching fails, Sasha runs the same start script to
restart the browser.

## Requirements

- Python 3.12 or newer
- Docker
- An OpenAI application API key
- An OpenAI executor API key
- Fresh Prints credentials for the configured environment

## Setup

Create a virtual environment and install the Python dependencies:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create the local configuration:

```bash
cp .env.example .env
```

Fill in the required values in `.env`. One deployment uses one environment;
changing environments requires updating configuration and restarting the app.
Skills use the resolved destinations supplied with each task, so the same skills
work across environments without editing Markdown.

| Required setting | Value to supply |
|---|---|
| `FP_ENVIRONMENT` | Stable environment name, such as `qa` or `production`; lowercase letters, digits, underscores, and hyphens |
| `FP_BASE_URL` | CRM base URL, used to build the deal URL |
| `FP_LOGIN_URL` | Full CRM login URL |
| `FP_CATALOG_URL` | Full product catalog entry URL |
| `FP_DESIGNS_URL` | Full designs gallery entry URL |
| `FP_DESIGN_TOOL_URL` | Full Design Tool entry URL |
| `FP_HELP_CENTER_URL` | Full help center entry URL |
| `FP_USER`, `FP_PASSWORD` | Credentials for this environment |

Supply actual deployment URLs; Sasha does not derive hostnames from the environment
name. There are no default application URLs. Missing or invalid destinations fail
configuration before browser work. Application links must match the configured
application origins, and a deal URL from another CRM origin is rejected.
`runtime.json` records the resolved application destinations without login credentials.

The environment name is appended to the runner, web jobs, and web deals directory
settings. Notes and conversations for the same deal ID are therefore kept separate
across environments. When upgrading an existing deployment, copy its old `notes/`
and `conversations/` directories into `<OPENAI_MANAGED_RUNS_DIRECTORY>/<FP_ENVIRONMENT>/`.
Copy old web job and deal JSON files into the environment subdirectory of their
respective configured directories. Keep the original run artifact directories:
historical jobs still reference them. Do not copy data between environments.

Then build the disposable executor image:

```bash
docker build -t sasha-openai-managed:local .
```

## Run

Run Account Manager (an empty local conversation produces initial outreach):

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID --verbose
```

Handle a client message:

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID \
  --message "What's the price for 40?" \
  --verbose \
  --pricing
```

Add direct artwork URLs with repeatable `--file` arguments:

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID \
  --message "Put this logo on the back." \
  --file "https://example.com/logo.png"
```

The command writes Sasha's structured result JSON to stdout. Progress, timing,
and optional pricing go to stderr.

## Web app

Start the web server with one worker:

```bash
.venv/bin/uvicorn webapp.app:app --host 0.0.0.0 --port 8100 --workers 1
```

Open `http://localhost:8100`. Team members on the same reachable network can use
the host machine's address instead of `localhost`.

The app intentionally has no authentication. Its home page lists every saved
deal. Adding a deal creates an empty `/deals/<deal-id>` page where a user can
start a run. Each deal page lists all of that deal's previous runs, and each run
has a stable `/runs/<run-id>` URL.

Runs are queued through one background worker so only one Sasha turn runs at a
time. Refreshing or reopening a deal or run URL restores the same view without
stopping an active run.

Deal records and web job state are written under `runs/webapp/deals/<environment>/` and
`runs/webapp/jobs/<environment>/`. Existing job records are automatically added to the deal
list. If the web server restarts, completed jobs remain viewable. A job that was
queued or running when the server stopped is marked failed because its original
background worker no longer exists.

### Two sections

- **Deals** (`/`): start Account Manager on a deal you already know. The deal is checked
  first, like step 3 below. A run with a client message uses the client-response rules; a
  run without one uses Sasha v3's outreach rules: its duplicate guard (other active deals
  for the client with the same title and proof set are Zap duplicates; the one Sasha wrote
  to last survives, else the oldest created within `DUPLICATE_DEAL_RECENCY_DAYS`, default
  45, else this deal, and the rest are marked lost), then consolidation, and outreach is
  skipped if the kept deal already had Sasha outreach. If the check stops or skips the run,
  the form says why and nothing starts; otherwise the run page shows the check, and the run
  may be on the kept deal rather than the one you opened.
- **Client response without deal ID** (`/client-response`): paste a client email or SMS.
  The same flow runs for the webhook `POST /api/inbound`, which another service calls
  for every inbound message.

Client response without deal ID, in order:

1. **Find deal by email or phone** (`deal_manager_agent/deal_lookup.py`, no LLM). The
   sender's user record -> its deals that are not deleted, not `won`/`lost` and managed
   by Sasha (`SASHA_USER_ID`, default 297140). One deal -> use it.
   Several -> the one Sasha last wrote to.
2. **Deal Manager** runs only when step 1 finds no single deal, and reads the message to
   find the deal. Only a `matched` decision continues.
3. **Validate and consolidate** (`deal_manager_agent/deal_resolution.py`), ported from Sasha
   v3 `deal_resolution` for CLIENT_RESPONSE. The deal must be active for its client; a
   `won` deal still proceeds; anything else stops for a person. If the client has several
   active deals, the kept deal is the one Sasha last wrote to (else the found deal); the
   other deals' proofs are linked onto it and the other deals are **marked lost in the
   CRM** through the v3 edit-deal API, then Account Manager runs on the kept deal.
   These CRM writes need `V3_API_BASE_URL`, `JWT_AUTH_KEY` and `SASHA_ADMIN_USER` (the
   same values Sasha v3 uses). Without them a client with several active deals stops with
   `crm_writes_not_configured` and nothing is written. Not ported: v3's Sasha-database
   side effects of marking a deal lost (stopping its follow-up schedule and updating its
   deal context) and its Slack alerts.
4. **Account Manager** runs on the deal with the message as the client message.

**More messages while it runs.** Like additional messages on a run, a client can send
more while the flow is open: `POST /api/inbound/{inbound_id}/messages` with `{"text": "..."}`,
or **Send additional message** on the page. Until Account Manager starts, messages are
held and sent with the first message. After it starts, each one goes to the running
Account Manager as an additional message, retried until the run accepts messages. Once the
flow is handed off or the run has finished, new messages are refused (409); send them as a
new client response. The webhook does this by itself: an SMS from the same phone, or an
email on the same thread, joins the open conversation (`"attached": true`) instead of
starting a second flow.

Webhook payload: `email`, `subject` and `body` (a string) for email; `phone` and `body`
(an array of messages in order) for SMS. `thread_id` and `message_id` are optional; a
repeated `message_id` returns the existing record. Poll `GET /api/inbound/{inbound_id}`.
Database settings may use the `NON_PROD_CRM_*` / `NON_PROD_SASHA_DB` names; the host
maps them for the lookup, and the agent container receives resolved values.

## Time limit

Sasha may work for up to 20 minutes per managed turn. Change
`OPENAI_MANAGED_TURN_TIMEOUT_SECONDS` if a different limit is required.
Container preparation, Fresh Prints authentication, artifact collection, and cleanup are
outside that managed-turn limit.

## Output

Run artifacts are stored under `runs/openai-managed/<environment>/`. Each completed turn also
updates the deal conversation at:

```text
runs/openai-managed/<environment>/conversations/DEAL<deal_id>_conversation.json
```

Downloaded client artwork is removed after the turn.

## Deal notes

Each deal has one shared plain-English Markdown notebook:

```text
runs/openai-managed/<environment>/notes/deal_<deal_id>_notes.md
```

Existing notebooks with the old `SASHANOTES01_deal_<deal_id>_notes.md` name remain
readable when no notebook with the new name exists. Successful updates write
`deal_<deal_id>_notes.md`, which takes precedence over the old file. The temporary
agent output is `notes_update.md`. Historical run artifacts keep their original names.

The location is `<OPENAI_MANAGED_RUNS_DIRECTORY>/<FP_ENVIRONMENT>/notes/`. Each run
receives the current notebook alongside the full conversation history. Sasha
updates explicitly stated client preferences, current choices and saved state,
actual client requests that remain incomplete, and uncertainties relevant to the
current request. Each bullet is one short factual statement, normally up to 20
words. Notes describe the deal; behavioral instructions, generic checks, and reply
wording belong in the selected workflow skill. Unknowns do not automatically become tasks, and a
client considering options does not create a follow-up obligation.
Notes omit source appendices, quotations, task IDs, activity history, and resolved
technical details; conversation and run logs retain the evidence. A short proof
ID can identify the relevant record. Existing verbose notes are shortened during
their next successful update. Keep a fact only when forgetting it could cause a
misunderstanding or an incorrect action, including facts needed for active
comparisons. Notes are not a reply checklist: Sasha answers the current request,
keeps irrelevant unresolved details internal, and explains relevant uncertainty
in everyday language rather than copying shorthand such as "TBD."
Corrections replace old decisions, completed work leaves the pending list, and
changes to an order require dependent quotes to be checked again. Existing
workflow rules still determine when to check live prices, stock, dates, and links.
Drafted messages remain drafts; notes do not send messages or schedule follow-ups.

An `Attachments` section keeps the original client-provided URLs as one-line
filename/link entries. The runner preserves these references when it updates the
notebook, even if Sasha leaves them out of its revision. It ignores attachment
URLs invented in the revision. Later turns download saved attachments again and
provide fresh local upload paths, so clients do not need to resend working links.
Newly supplied files and restored files are labeled separately. Exact duplicate
URLs are downloaded once per turn. Downloads are still removed after each turn.
If a saved link expires or cannot be downloaded, the run continues with a warning;
Sasha asks for a fresh link only when that artwork is needed. Original URLs,
including signed query strings, remain intact in the local notebook.

The runner validates Sasha's temporary revision and replaces the notebook only
after a successful turn and conversation save. It removes the temporary output
afterward. There are no dedicated notes snapshots or archived notebook versions.
Existing task and session logs still record the inputs and tool activity as usual.

Missing notes start empty. Unreadable existing notes are preserved and disable
notes updates for that run. Missing, empty, invalid UTF-8, non-regular, or larger
than 16 KiB revisions produce a progress warning and leave previous notes intact;
they do not discard an otherwise successful sales draft. This checks file
integrity, not whether every note is factually correct.

The POC expects runs for the same deal to be sequential. It creates no lock files
and does not reject overlapping runs. Notes and conversation files are still
replaced atomically, but overlapping runs can overwrite each other's updates.

To check model behavior manually on a QA deal, run a conversation that introduces
a navy preference and an estimated quantity, then changes the preference to black
and confirms the quantity. Inspect the single notebook after each run: resolved
questions should disappear, previous quotes should not be reused for a changed
configuration, current stock should still require verification, and generated
replies should never be recorded as delivered. Use an isolated runs directory for
test conversations and explicitly request no saved QA changes when only testing
notes. Automated tests use simulated agent output and do not prove these semantic
behaviors.

## OpenAI-managed source

- `openai_managed/runner.py`: session lifecycle and result collection
- `openai_managed/sandbox.py`: disposable Docker environment
- `openai_managed/setup_auth.js`: Fresh Prints authentication inside the container
- `openai_managed/conversation.py`: per-deal conversation history
- `openai_managed/notes.py`: shared Markdown notebooks
- `openai_managed/attachments.py`: temporary client artwork downloads
- `openai_managed/agent_instructions.md`: stable agent boundaries
- `openai_managed/capabilities/account-manager/`: conversation routing, shared browser, evidence, verification, and formatting rules
- `openai_managed/capabilities/outreach/SKILL.md`: outreach inspection and action rules
- `openai_managed/capabilities/client-response-orchestrator/SKILL.md`: client-response strategy and action rules
- `scripts/run_openai_managed_sasha.py`: CLI entry point

## Send an Additional Message during a run

Start an **Account Manager** run in the web console. Open its run page and wait
until **Send Additional Message** becomes available. Enter the correction or
additional request and send it. This first version accepts text only.

The web backend keeps an in-memory mapping from run IDs to active runners. The
runner sends each additional message to the existing OpenAI session; the same container
and browser stay connected. An active turn receives steering. If it was preparing
outreach or a follow-up, Account Manager reconsiders its action and handles the new client request. A message arriving
as that turn ends can start another turn in that same session. Completion checks
use saved user-message items, their turn IDs, and item order before selecting the final answer.
If an accepted message was attached after an answer on the same completed turn,
the runner continues it once in that session and waits for an answer after that message.

The message list shows pending, sending, accepted, failed, or uncertain delivery.
**Accepted** confirms API acceptance, not that Sasha interpreted the correction
correctly. Inspect the final answer and `session-items.json` for that check. Drafts
and submission IDs survive a refresh in the same browser tab. Retrying the same
submission does not enqueue another copy. New submissions are rejected once the
runner starts finishing; keep that message for a new run.

For a manual QA check, ask for navy options for 40 shirts, then send “Actually,
black, and quantity 60” while Sasha is working. Check the final answer, ordered
client messages in the saved conversation, and session events. Use requests that
do not authorize saved deal/proof changes for this test.

If OpenAI cannot steer the active turn, the runner holds the message until that
turn finishes. An uncertain delivery fails the run and preserves the submission
record for investigation. The run timeout includes additional messages. A server restart
does not resume active runners: unfinished jobs are marked failed and pending
or sending messages become uncertain. Keep running the web server with one worker.

The endpoint is `POST /api/runs/{run_id}/messages` with
`{"submission_id": "unique-message-id", "text": "New client message"}`.
The run status endpoint includes `messages` and `accepting_messages`. The session
ID stays inside the backend. Real inbound email routing is not connected here.

## Account Manager decisions and skills

Callers supply a deal ID, an optional client message, and optional artwork URLs.
There is no flow selector. The agent chooses its action; Python does not route by
message presence. `--workflow` and API `workflow` fields are rejected. For example:

```json
{
  "deal_id": "303992",
  "client_message": "What's the price for 40?",
  "file_urls": []
}
```

For this version, only the supplied local conversation and incoming client messages
establish conversation state. CRM activity supplies deal facts, not routing history.
Database-backed conversations and automatic handoff from Deal Manager are not connected.

| Local conversation and incoming messages | Action |
|---|---|
| Empty history and no incoming message | Initial outreach |
| First incoming client message, even with empty history | Client response |
| Existing history with an unanswered client request | Client response |
| Saved Sasha draft and no new request | No reply |
| Client acknowledgement with nothing outstanding | No reply |
| Incoming client message during outreach | Reconsider in the same session |

Blank incoming messages are normalized to absent; the agent ignores blank historical
entries. A saved nonblank Sasha draft counts as handled for this local simulation,
but remains marked `generated`, never sent. Ordinary runs never start proactive follow-ups. Explicit follow-up checks are described below.
A successful no-reply result has `status: "completed"` and `message_html: ""`;
the console displays **No reply needed**. Client messages are still saved after a
successful no-reply turn, without appending an empty Sasha entry. Failed runs retain
the existing behavior: they do not append a completed conversation turn.

Every run copies all eight skills:

- [Account Manager](openai_managed/capabilities/account-manager/SKILL.md) owns action selection, no-reply rules, and shared browser/evidence boundaries.
- [Outreach](openai_managed/capabilities/outreach/SKILL.md) owns read-only initial outreach inspection.
- [Client response](openai_managed/capabilities/client-response-orchestrator/SKILL.md) owns sales actions for client requests.

- Follow-up stages [1](openai_managed/capabilities/followup-stage-1/SKILL.md), [2](openai_managed/capabilities/followup-stage-2/SKILL.md), [3](openai_managed/capabilities/followup-stage-3/SKILL.md), [4](openai_managed/capabilities/followup-stage-4/SKILL.md), and [5](openai_managed/capabilities/followup-stage-5/SKILL.md) provide tactic pools and tone rules.

Account Manager's routing and no-reply rules govern the specialized skills.
All actions use [shared sales rules](openai_managed/capabilities/account-manager/references/sales_rules.md), including pricing, proofs, stock, delivery, and samples.
Before drafting, the agent reads [shared crafting](openai_managed/capabilities/account-manager/references/shared_crafting.md)
and the relevant [outreach](openai_managed/capabilities/outreach/references/outreach_crafting.md)
or [client response](openai_managed/capabilities/client-response-orchestrator/references/client_response_crafting.md)
crafting guide. The [Workplace reference](openai_managed/capabilities/account-manager/references/workplace.md)
describes application pages and controls. Sasha remains the client-facing name.

Restart the web app after deploying the Python and UI changes. Skill edits apply to
newly prepared containers; active runs retain their copies. Historical jobs remain
readable, including their old workflow labels. Existing conversation files, notes,
attachments, environment directories, CLI script paths, and Docker image names are
unchanged; no data migration is needed.

For behavioral acceptance, use isolated local history directories and read-only
requests to exercise every row above, including CRM history that differs from the
local history. Mocked tests verify plumbing, not the model's routing decisions.
Report real-agent checks separately.

## Explicit follow-up checks

Open a deal and select **Follow-up check**. Choose a stage and a business date;
the date defaults to the browser's local calendar date. Days since the client's
reply are optional context. The selected stage remains authoritative, even when
the elapsed days suggest another stage. The notes sidebar shows the existing
notebook from the current environment's runs directory.

The same entry point is available through `POST /api/runs`:

```json
{
  "deal_id": "303992",
  "turn_type": "follow_up",
  "follow_up_stage": 2,
  "as_of_date": "2026-10-13",
  "days_since_client_reply": 5
}
```

Stage must be an integer from 1 to 5; date must use `YYYY-MM-DD`; optional days
must be a nonnegative integer. Initial follow-up checks cannot include client
messages or new artwork. Additional client messages remain available during the
active run. Ordinary requests omit follow-up fields. `turn_type: "outreach"`,
`turn_type: "response"`, and `workflow` are rejected on new requests. The CLI
continues to start ordinary Account Manager turns.

| Explicit check state | Account Manager action |
|---|---|
| Empty local conversation | No reply |
| Unanswered local client request | Client response |
| Conversation exists, no unanswered request | Evaluate one supported unused tactic in the requested stage |
| No supported unused tactic | No reply |
| Client message arrives during follow-up work | Reconsider and handle it in the same session |

Run metadata says **requested follow-up check**, since the final action may be a
client response or no reply. Follow-up research is read-only. There is no second
agent, scheduler, automatic sending, or database conversation integration.

The notebook retains every prior `Stage N follow-up generated: tactic_id` entry.
Only a successful nonblank follow-up draft adds one entry. No-reply, failed,
ordinary, or superseded work does not add a used tactic. Historical `sent` entries
also count as used, but that wording does not prove delivery. Generated drafts
count as used in this POC. No history migration is needed.

Samples remain free and blank. Offering one without approval requires verified
value below $50 and evidence that no sample was sent within the preceding 14 days.
Exactly $50, a more recent sample, or missing evidence requires confirmation before
promising one. Internal cost and approval details stay out of client drafts.

Real-agent verification uses synthetic conversations and container-local browser
fixtures, separately from mocked unit tests. It reads only OpenAI credentials and
runtime configuration from `.env`, never deployment CRM credentials or records:

```bash
.venv/bin/python tests/FM01_synthetic_agent_checks.py
```

This opt-in check uses the configured OpenAI API and Docker image. Results and
isolated histories are saved under `runs/FM01_synthetic_followup_checks/`.
Pass case names as arguments to run a subset. Inspect the recorded drafts and
notebooks for semantic behavior as well as the harness's structural assertions.

## Tests

Run the sales and web regression suites:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test*.py' -v
.venv/bin/python -m unittest discover -s webapp/tests -p 'test*.py' -v
```

Run the browser UI test with synthetic HTTP responses (no CRM or OpenAI access):

```bash
docker run --rm --mount "type=bind,src=$PWD,dst=/repo,readonly" \
  sasha-openai-managed:local node /repo/webapp/tests/AM01_account_manager_ui.js
```

### Independent Deal Manager Agent

Open `/deal-manager` from the web app's **Deal Manager Agent** link. Supply sender email,
subject (optional), and body. The separate resolver uses only `deal-manager-database`
and `deal-manager-resolve-deal` from `deal_manager_agent/skills`. It has its own prompt,
result schema, background worker, and saved lookup history. It uses the same
configured OpenAI model and managed Codex executor technology as the sales runner.
It does not load sales skills, sign into CRM, send messages, or update deal notes.

Build its dedicated image once:

```bash
docker build -f deal_manager_agent/Dockerfile -t deal-manager-agent:local .
```

Set `NON_PROD_HOST`, `NON_PROD_PORT`, `NON_PROD_USER`, `NON_PROD_PASS`, and
`NON_PROD_SSL_CA` in the private `.env`. The last setting is an absolute path to
the [AWS RDS CA bundle](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem).
Set `DEAL_MANAGER_MAIN_SCHEMA`, `DEAL_MANAGER_OPERATIONS_SCHEMA`, and
`DEAL_MANAGER_CRM_SCHEMA` to the actual database names for this deployment.
All three are required; there are no built-in schema names. The configured login
must read those schemas. Schema values are validated before use in SQL. The agent
receives a public schema-role map, and result validation checks the configured main
schema. Table names and relationships remain application-specific; changing schema
names does not adapt a different database model automatically. Use SELECT-only grants for
deployment; the Python helper additionally uses read-only transactions and
bounded queries. Database access must work from Docker. Credentials are passed
over stdin into container tmpfs; they are not placed in the copied skills,
task prompt, image, or saved job files. Stop removes the container and its secrets.

`DEAL_MANAGER_IMAGE`, `DEAL_MANAGER_DIRECTORY`, and `DEAL_MANAGER_TIMEOUT_SECONDS`
configure the resolver. Defaults are `deal-manager-agent:local`,
`runs/deal-manager-agent`, and 600 seconds. Give different database deployments different
lookup directories to keep histories separate. Restart the server after configuration
or Python changes; skill edits apply to newly prepared containers.

`POST /api/deal-manager/lookups` accepts `{ "sender_email": "client@example.com",
"subject": "Proof question", "body": "Where is my proof?" }` and returns a queued
lookup. Poll `GET /api/deal-manager/lookups/{lookup_id}` or reopen `/deal-manager/lookups/{lookup_id}`.
`GET /api/deal-manager/lookups` lists previous lookups. A completed job may have a
`matched`, `needs_review`, `no_match`, or `error` decision. Only `matched` selects
a deal ID. Candidates, evidence, limitations and the next useful check remain
available in the structured result. A missing email timestamp is never replaced
with the time the user clicked Submit.

Lookup inputs and results are saved under the configured directory. Agent sessions
receive the email and database evidence as part of the investigation. The existing
web application's access model also applies to this section.

Run offline resolver tests:

```bash
.venv/bin/python -m unittest discover -s deal_manager_agent/tests -p 'test*.py' -v
```

The opt-in live regression script sends the four confirmed email samples through
the running web app, OpenAI, and database. It incurs API usage and transfers the
samples and retrieved evidence to the managed service. Run it only with approval
for that transfer:

```bash
.venv/bin/python deal_manager_agent/live_web_checks.py --base-url http://127.0.0.1:8100
```

Real samples, expected IDs, and historical discovery records live outside the runtime
skills. The test script sends only message inputs to the agent; expected IDs stay on
the host for evaluation. Results default to `runs/deal-manager-agent/live_checks.json`.

The tests do not make live OpenAI calls or start a real browser:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s webapp/tests -t . -v
```

Sasha operates the Design Tool through general Playwright browser actions using
written guidance in the sales skill. It observes the current page, edits the
selected text, and verifies the saved proof.

Each run saves `workspace/runtime.json` with its loaded runner version, model,
and agent instructions, plus the actual `workspace/TASK.md`. Screenshots are in
`workspace/artifacts`. Restart the webapp after Python changes; the documented
server command does not enable automatic reload. The current runner version is
`separate-writing-guides`.

Live QA smoke tests for the kept-open browser sign in to the configured Fresh Prints environment inside
the managed Docker image. They are skipped unless `SASHA_LIVE_QA=1`:

```bash
SASHA_LIVE_QA=1 .venv/bin/python -m unittest tests.test_openai_managed_browser -v
```
