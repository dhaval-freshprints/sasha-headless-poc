# OpenAI-managed Account Manager POC

Account Manager handles one Fresh Prints sales turn through an OpenAI managed agent
session, using Sasha as its client-facing persona. It chooses outreach, client response,
or no reply from database conversation history and incoming client messages.

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
| `FP_HQ_BASE_URL` | Customer HQ base URL, without query parameters or fragments |
| `FP_USER`, `FP_PASSWORD` | Credentials for this environment |

Supply actual deployment URLs; Sasha does not derive hostnames from the environment
name. There are no default application URLs. Missing or invalid destinations fail
configuration before browser work. Application links must match the configured
application origins, and a deal URL from another CRM origin is rejected.
`runtime.json` records the resolved application destinations without login credentials.

The environment name is appended to run-artifact, web-job, and deal-registry directories.
Notes and conversations use the configured operations database, not those directories.
`FP_ENVIRONMENT` does not select a database: configure the matching schema and connection
explicitly. Legacy notes/conversation files are ignored; do not import them during this rollout.

Then build the disposable executor image:

```bash
docker build -t sasha-openai-managed:local .
```

## Run

Run Account Manager (an empty database conversation produces initial outreach):

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
   by Sasha (the `sasha_user_id` value in `sasha_settings`). One deal -> use it.
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

**Internal team mail.** An email from `INTERNAL_SENDER_DOMAINS` (default `freshprints.com`),
such as art@ proof replies, `[HQ] Order #…` or logistics@ forwards, takes the same flow with
three differences: step 1 is skipped (a staff address never maps to the client), so the Deal
Manager agent finds the deal from the message; step 3 only checks the deal is active or won
for Sasha and never merges deals or writes to the CRM; and it never joins an open
conversation. Account Manager recognises the team `From:` line and uses its `internal-email`
skill to confirm the update on the deal, decide whether the client needs to hear about it,
and draft the client message or return no reply.

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

Run artifacts remain under `runs/openai-managed/<environment>/`. They may contain debug
copies of prompts and results. They are never a fallback for deal notes or conversation history.
Downloaded client artwork is removed after the turn.

## Shared database storage

The `sasha_database` package owns configuration, connections, bounded read queries, and
Sasha persistence. DM and AM use the same configuration loader and container environment
allowlist. DM, AM and host persistence use only the main and operations schemas.
DM reads message evidence from `sasha_conversation_history`. The host loads `.env`; both containers receive `--env NAME` arguments with values
supplied through the Docker subprocess environment. The shared package is mounted read-only. Database connections use no TLS
and require a server/account that permits unencrypted connections. Neither container writes a private database configuration JSON file.

Configure the existing `NON_PROD_HOST`, `NON_PROD_PORT`, `NON_PROD_USER`, `NON_PROD_PASS`,
`DEAL_MANAGER_MAIN_SCHEMA`, and `DEAL_MANAGER_OPERATIONS_SCHEMA` settings, plus:

```dotenv
SASHA_SENDER_EMAIL=your-sasha-mailbox@example.com
```

The sender must be the actual outbound mailbox. Missing configuration prevents AM from
starting. Existing CRM/V3 credential aliases and `NON_PROD_SASHA_DB` remain supported.
The operations schema must already contain `sasha_deal_status` (with `status_reason`, from
`sql/20261015_0001_sasha_deal_status_handoff_reason.sql` then `sql/20261016_0001_sasha_deal_status_reason.sql`), `sasha_deal_notes`, and
`sasha_conversation_history` (column `convo_type`). Handoff offers also need v3's
`sasha_handoff_isas` and `sasha_handoff_offers` there; see [Handoff to a person](#handoff-to-a-person).
The account needs SELECT on lookup schemas and SELECT/INSERT/UPDATE on these three Sasha
tables. Agent tools still use read-only transactions; only host orchestration performs
persistence writes. Read-only transactions are a tool guard, not separate DB credentials.

Before AM starts, the host commits one `active` status row if absent and saves each incoming
message. Existing status rows are preserved. Additional messages are saved before agent
delivery. Incoming messages survive failed runs. Provider retries are checked under a
per-deal row lock, and stored row IDs prevent reinsertion at completion. SMS batches have
one provider event ID on the first message, with subsequent messages stored separately.

Email sender addresses and SMS phone numbers populate `sender_name`. Manual email input
without a sender uses the deal client's email, failing clearly if unavailable. Manual SMS
requires a sender number. Generated email drafts use `SASHA_SENDER_EMAIL` and delivery
status `generated`; this code does not send messages. Timestamps are stored in UTC.

`POST /api/runs` accepts optional `incoming_message` metadata: `body`, `sender_name`,
`message_type` (`EMAIL` or `SMS`), `subject`, `content_type`, `message_timestamp`,
`external_message_id`, `external_thread_id`, and `attachments`. Legacy `client_message` envelopes and
plain CLI `--message` inputs remain supported. Initial attachment URLs use `file_urls`.
`POST /api/inbound` also accepts `attachments`, `content_type`, and `message_timestamp`.
The additional-message endpoint accepts optional `incoming_message` with the same shape.
Subjects are limited to 500 characters; provider and thread IDs to 255. Values are not truncated.

## Deal notes

Each deal has one Markdown notebook in `sasha_deal_notes`. Every run fetches the notebook
and ordered, non-deleted history from DB. The model returns the full notebook in `notes`
and the final action in `convo_type`: `outreach`, `client_response`, `internal_mail`, or
`followup`. A no-reply success uses empty `message_html` and null `convo_type`.
There is no temporary notes-output file. Sasha
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
including signed query strings, remain intact in the database notebook.

The runner validates the structured notes revision (nonempty, no null bytes, at most
16 KiB in UTF-8), preserves attachment links and follow-up tactics, and commits notes
and the final outgoing draft in one transaction. A no-reply success commits only notes.
Database read/write errors or invalid revisions fail the run. Incoming messages remain
saved, but notes and the outgoing draft roll back together. If another run changed the
notes after this run read them, completion fails rather than overwriting the newer notes.

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

For this version, only the supplied database conversation and incoming client messages
establish conversation state. CRM activity supplies deal facts, not routing history.
Deal Manager handoffs and direct AM runs both use the shared DB persistence path.

| Database conversation and incoming messages | Action |
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
the console displays **No reply needed**. Client messages are saved before AM starts, including when the run fails or returns no reply.
No-reply turns do not append an empty Sasha entry.

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
readable, including their old workflow labels. Existing debug artifacts,
attachments, environment directories, CLI script paths, and Docker image names are
unchanged; no data migration is needed.

For behavioral acceptance, use isolated test database fixtures and read-only
requests to exercise every row above, including CRM history that differs from the
database history. Mocked tests verify plumbing, not the model's routing decisions.
Report real-agent checks separately.

## Handoff to a person

Sasha hands a deal to a Fresh Prints teammate (an ISA) when it judges that a person should take
over. There are no keyword rules or fixed categories: Account Manager decides, following
[the handoff guide](openai_managed/capabilities/account-manager/references/handoff.md), which
describes the kinds of situations (an item Fresh Prints does not carry, a group order, a client
upset with Sasha, work that keeps failing or a request it cannot resolve) and leaves the call to
the agent. It returns `status: "handoff"` with `handoff_reason` in its own words for the ISA, and
may include a short holding message for the client.

The code only carries out decisions, ported from Sasha v3:

1. In the same transaction as the notes and any holding draft, `sasha_deal_status` becomes
   `handoff_needed`, with the agent's reason in `status_reason`.
2. The deal is offered to the next active ISA in `sasha_handoff_isas`, round-robin by
   `rank_order`, never twice to the same ISA and with at most one open offer per deal. The ISA
   is offered "Handoff request — Deal N" with the reason.
3. Every `HANDOFF_TIMEOUT_CHECK_SECONDS` during ISA working hours (Mon–Fri 08:00–17:00 New York),
   an offer older than `HANDOFF_OFFER_TIMEOUT_MINUTES` working minutes (default 20) times out and
   goes to the next ISA. `POST /api/handoffs/check-timeouts?force=true` runs the check now. When
   every ISA has been tried, the deal stays handed off with no owner.
4. Later messages on a handed-off deal still run Account Manager, which now gets the handoff state
   (reason and current ISA offer). The agent works out what the message is. When it is the ISA
   answering the offer, it returns `isa_decision`, and the code applies it:
   - `accepted`: other open offers are closed, the ISA becomes the client's manager through the
     v3 assign-manager API (`V3_API_BASE_URL`, `JWT_AUTH_KEY`, `SASHA_ADMIN_USER`), and the client
     gets "I've looped in {ISA} to help you from here", with the ISA in CC.
   - `rejected`: the next ISA gets the offer. `unclear`: nothing changes.
   A person owns a handed-off deal, so any client draft Sasha writes on it is discarded.

Everything is stored in the Sasha database: the offers in `sasha_handoff_offers`, the reason in
`sasha_deal_status`, and the client transition as a generated message in
`sasha_conversation_history`. The ISA offer email is rebuilt from those rows, so nothing is kept in
files and nothing is sent from this host. `GET /api/handoffs/<deal-id>` shows the deal's status,
reason and offers with their emails; the run page shows the handoff panel.

Database setup: apply `sql/20261015_0001_sasha_deal_status_handoff_reason.sql` and then
`sql/20261016_0001_sasha_deal_status_reason.sql` to the operations
schema. If `sasha_handoff_isas` and `sasha_handoff_offers` are not there yet, apply v3 migrations
`20260710_0001_create_sasha_handoff_tables` and `20260710_0003_add_handoff_offer_thread_id`, then
add the ISAs to offer to.

Not ported from v3: email sending (v3's SQS queue), the ISA-reply judge prompt, Slack alerts, the
code-only external-apparel, promo-goods, MTO and order-history checks, and handing a deal back to
Sasha.

## Replying, opt-out and win-back

Account Manager decides whether to write at all and what a client's words mean for the deal,
following [conversation judgement](openai_managed/capabilities/account-manager/references/conversation_judgement.md)
and [win-back](openai_managed/capabilities/account-manager/references/winback.md). There are no
keyword rules: silence for a plain thanks or an auto-reply, a pause when the client asks to be
contacted later, one gentle re-engagement after a declined order, and v3's opt-out handling
(one calm win-back question, otherwise a permanent opt-out).

Each run shows the agent the deal's Sasha status with the reason saved when it was set. When a turn
changes it, the agent returns `deal_state`: `status` (active, paused, opt_out or completed), its
own `reason`, an optional `next_check_on` day, and an optional `crm_lost_reason`. Code saves it
in `sasha_deal_status` (with Sasha's reason in `status_reason`) and, when `crm_lost_reason` is set, marks the deal lost in
the CRM through the v3 edit-deal API, as v3 did after a win-back question or a permanent opt-out.
A client's answer to a win-back question on a lost deal still reaches Account Manager
(`incoming_deal_lost_winback_reply`).

One safety net stays in code: no draft is ever saved for a deal that is, or becomes, opted out.

`next_check_on` (after spring break, after an out-of-office, 60 days after a declined order) is
written to v3's `sasha_followup_schedule` as `next_followup_date` with Sasha's reason in
`scheduled_followup_reason`, the row v3's follow-up cron reads. An opt-out, a win-back question or a
completed order stops the schedule (`stopped` with `opted_out`, `opt_out_winback` or `completed`), as
v3 does. Nothing in the POC starts the follow-up on that day yet; the cron will.

Delivered-order notices from sales@ trigger one warm check-in through the internal-email skill.

Database setup: apply `sql/20261016_0001_sasha_deal_status_reason.sql`; the operations schema also needs v3's `sasha_followup_schedule`.

## Free samples

Fresh Prints sends free, blank samples (no print) so a client can check fit and quality. Account
Manager handles the selling and judgement, following
[the samples guide](openai_managed/capabilities/account-manager/references/samples.md): it offers a
sample when the client asks or doubts fit or quality (or as a follow-up tactic), checks the limits,
collects style, color, size, shipping address and contact details, and places the sample only once
everything is confirmed.

Each run gets the deal's samples and the limits from Sasha v3's `sasha_settings` table
(`followup_sample_max_unit_value`, `sample_max_per_deal`, `followup_sample_cooldown_days`). The
agent applies them; there are no rule checks in code.

The code only carries out what the agent decides, because it needs database writes and the
Freshdesk key: when the agent returns a confirmed `sample_request`, the sample is saved in
`sasha_sample_requests` and v3's Freshdesk ticket is created ("New Sample Requested for Fresh
Prints", Sample Orders, "BLANK sample only"). Without `FRESHDESK_DOMAIN` and `FRESHDESK_API_KEY`
the sample is saved without a ticket. If saving or the ticket fails, the turn fails with
`sample_not_placed` and nothing from it is saved. Logistics emails arrive as internal mail; the
agent returns `sample_update` (shipped, delivered or cancelled, with tracking) and the code stores
it. The outcome is recorded on the run (`sample`).

Database setup: apply `sql/20261015_0002_sasha_sample_requests.sql` to the operations schema, which
also needs v3's `sasha_settings`. Sasha's CRM user for deal lookups is the `sasha_user_id` setting.

Not ported from v3: the Freshdesk webhook and daily poller, the stale-shipment nudge and Slack alert,
and cancelling open samples on opt-out.

## Follow-ups

**Account Manager decides when.** After every turn (outreach, client response, internal update,
follow-up) Sasha sets the next follow-up day with its own reason in `deal_state.next_check_on`,
following "Choose the next follow-up" in the [follow-up skill](openai_managed/capabilities/followup/SKILL.md)
(v3's rhythm is the default guidance, not a fixed calendar; a client's own date always wins). The day
is written to v3's `sasha_followup_schedule` (`next_followup_date`, `scheduled_followup_reason`).

**An hourly job runs them.** Every `FOLLOWUP_CHECK_SECONDS` (default 3600) the web app claims deals
whose day has arrived (active schedule, deal not opted out or handed off) and starts a normal
follow-up run for each, with today as the business date. It runs only when `sasha_settings` has
`sasha_master_key=enabled` and `auto_followup_enabled=true`, Mon–Fri 8:00–17:00 New York. Claiming
clears the date in the same transaction, so a follow-up never starts twice.
`POST /api/followups/run-due?force=true` runs the check now (force skips only the hours check).

**In the follow-up run** Sasha gets follow-up signals (days since the client replied and since
Sasha wrote, conversation age, message counts, tactics used) and why this follow-up was planned. It
works out the phase itself; there is no stage. A drafted follow-up increments `followup_count`; if
Sasha sets no next day, the schedule is paused. A handoff stops it (`stop_reason: handoff`).

**By hand:** open a deal and select **Follow-up check** with a business date (defaults to today).
Days since the client's reply are optional and override the conversation's own dates. The notes
sidebar shows the existing notebook from the current environment's runs directory.

The same entry point is available through `POST /api/runs`:

```json
{
  "deal_id": "303992",
  "turn_type": "follow_up",
  "as_of_date": "2026-10-13",
  "days_since_client_reply": 5
}
```

`follow_up_stage` is rejected; date must use `YYYY-MM-DD`; optional days
must be a nonnegative integer. Initial follow-up checks cannot include client
messages or new artwork. Additional client messages remain available during the
active run. Ordinary requests omit follow-up fields. `turn_type: "outreach"`,
`turn_type: "response"`, and `workflow` are rejected on new requests. The CLI
continues to start ordinary Account Manager turns.

| Explicit check state | Account Manager action |
|---|---|
| Empty database conversation | No reply |
| Unanswered client request in DB history | Client response |
| Conversation exists, no unanswered request | Choose one unused tactic that adds value, then the next follow-up day |
| No supported unused tactic | No reply |
| Client message arrives during follow-up work | Reconsider and handle it in the same session |

Run metadata says **requested follow-up check**, since the final action may be a
client response or no reply. Follow-up research is read-only. There is no second
agent, scheduler, automatic sending, or automatic sending.

The notebook retains every prior `Stage N follow-up generated: tactic_id` entry.
Only a successful nonblank follow-up draft adds one entry. No-reply, failed,
ordinary, or superseded work does not add a used tactic. Historical `sent` entries
also count as used, but that wording does not prove delivery. Generated drafts
count as used in this POC. No history migration is needed.

Samples are covered in [Free samples](#free-samples).

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

Set `NON_PROD_HOST`, `NON_PROD_PORT`, `NON_PROD_USER`, and `NON_PROD_PASS`
in the private `.env`. No database certificate is required.
Set `DEAL_MANAGER_MAIN_SCHEMA` and `DEAL_MANAGER_OPERATIONS_SCHEMA` to the actual
database names for this deployment. Both are required; there are no built-in schema names. The configured login
must read those schemas and write the three Sasha persistence tables. Schema values are validated before use in SQL. The agent
receives a public schema-role map, and result validation checks the configured main
schema. Table names and relationships remain application-specific; changing schema
names does not adapt a different database model automatically. The lookup helper uses
read-only transactions and bounded queries. Database access must work from Docker.
DM and AM receive the same explicit environment-variable allowlist; credential values
are not placed in command arguments, copied skills, task prompts, or saved job files.

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

## Account Manager customer links

Account Manager can share a customer-facing HQ link in client responses, follow-ups,
and internal-update notifications. Outreach does not use this capability. Deal Manager
has no new link command or skill. AM reuses its existing read-only database connector.

AM first verifies the proof on the deal in the browser, then runs
`python /opt/sasha/customer_links.py PROOF_ID`. The helper checks the current deal's
non-deleted proof association and customer record, then returns `proof_id`, `url`, and
`login_included`. It reads fresh data on each invocation, including after proof creation.
The URL is `{FP_HQ_BASE_URL}/design/{proof_id}?login={encoded user.password}`.
When that customer has no stored login value, the URL has no login parameter.
Missing records, an unrelated proof, or unavailable database access return an error
without a URL. Required-link failures use `customer_link_unavailable` in the normal
failed run result. Every AM run requires database access for notes and conversation persistence.

Both viewing and ordering use this URL. Before drafting an order invitation, AM checks
proof completion, quantity, stock, and delivery in the browser. Pending proofs may
receive a status link, never a ready-to-order claim. The customer places the order.

Set `FP_HQ_BASE_URL` explicitly for the deployment. It is not inferred from
`FP_ENVIRONMENT` or the CRM hostname. Configure matching database values through
`NON_PROD_HOST`, `NON_PROD_PORT`, `NON_PROD_USER`, `NON_PROD_PASS`,
`DEAL_MANAGER_MAIN_SCHEMA`, and `DEAL_MANAGER_OPERATIONS_SCHEMA`.
Neither DM nor AM requires or receives a separate CRM schema setting.
Existing CRM/V3 credential and schema aliases remain supported. Despite their legacy
names, these settings select the connection; `FP_ENVIRONMENT` does not select a database.
HQ, CRM, and database settings must all refer to the same environment.

Both DM and AM containers receive only the required database environment variables through
Docker's `--env NAME` options. Values are supplied through the Docker subprocess's
environment, not command arguments. No database certificate is mounted.
Neither creates a database configuration JSON file or passes the entire `.env` file.
Credentials are not written into task prompts or persistent run configuration.
The helper returns no separate customer login value. Authenticated URLs are present
in generated drafts and may appear in normal run output; do not copy them into notes.

After deploying, configure the sender and DB grants, rebuild both executor images, and restart the web app.
No database migration is needed. Validate HQ login with an approved test customer in
a fresh browser session before rollout; creating a correctly formatted URL alone
does not verify HQ authentication.

### DB regression tests

Run `python -m unittest discover -s tests`, `python -m unittest discover -s webapp/tests`,
and `python -m unittest discover -s deal_manager_agent/tests`.
The MySQL storage tests are opt-in and hardcode localhost plus schema `sasha_test` to avoid
QA writes. Start a disposable MySQL 8 container with an empty root password and that schema,
then set `SASHA_TEST_MYSQL_PORT` to its published localhost port. No CA file is needed.
Run `python -m unittest tests.test_sasha_database -v`, then remove the container.
These tests exercise actual commits, rollback, concurrent deduplication, metadata, and notes.
Live agent/browser checks remain opt-in; mocked tests do not verify model output quality.
