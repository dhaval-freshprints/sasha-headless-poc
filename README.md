# Sasha Browser POC

Sasha as a browser-driving sales rep. One model, one loop, hybrid browser control (accessibility tree + screenshot), no business rules in the prompt.
Runs against Fresh Prints **QA** (`v4-qa.internal-fp.com`).

```
deal_id (+ optional client message)
        │
        ▼
   brain.py   — the loop: model picks an action, sees the result, repeats, then replies
        │
        ▼
   browser.py — logged-in headless Chromium
              sees: accessibility tree + EDITABLE FIELDS list + screenshot, every step
              acts: click / click_text / fill_field by name · click_at / type_here by pixel
        │
        ▼
   QA CRM / quoter / proofs
```

## Files

| File | Job |
|---|---|
| `config.py` | settings from `.env` |
| `browser.py` | Browser Hands — hybrid tools (by name first, by coordinate as fallback) |
| `brain.py` | Sasha Brain — the loop + system prompt |
| `prompts/workplace.md` | the map: pages, URLs, how each form works. Facts only, no opinions |
| `prompts/playbook.md` | how Sasha works and writes: judgment, voice, outreach, client reply |
| `run_cli.py` | terminal runner |
| `api.py` | `POST /simulate`, `POST /reset` |
| `auth_setup.py` | one-time login, saves session to `./auth` |

## Setup

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
python -m playwright install chromium

cp .env.example .env     # fill FP_PASSWORD and LLM_API_KEY
python auth_setup.py     # logs in, saves ./auth
```

## Model

Any OpenAI-compatible endpoint. Set `LLM_BASE_URL`, `LLM_API_KEY`, `MODEL` in `.env`.
List what the endpoint offers: `curl -H "Authorization: Bearer $LLM_API_KEY" $LLM_BASE_URL/models`.

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

Every turn writes `runs/deal_<id>/<timestamp>/run.json` plus one screenshot per step.

## Docker

```bash
python auth_setup.py     # on the host first, so ./auth exists
docker compose up --build
```

## Prompts

Exactly two, both in the system message, cached (one `cache_control` marker; ~90% cache hit
rate measured through the gateway):

- **workplace.md** — where things are. A line belongs here if it has a URL, a button name or
  a field label in it. If it needs "always", "never" or "prefer", it doesn't.
- **playbook.md** — what to do. If a section ever needs a priority order to resolve conflicts
  with another section, something has gone wrong.

When the model has to explore a page (the quoter, first time: 11 steps), a human writes what it
found into `workplace.md`. Policy does not go in prompts; it goes in the CRM's own forms or in code.

## Results so far (QA)

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

## Why hybrid

Same task ("change the text to Welcome"), same model, three tool designs:

| Tool design | Steps | Time | Result |
|---|---|---|---|
| accessibility tree only | 10 | 58s | revision submitted with **empty** note (unnamed fields) |
| screenshot + pixels only | 40 (cap) | 9m 21s | opened a delete dialog, never submitted |
| **tree + screenshot (hybrid)** | **5** | **59s** | **revision submitted with the client's instruction** |

The `EDITABLE FIELDS` list labels each input by the heading physically above it, and
`fill_field` reads back what it wrote. That is what fixed it.

## The failure worth remembering

On 303821 with no proof, "print Droid on the chest" produced a reply saying *"I'm getting a
mockup started"* — and nothing was started. The model had no route to create a proof, did the
part it could (pricing), and narrated the part it couldn't as done. Mapping the wizard fixed
the route; the playbook line *"only after you've seen the proof on the deal may you say a
mockup is on the way"* is what makes the claim checkable. Same rule as everywhere else:
verify before you report.

The wizard also refused to list the deal until it was moved to Lead stage. That is a CRM rule,
and it is written in `workplace.md` as a fact, not routed around.

## Known gaps

- Conversation history is in memory; restarting forgets it.
- One browser, one request at a time.
- Session expiry: re-run `auth_setup.py`.
- Reply is returned as text, not sent anywhere.
