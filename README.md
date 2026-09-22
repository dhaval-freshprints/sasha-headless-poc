# Sasha Browser POC

Sasha as a browser-driving sales rep. One model, one loop, Anthropic's browser toolset
(`browser_toolset_20260801`) executed against Playwright, no business rules in the prompt.
Runs against Fresh Prints **QA** (`v4-qa.internal-fp.com`).

```
deal_id (+ optional client message)
        │
        ▼
   brain.py            — the loop: model returns a batch of browser calls, we run them in
        │                order, send every result back, repeat until reply_to_client
        ▼
   toolset_executor.py — one method per toolset member: read_page/find (tree with [ref_N]),
        │                clicks by ref or coordinate, form_input, screenshot, tabs
        ▼
   browser.py          — logged-in Chromium session, named tabs, readiness, toasts
        │
        ▼
   QA CRM / quoter / proofs
```

## Files

| File | Job |
|---|---|
| `config.py` | settings from `.env` |
| `llm.py` | the Anthropic call: system prompt, the toolset + `reply_to_client`, message shapes |
| `toolset_executor.py` | runs each toolset member against Playwright; owns refs and tab ids |
| `browser.py` | the Chromium session and tabs |
| `brain.py` | Sasha Brain — the loop + system prompt |
| `run_limits.py` | action/time budgets and a reserved verification window |
| `tool_policy.py` | shared disabled-tool policy and key-repeat bounds |
| `prompts/workplace.md` | the map: pages, URLs, how each form works, incl. the Design Tool. Facts only, no opinions |
| `prompts/playbook.md` | how Sasha works and writes: judgment, voice, outreach, client reply |
| `run_cli.py` | terminal runner |
| `api.py` | `POST /simulate`, `POST /reset` |
| `report.py` | rolls every `run.json` into one table |
| `auth_setup.py` | one-time login, saves session to `./auth` |

## Setup

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env     # fill FP_PASSWORD and ANTHROPIC_API_KEY
python auth_setup.py     # logs in, saves ./auth
```

## Model

Claude via the Anthropic API. Set `ANTHROPIC_API_KEY` and `MODEL` in `.env`. The model must
support the browser toolset: `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5-1`,
`claude-opus-4-8`. Claude API or Vertex only; the toolset is not on Bedrock.

## How the model sees the page

Nothing is pushed after an action. The model asks: `read_page` (accessibility tree, every
element tagged `[ref_N]`), `find` (a query, up to 20 matching elements), `get_page_text`
(visible text with control-state annotations), `screenshot`, `zoom`. It acts by ref (`left_click`, `form_input`,
`scroll_to`) or by viewport coordinate when a control has no name (canvas, icon buttons).
Refs live until the tab navigates; a stale ref returns an error and the model re-reads.

Checkbox cards include enabled/disabled and selected state alongside their text. The shared
`observation_state.py` helper reads native disabled controls and ARIA/inert state; it does not
infer eligibility from colour, prices or delivery dates. Supplemental DOM controls retain
that state in `read_page`/`find` too. Shipping recommendations use the current date mode and
enabled options, rather than listing every visible tier.

Controls the accessibility tree does not list (role-less DIVs with a pointer cursor, a tabindex
or a button class: React chips, tiles, cards, the Design Tool's whole UI) are added to the tree
as `button "<text>"`. Tooltip discovery runs for `find` and interactive `read_page` requests;
names are cached while the corresponding node refs remain live. Ordinary reads reuse known names.

One model turn can carry several calls. They run in order and stop at the first failure;
the rest are answered `Not executed: an earlier action in this turn failed.`

## Run

```bash
python run_cli.py 303817 --once                  # initial outreach, exit
python run_cli.py 303817                         # initial outreach, then chat as the client
python run_cli.py 303817 -m "price for 50?"      # one client-message turn, exit
python run_cli.py 303817 --headed                # watch the browser
```

API:

```bash
uvicorn api:app --port 8100
curl -X POST localhost:8100/simulate -H 'content-type: application/json' -d '{"deal_id": 303817}'
curl -X POST localhost:8100/simulate -H 'content-type: application/json' -d '{"deal_id": 303817, "client_message": "price for 50?"}'
```

Every turn writes `runs/deal_<id>/turn_<timestamp>/run.json` and appends to
`runs/deal_<id>/transcript.md`, Sasha's only memory of the deal. Batches with executed browser
work get a recording for humans; final-reply-only and rejected-only batches do not. A full
screenshot that is the last browser observation in the batch is reused once for recording.
Any subsequent browser tool invalidates reuse; a zoom never substitutes for a full image.
The model only receives images it requests.

## Work limits and timing

Defaults are 40 work batches, 120 tool attempts and 600 seconds. `MAX_BATCHES` replaces
`MAX_STEPS`; the old setting still works when `MAX_BATCHES` is absent. When a limit is reached,
new mutations stop. Up to 8 inspection/navigation calls and 45 seconds remain to check an
uncertain save and report what finished. Two model turns are reserved after the batch cap.
Limits are checked between calls, so an in-flight browser call can finish after the work
deadline. Model requests have a 60-second timeout, shortened to the remaining budget, with
automatic SDK retries disabled. All settings are shown in `.env.example`.

A key call may send at most 20 chords. Arrow movement requires a screenshot/zoom checkpoint
after three calls or 20 arrow presses, whichever comes first; further movement waits until
the next model turn can inspect that image. Determining that artwork actually moved remains
a visual check. Disabled tools, including fixed `wait`, are rejected locally as well as
withheld from the model's tool configuration.

Reads and captures do not wait for network idle. Actions check visible processing indicators
under a bounded readiness deadline. A pending action stops dependent calls in its batch;
it does not establish a successful save. Proof persistence still needs inspection after saving.
Dropdown polling uses one three-second deadline without full-page scans on each poll.
Autocomplete checks input and dropdown loading state, confirms the typed query, and waits
for a matching enabled option across consecutive observations. It rechecks that option before
clicking and verifies the selected label/value afterward. A visible no-results message after
an observed loading cycle is a confirmed no-match; a timeout or an unchanged unrelated list
is uncertain. Widgets without recognizable completion/selection signals return uncertainty
instead of reporting absence or success. These checks are shared by autocomplete fields on
every page; they do not hardcode products or style codes.

`run.json` retains existing totals and adds:

- Per-step elapsed `seconds`, `outcome`, and disjoint readiness/tree/enrichment/capture timings.
- Per-model-call duration, tokens, request ID and stop reason or error type.
- Recording duration and screenshot reuse, separate from model-requested capture timings.
- Prompt/source hashes, SDK version, effective non-secret limits and disabled tools.
- A turn `stop_reason` and counts of reserved actions and verification actions.

For compatibility, `browser_seconds` still includes recording. Subtract `recording_seconds`
to isolate tool execution; do not add nested step phase timings to step totals. The model
duration includes the complete SDK request, not just model inference.

Focused local checks (no API calls or live browser):

```bash
python -m unittest discover -s tests -v
```

## OpenAI-managed outreach POC

This isolated path reads one QA deal and returns an outreach draft. It does not
send a message or change Fresh Prints data.

Build the executor image and create its authenticated browser profile once:

```bash
docker build -f Dockerfile.openai-managed -t sasha-openai-managed:local .
.venv/bin/python scripts/setup_openai_managed_auth.py
```

Then run outreach for an explicitly authorized QA deal:

```bash
.venv/bin/python scripts/run_openai_managed_outreach.py DEAL_ID
```

Add `--verbose` to see orchestration milestones, Sasha commentary, and tool-step
status while the run is active:

```bash
.venv/bin/python scripts/run_openai_managed_outreach.py DEAL_ID --verbose
```

Add `--pricing` to retrieve the run's best-effort token usage and print an
estimated OpenAI model cost:

```bash
.venv/bin/python scripts/run_openai_managed_outreach.py DEAL_ID --pricing
```

The pricing estimate uses the published Standard rates for `gpt-6-astra`.
If OpenAI does not return usage, the command prints that pricing is unavailable.
The detailed estimate is also saved as `pricing.json` with the other run artifacts.

The command prints Sasha's structured result as JSON. Session events, executor
logs, browser artifacts, and the final result are saved under
`runs/openai-managed/`.

## Evals

`evals/asks.json` is a fixed set of client asks on QA deals, each with checks the harness can
score without a human: pages visited, CRM state before vs after (proof count, revisions, saved
quantity), reply patterns, sign-off, and a round cap.

```bash
python evals/run.py          # all asks, in order (ask 10 depends on ask 5)
python evals/run.py 2 4      # a subset
```

One row per ask plus totals; full detail in `evals/results/<timestamp>.json`.

| Pass (2026-09-18) | Score | Wrong outcomes | Rounds | Cost |
|---|---|---|---|---|
| Baseline, toolset as first built | 7/10 | 3 (no reply on 6, no proof on 8) | 134 | $4.15 |
| Pass 4, widget classes A/B/E + map fixes | 8/10 | 0 | 98 | $2.58 |
| Pass 5, + link URLs, options on open | 8/10 | 0 | 91 | $2.23 |

The two remaining fails are the 15-round cap on the two quoter asks (4 and 6), not wrong answers. The score is the
measure of "Sasha can do it from the browser and the rulebook". Rules for the three files it
exercises are in `TOOLSET01_plan_three_kinds_of_knowledge.html`: widget mechanics live in the
executor, page facts and CRM rules in `workplace.md`, judgment in `playbook.md`.

## Docker

```bash
python auth_setup.py     # on the host first, so ./auth exists
docker compose up --build
```

## Prompts

Exactly two, both in the system message, cached (one `cache_control` marker; ~90% cache hit
rate measured on the previous tool design):

- **workplace.md** — where things are. A line belongs here if it has a URL, a button name or
  a field label in it. If it needs "always", "never" or "prefer", it doesn't.
- **playbook.md** — what to do. If a section ever needs a priority order to resolve conflicts
  with another section, something has gone wrong.

When the model has to explore a page (the quoter, first time: 11 steps), a human writes what it
found into `workplace.md`. Policy does not go in prompts; it goes in the CRM's own forms or in code.

## Results so far (QA)

All rows below were measured with the previous hand-written tools (tree + screenshot pushed
after every step). No turn has been run on the toolset yet; rerun these and add rows.

| Deal | Task | Model | Steps | Tokens in | Time |
|---|---|---|---|---|---|
| 302886 | follow-up on stalled deal | claude-sonnet-5 | 2 | 16K | 27s |
| 303688 | "price for 50?" — used proof qty field, didn't save | claude-sonnet-5 | 8 | 72K | ~70s |
| 303817 | initial outreach | claude-sonnet-5 | 15 | 228K | ~90s |
| 303817 | initial outreach | claude-opus-5 | 4 | 25K | 28s |
| 303675 | "change the text to Welcome" → revision request with the instruction in it, verified | claude-opus-5 | 5 | 185K | 59s |
| 303675 | "30 as embroidery?" — quoter unmapped: explored it, one price | claude-opus-5 | 11 | 1.1M (88% cached) | 2m 24s |
| 303675 | same, quoter mapped: both embroidery sizes + screen print baseline, all read from the quoter | claude-opus-5 | 15 | 1.0M (90% cached) | 3m 06s |
| 303675 | "green polos for an office event" — catalog unmapped: guessed styles in the quoter, one pick (Acid Green) | claude-opus-5 | 16 | 2.4M (92% cached) | 5m 02s |
| 303675 | same, catalog mapped: one filtered URL → 27 results → priced two (Forest Green, Gorge Green) with stock | claude-opus-5 | 15 | 1.2M (91% cached) | 4m 19s |
| 303675, 303688 | "shipping options for 50?" — dropped Fresh Prints Flash on both (4/5 and 3/5 tiers) | claude-opus-5 | 9, 6 | — | 2m 28s, 1m 21s |
| 303675, 303688 | same, after one playbook line on completeness: 5/5 tiers on both, costs and dates verified | claude-opus-5 | 9, 5 | — | — |
| 303821 (empty deal) | outreach → "green polos for an Android event" → three catalog picks priced at MOQ | claude-opus-5 | 5, 22 | — | 53s, 5m 42s |
| 303821 | "Nike, print Droid on the chest" — create-proof wizard unmapped: **told the client a mockup was started; no proof created** | claude-opus-5 | 9 | 1.7M | 2m 22s |
| 303821 | same, wizard mapped + playbook line: proof 576394 created with "Front, center chest. The word Droid…", verified on the deal before replying | claude-opus-5 | 23 | 5.8M (95% cached) | 4m 26s |
| 303821 | "around 35, event Oct 12" → set qty on its own proof, saved, price + delivery vs event date | claude-opus-5 | 3 | — | 47s |
| 303821 | "10 S, 15 M, 10 L" → **opened the stock checker, didn't use it, reported quoter numbers from two turns earlier as "confirmed"** | claude-opus-5 | 1 | — | 37s |
| 303821 | stock re-asked ×3, after map + two playbook lines: same behaviour every time. History outweighs the playbook once a claim is in it | claude-opus-5 | 1 | — | ~25s |
| 303688 (fresh) | "20 M and 20 L of the bag?" → used the stock checker as mapped, caught that the bag is one-size | claude-opus-5 | 4 | — | 52s |

## Why tree and screenshot together

Measured before the toolset, with the hand-written tools. Same task ("change the text to
Welcome"), same model, three tool designs:

| Tool design | Steps | Time | Result |
|---|---|---|---|
| accessibility tree only | 10 | 58s | revision submitted with **empty** note (unnamed fields) |
| screenshot + pixels only | 40 (cap) | 9m 21s | opened a delete dialog, never submitted |
| **tree + screenshot (hybrid)** | **5** | **59s** | **revision submitted with the client's instruction** |

Labelling each unnamed input by the heading physically above it, and reading back what was
written, is what fixed it. Both survive in the toolset executor: `read_page` prints
`(near: "Proof Title")` on unnamed fields, and `form_input` reports what the field now contains.

## The failure worth remembering

On 303821 with no proof, "print Droid on the chest" produced a reply saying *"I'm getting a
mockup started"* — and nothing was started. The model had no route to create a proof, did the
part it could (pricing), and narrated the part it couldn't as done. Mapping the wizard fixed
the route; the playbook line *"only after you've seen the proof on the deal may you say a
mockup is on the way"* is what makes the claim checkable. Same rule as everywhere else:
verify before you report.

The wizard also refused to list the deal until it was moved to Lead stage. That is a CRM rule,
and it is written in `workplace.md` as a fact, not routed around.

## Memory: transcript only

Sasha remembers one thing between turns: `runs/deal_<id>/transcript.md`, the client ↔ Sasha
messages. Not her tool calls, not the pages she read, not screenshots. Each turn she reads the
thread, then opens the CRM, like a rep.

Why. With the full model history carried forward, once Sasha had said "confirmed" about stock
without checking, every later stock question on that deal repeated the claim, through four
turns and two playbook lines saying to check every time. The model followed its own precedent
in the conversation over the system prompt. Switching to transcript-only memory on the same
deal, same question: it ran the stock checker, 4 steps, and the claim was true. Also ~6× fewer
tokens per turn (97K vs 590K), and the memory is human-readable.

Cost: anything Sasha learned but didn't say is forgotten (a proof ID, a style code she looked
up). She re-reads it. A few extra steps on some turns, not a failure.

## Known gaps

- One browser, one request at a time.
- Session expiry: re-run `auth_setup.py`.
- Reply is returned as text, not sent anywhere.
