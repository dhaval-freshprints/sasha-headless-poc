# OpenAI-managed Sasha architecture

## Scope

This POC accepts a Fresh Prints QA deal ID and an optional client message. One
OpenAI-managed Sasha runner handles both modes:

- no client message means initial outreach;
- a supplied client message means a client-response turn.

The application makes no further intent decision. Astra reads the current deal,
uses the `sasha-sales` skill, chooses the relevant workflow, operates the browser,
and returns a structured client-message draft. The POC does not send that message.

There is no contract validator, semantic message validator, or independent
before/after browser checker.

## Architecture

```mermaid
flowchart LR
    Input["1. Deal ID<br/>optional client message"]
    CLI["2. Generic Sasha CLI<br/>build task and deal URL"]
    History["Per-deal conversation JSON<br/>previous client + Sasha messages"]
    Runner["3. OpenAIManagedRunner<br/>session, timeout, artifacts, cleanup"]
    Workspace["4. Disposable workspace<br/>browser profile + copied skill"]
    API["5. OpenAI Agents API<br/>managed session"]
    Astra["6. Astra<br/>choose next action"]
    Skill["Sasha skill<br/>SKILL + Playbook + Workplace"]
    Executor["7. Self-hosted Codex executor<br/>Docker container"]
    Browser["8. Node.js Playwright<br/>headless Chrome"]
    FP["9. Fresh Prints QA<br/>deal and relevant pages"]
    Observe["10. Observation<br/>DOM text + screenshots"]
    Complete{"Requested work<br/>complete or blocked?"}
    Output["11. SashaResult JSON<br/>generated HTML message"]
    Caller["12. Caller receives draft<br/>message is not sent"]
    Artifacts["Run evidence<br/>events, commands, URLs,<br/>screenshots, pricing, skill"]

    Input --> CLI --> Runner
    History -- "load" --> Runner
    Runner -- "append completed turn" --> History
    Runner --> Workspace
    Runner --> API --> Astra
    Skill -.-> Astra
    Workspace --> Executor
    API --> Executor --> Browser --> FP --> Observe --> Complete
    Complete -- "No: continue managed loop" --> Astra
    Astra --> Executor
    Complete -- "Yes" --> Output --> Runner --> Caller
    Workspace -.-> Artifacts
    API -.-> Artifacts
    Runner -.-> Artifacts
```

## End-to-end flow

1. The CLI receives a QA deal ID and optional `--message`.
2. The runner derives the conversation filename from the deal ID, creates it
   when needed, and loads that deal's previous ordered conversation.
3. It creates one `SashaTask`. `client_message is None` is the only mode choice
   in Python; no client intent is classified. Previous history and the current
   client message are labeled separately as untrusted data.
4. The runner creates a disposable workspace and copies the authenticated
   browser profile and `sasha-sales` capability into it.
5. The runner creates one OpenAI Agents API session with generic Sasha
   instructions, the shared JSON result schema, and
   `/workspace/capabilities` registered for skill discovery.
6. The local Docker container starts `codex exec-server` and connects the
   self-hosted environment to the managed session.
7. Astra reads the task and skill, then begins at the exact supplied deal URL.
8. Astra decides which page and Playwright action are needed from the previous
   conversation, current client message, deal history, linked proofs, Playbook,
   and Workplace. Python does not select a quotation, product, stock, proof, or
   revision route.
9. Commands run inside the container. Browser observations return to Astra.
10. Astra repeats the decide-act-observe cycle until the requested work is
   complete or it establishes a real blocker.
11. Astra returns one `SashaResult` JSON object containing the generated HTML
    message or a failure description.
12. The runner saves run evidence, stops the container, removes the disposable
    browser profile, and deletes the managed session.
13. For a completed result, the runner atomically appends the current client
    message and exact Sasha HTML to the deal's conversation file. The Sasha
    entry is marked `generated`, not `sent`. Failed turns are not appended.
14. The CLI prints result JSON to stdout. The generated message is not sent.

## Instruction layers

| Layer | File | Responsibility |
| --- | --- | --- |
| Stable agent instructions | `openai_managed/SASHA01_agent_instructions.md` | Sasha identity, QA boundary, untrusted-data rule, prohibited actions, and result requirement |
| Skill routing | `openai_managed/capabilities/sasha-sales/SKILL.md` | Start from the deal, select the workflow, and continue the browser loop |
| Playbook | `openai_managed/capabilities/sasha-sales/references/playbook.md` | Sales judgment, outreach, price and MOQ policy, verification, and client-writing style |
| Workplace | `openai_managed/capabilities/sasha-sales/references/workplace.md` | Fresh Prints page locations, fields, controls, and observed UI behavior |
| Per-run task | `workspace/TASK.md` | Exact deal, turn mode, optional client message, artifacts, and output request |

The stable instructions stay short. Business behavior lives in the skill, and
the Playbook and Workplace can change without adding Python intent routes.

## Component ownership

| Component | Owner | Responsibility |
| --- | --- | --- |
| Generic CLI and task contract | Sasha repository | Accept input and construct one `SashaTask` |
| Per-deal conversation store | Sasha repository | Derive the internal file, load previous turns, and atomically append completed turns |
| Runner | Sasha repository | Session creation, timeouts, result parsing, progress, pricing, artifacts, and cleanup |
| Sasha skill | Sasha repository | Company policy, sales judgment, workflow guidance, and page knowledge |
| Managed agent loop | OpenAI | Repeated model and command turns until completion or failure |
| Astra | OpenAI | Interpret context, select actions, observe results, and draft the message |
| Self-hosted executor | Sasha infrastructure | Execute Astra's commands in the disposable Docker environment |
| Playwright and Chrome | Sasha container | Interact with Fresh Prints QA using the copied authenticated profile |
| Fresh Prints QA | Fresh Prints | Source of current deal, proof, product, price, stock, and workflow state |
| Calling application | Future integration | Decide whether and how to deliver the generated message |

## Output and evidence

The shared result contains:

- `deal_id`
- `status`
- `message_html`
- `failure_code`
- `failure_message`

Each run retains `task.json`, `TASK.md`, the copied skill, session events,
session items, executor logs, screenshots, visited URLs, best-effort pricing,
and `result.json`. The browser profile is removed during cleanup.

Conversation continuity is stored separately from disposable run evidence in
`runs/openai-managed/conversations/DEAL<deal_id>_conversation.json`. The CLI
does not accept a conversation path. One deal never loads another deal's file.

`--verbose` reports runner milestones, Astra commentary, and command status to
stderr. It does not create a second execution path. `--pricing` is also optional
and best effort; missing usage does not fail an otherwise completed turn.

## Current tested boundary

The managed path has been tested for:

- initial outreach from a supplied QA deal;
- one client-response quotation scenario on a linked existing proof;
- two consecutive client-response runs where a fresh second managed session
  received the first run's exact conversation from the per-deal JSON file.

In the quotation scenario, Astra independently started from the deal, followed
the linked proof, entered quantity 40, read the recalculated unit and total
prices, and clicked Cancel instead of Save Price. The returned message was not
sent.

The managed path has not yet been validated for product suggestions,
alternatives, stock and shipping questions, proof creation, attachments, or
revision submission. Those scenarios should extend and test the same skill and
runner rather than add intent-specific Python runners.
