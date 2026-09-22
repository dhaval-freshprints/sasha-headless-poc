# OpenAI-Managed Sasha: Outreach-Only Plan

## 1. Status

- Identifier: `OUTREACH01`
- Date: 2026-09-22
- Scope: initial outreach draft only
- Input: one Fresh Prints QA deal ID
- Output: one HTML outreach message draft
- Remote writes: none
- Email sending: not included
- Existing Claude implementation: remains unchanged

## 2. User decision

For this scenario, “send outreach” means **generate and return the outreach
message**. Sasha must not send the message through Fresh Prints CRM, email, or
another external system.

## 3. Verified baseline facts

The repository already defines the expected outreach behaviour:

- The current CLI treats a run with no client message as outreach
  (`run_cli.py:4-6`, `run_cli.py:82-88`).
- The current API treats an omitted or null `client_message` as initial outreach
  (`api.py:1-7`, `api.py:30-32`).
- The current agent receives the exact deal URL and an explicit initial-outreach
  instruction (`brain.py:393-405`).
- The outreach playbook says all required facts are on the deal page and forbids
  opening the quoter or changing anything (`prompts/playbook.md:61-72`).
- The existing outreach evaluation checks that the run does not visit the
  quoter, stock checker, or proof-creation page; that the proof count stays the
  same; that the reply starts with “Hey”; that it mentions Fresh Prints; that it
  contains no price; and that it has Sasha’s sign-off
  (`evals/asks.json:2-22`).
- The existing evaluator already supports URL, reply-pattern, sign-off, and
  before/after proof-count checks (`evals/run.py:92-114`,
  `evals/run.py:119-175`).
- The existing QA configuration builds a deal URL from `FP_BASE_URL` and the
  deal ID (`config.py:13`, `config.py:57-58`).

OpenAI documents that, for a self-hosted Agents API environment, OpenAI runs the
agent harness while the application runs `codex exec-server` inside its own
environment. The application creates the session, starts the executor with the
returned environment ID and remote URL, sends work, and observes session events:

- [Self-hosted sandboxes](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted)

## 4. Exact outreach behaviour

Sasha reads only the supplied deal page and determines:

1. The client’s first name.
2. Whether the deal has a proof.
3. Whether a proof is finished or still with the art team.
4. Whether the deal is a flash/rush order.
5. The garment and occasion, when shown.

Sasha then applies the existing playbook:

| Deal state | Outreach behaviour |
|---|---|
| No proof | Introduce Sasha as the Fresh Prints account manager and ask what products and designs the client has in mind. |
| Finished proof | Mention the occasion and garment when available; ask about budget, quantity, and when they need the order. |
| Proof in progress | Say the art team is working on it and that Sasha will follow up; ask about budget, quantity, and timing. |
| Flash/rush | Say Sasha will move quickly; ask whether it is needed within five days and whether quick design revisions are wanted. |

Common rules:

- Start with `Hey [first name]!`.
- Use `Hey there!` when the name is missing or clearly fake.
- Introduce Sasha as the client’s account manager at Fresh Prints.
- Keep the message warm, short, and action-oriented.
- Return HTML fragments, not Markdown or a complete HTML document.
- End with `Best,<br>Sasha` or `Thanks,<br>Sasha`.
- Do not quote prices, offer samples, promise dates, open the quoter, or change CRM state.
- Do not claim facts that are absent from the deal page.

## 5. Public contract

The first POC has one command:

```bash
python scripts/run_openai_managed_outreach.py DEAL_ID
```

The caller supplies no prompt, product details, proof details, or message text.

The successful result is intentionally small:

```json
{
  "deal_id": "303839",
  "status": "completed",
  "message_html": "<p>Hey Thomas!</p>...<p>Best,<br>Sasha</p>",
  "failure_code": "",
  "failure_message": ""
}
```

The application returns this draft to its caller. Browser evidence, screenshots,
session events, and executor logs are stored as run artifacts; they are not part
of the public result.

If the deal cannot be inspected reliably, the result is `failed` with an empty
`message_html`. Sasha must not fabricate a generic outreach after an
authentication failure, missing deal, timeout, or invalid browser result.

## 6. Smallest architecture

```text
DEAL_ID
   |
   v
Outreach CLI
   |
   | creates one task and one self-hosted session
   v
OpenAI Agents API + Astra
   |
   | asks the connected executor to run Playwright
   v
Disposable Sasha sandbox
   |
   | opens exactly the QA deal page with copied login state
   v
Fresh Prints QA deal
   |
   | read-only observations
   v
Astra writes one outreach draft
   |
   v
Host validates result -> JSON output + audit artifacts
```

The Sasha application owns:

- validating the deal ID;
- creating and deleting the Agents API session;
- starting and stopping the disposable executor;
- copying and deleting the run-specific browser profile;
- supplying the exact deal URL and outreach rules;
- validating and returning the final result;
- writing audit artifacts.

OpenAI’s managed harness and Astra own:

- deciding how to inspect the page with Playwright;
- interpreting the observed deal state;
- selecting the matching outreach rule;
- drafting the outreach message.

The POC does not recreate the current granular browser toolset.

## 7. Required prerequisites

### OpenAI

The official OpenAI documentation requires:

- `OPENAI_API_KEY` in the host application for session and inference requests;
- `OPENAI_EXECUTOR_API_KEY` in the provisioning process;
- the executor key passed into the sandbox as `CODEX_API_KEY`;
- Agents API access and access to the configured Astra model;
- outbound access to the OpenAI API and the executor WebSocket endpoint.

The application API key must remain outside the agent sandbox. These
requirements come from the official
[self-hosted sandbox guide](https://developers.openai.com/api/docs/guides/agents-api/environments/self-hosted).

### Fresh Prints QA

- `FP_BASE_URL` pointing to Fresh Prints QA.
- A working authenticated Chromium profile for the QA CRM.
- At least one QA deal for each outreach branch being evaluated.
- Explicit authorization before sending a QA deal’s data to OpenAI.

### Local runtime

- Docker.
- A sandbox image containing the Codex CLI, Node.js, Playwright, and pinned Chromium.
- A writable run-artifact directory.

## 8. Proposed files

Keep all new implementation isolated from the current Claude path:

| File | Single responsibility |
|---|---|
| `openai_managed/outreach.py` | Outreach input/result data classes and result validation. |
| `openai_managed/runner.py` | Create one Agents API session, send one task, collect one result, and clean up. |
| `openai_managed/sandbox.py` | Prepare, start, stop, and clean one disposable self-hosted executor. |
| `openai_managed/OUTREACH01_rules.md` | Only the outreach facts, writing rules, and prohibitions. |
| `scripts/run_openai_managed_outreach.py` | Accept `deal_id`, run outreach, and print the result. |
| `Dockerfile.openai-managed` | Codex executor plus Playwright/Chromium runtime. |
| `tests/test_openai_managed_outreach.py` | Contract and deterministic message-validation tests. |
| `tests/test_openai_managed_runner.py` | Session lifecycle and cleanup tests using fakes. |

Do not change `brain.py`, `llm.py`, `toolset_executor.py`, `browser.py`,
`api.py`, or `run_cli.py` in this scenario.

## 9. Implementation sequence

Each step gets its own review before the next step begins.

### Step 1 — Define the outreach contract and rules

Build only:

- `OutreachTask(deal_id, task_id, deal_url)`;
- `OutreachResult(deal_id, status, message_html, failure_code, failure_message)`;
- outreach-only rules copied from the verified existing playbook;
- local validation for deal ID, status, required message, forbidden price claims,
  HTML format, and sign-off.

Tests use fixed strings. No OpenAI call, browser, Docker, or QA access occurs.

Exit criterion:

- A developer can read the contract, rules, and tests and explain exactly what
  Sasha may read and what it must return.

### Step 2 — Prove the managed session and sandbox lifecycle

Build the smallest runner and disposable sandbox:

1. Create one self-hosted Agents API session.
2. Start `codex exec-server` in one container.
3. Wait for the connected event.
4. Send a harmless non-browser task.
5. Collect the final result and session events.
6. Stop the container and delete the session.

Do not access Fresh Prints in this step.

Exit criterion:

- One local smoke test proves connection, execution, result retrieval, and cleanup.

### Step 3 — Implement the first real scenario: fresh deal with no proof

This is the first end-to-end outreach capability.

1. Copy the authenticated QA profile into the run workspace.
2. Give Astra the exact QA deal URL and outreach-only rules.
3. Let Astra open and inspect the deal using Playwright.
4. Require one `OutreachResult` JSON object.
5. Validate the result on the host.
6. Store screenshots, session events, executor logs, and the final result.
7. Delete the copied browser profile and sandbox.

Expected draft content:

- correct first name, or `there` according to the rule;
- Fresh Prints account-manager introduction;
- help with the client’s merch;
- one question about products and designs;
- Sasha sign-off;
- no price, sample, or date promise.

Exit criterion:

- The authorized fresh QA deal passes three consecutive runs with correct
  messages, no forbidden navigation, no proof-count change, and no manual help.

### Step 4 — Add the remaining outreach branches one at a time

Implement and approve these separately:

1. Finished proof.
2. Proof in progress.
3. Flash/rush deal.

For each branch:

- select one controlled QA deal;
- record the expected facts and reply patterns before running it;
- add the evaluation case;
- run it three times;
- inspect any failure before changing rules.

Do not add product search, quoting, stock checks, proof creation, revisions, or
client-reply handling during these steps.

### Step 5 — Add the runner boundary only after outreach passes

After every outreach branch passes:

- define the smallest provider-neutral runner interface required by callers;
- wrap the managed outreach implementation behind it;
- keep the current Claude implementation available;
- add a configuration switch for controlled comparison.

Do not modify the API before the CLI and QA evaluation are accepted.

## 10. Deterministic checks

### Before and after the run

- Capture the deal’s proof IDs before the task.
- Capture the deal’s proof IDs after the task.
- Require the sets to be identical.
- Record every browser URL visited.
- Fail evaluation if the run visits the quoter, stock checker, proof creation,
  design tool, or a non-allowed host.

### Final message

- `deal_id` matches the task.
- `status` is valid.
- A completed result has non-empty HTML.
- A failed result has empty HTML and a failure code/message.
- Message starts with the required greeting.
- Message identifies Sasha/Fresh Prints appropriately.
- Message contains the correct branch-specific ask.
- Message contains no currency value.
- Message ends with Sasha’s sign-off.
- Message contains no Markdown or full-document HTML wrapper.

The host checks format and obvious prohibited content. QA evaluation checks
whether Astra selected the correct branch and used the correct observed facts.

## 11. Failure behaviour

| Failure | Result |
|---|---|
| Authentication expired | `failed`; no draft |
| Deal not found | `failed`; no draft |
| Deal ID mismatch | `failed`; no draft |
| Executor connection failure | `failed`; clean up |
| Agent timeout | `failed`; clean up |
| Invalid final JSON | `failed`; preserve artifacts |
| Empty or invalid outreach message | `failed`; preserve artifacts |
| Forbidden navigation | evaluation failure; do not accept the run |
| Proof set changed | evaluation failure; investigate before another QA run |

Because outreach performs no authorized mutation, there is no `uncertain`
mutation state in this scenario.

## 12. Explicitly out of scope

- Sending an email or CRM message.
- Receiving or handling a client reply.
- Product discovery.
- Quoting.
- Stock checking.
- Proof creation.
- Proof revision.
- Purchases or orders.
- Production data.
- Replacing the current Claude runner.
- General provider abstraction beyond the small boundary added after acceptance.

## 13. Definition of done

The outreach scenario is complete when:

1. A caller supplies only a QA deal ID.
2. Sasha independently reads the deal and drafts the correct outreach branch.
3. The caller receives valid HTML message content and no internal reasoning object.
4. Sasha never sends the message.
5. No Fresh Prints state changes.
6. Every outreach branch passes the agreed QA repetitions.
7. Failures return no fabricated outreach.
8. Run artifacts make each outcome understandable.

## 14. Immediate next action

Implement **Step 1 only**: the outreach input/result contract, the outreach-only
rules file, and local unit tests. Review those small files together before adding
the Agents API runner or Docker sandbox.
