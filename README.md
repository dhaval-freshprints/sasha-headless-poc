# OpenAI-managed Sasha POC

Sasha handles one Fresh Prints sales turn through an OpenAI managed agent
session. The same runner supports initial outreach and client responses.

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

Generate initial outreach:

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID --workflow outreach --verbose
```

Handle a client message:

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID \
  --workflow client-response-orchestrator \
  --message "What's the price for 40?" \
  --verbose \
  --pricing
```

Add direct artwork URLs with repeatable `--file` arguments:

```bash
.venv/bin/python scripts/run_openai_managed_sasha.py DEAL_ID \
  --workflow client-response-orchestrator \
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
- `openai_managed/capabilities/sasha-sales/`: shared browser, evidence, verification, and formatting rules
- `openai_managed/capabilities/outreach/SKILL.md`: outreach inspection and action rules
- `openai_managed/capabilities/client-response-orchestrator/SKILL.md`: client-response strategy and action rules
- `scripts/run_openai_managed_sasha.py`: CLI entry point

## Select and customize a workflow

The caller must explicitly select `outreach` or `client-response-orchestrator`.
The CLI requires `--workflow`. The web form requires a Run type selection, and
`POST /api/runs` requires a `workflow` field with one of those values. Client
response requires a nonblank `client_message`. A message never selects or
changes the workflow. Missing or invalid selections are rejected before a run.

```json
{
  "deal_id": "303992",
  "workflow": "client-response-orchestrator",
  "client_message": "What's the price for 40?",
  "file_urls": []
}
```

Edit [outreach](openai_managed/capabilities/outreach/SKILL.md) for initial outreach,
or [client response orchestrator](openai_managed/capabilities/client-response-orchestrator/SKILL.md)
for client-response strategy, suggestions, pricing, and proofs.
Each workflow owns its sales decisions. Neither reads the other workflow.

Reply crafting lives in separate reference files:

- [Shared crafting](openai_managed/capabilities/sasha-sales/references/shared_crafting.md): voice, structure, sign-off, and HTML formatting.
- [Outreach crafting](openai_managed/capabilities/outreach/references/outreach_crafting.md): introduction and outreach wording.
- [Client response crafting](openai_managed/capabilities/client-response-orchestrator/references/client_response_crafting.md): reply structure, product references, and wording for prices, delivery, and uncertainty.

Before drafting, the selected workflow explicitly loads shared crafting and its own
crafting guide. These are supporting references, not separately selected skills.
Keep evidence requirements, action permissions, pricing validity, and decisions
about whether a reply is needed in the workflow skill. Keep phrasing and
presentation in its crafting guide.

`sasha-sales` supplies shared browser, evidence, verification, and output rules;
its Workplace reference describes application pages and controls. The shared
skill does not select a workflow.

Each run copies only `sasha-sales` and its selected workflow when the sandbox is
prepared. Skill edits apply to subsequently prepared runs; existing run copies
stay unchanged. Restart an already-running webapp after this refactor to load
the required workflow field. Existing saved runs remain readable; records
without a workflow keep it unknown and are not automatically assigned one.

## Tests

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
