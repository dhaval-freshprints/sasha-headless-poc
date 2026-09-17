# Sasha Browser POC

Sasha as a browser-driving sales rep. One model, one loop, six browser actions, no business rules in the prompt.
Runs against Fresh Prints **QA** (`v4-qa.internal-fp.com`).

```
deal_id (+ optional client message)
        │
        ▼
   brain.py   — the loop: model picks an action, sees the result, repeats, then replies
        │
        ▼
   browser.py — logged-in headless Chromium: navigate · snapshot · click · type · select · screenshot
        │
        ▼
   QA CRM / quoter / proofs
```

## Files

| File | Job |
|---|---|
| `config.py` | settings from `.env` |
| `browser.py` | Browser Hands — the six tools |
| `brain.py` | Sasha Brain — the loop + system prompt |
| `prompts/initial_outreach.md` | wording rules for the first outreach (adapted from the prod LangSmith prompt) |
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

## Results so far (QA)

| Deal | Task | Model | Steps | Tokens in | Time |
|---|---|---|---|---|---|
| 302886 | follow-up on stalled deal | claude-sonnet-5 | 2 | 16K | 27s |
| 303688 | "price for 50?" — used proof qty field, didn't save | claude-sonnet-5 | 8 | 72K | ~70s |
| 303817 | initial outreach | claude-sonnet-5 | 15 | 228K | ~90s |
| 303817 | initial outreach | claude-opus-5 | 4 | 25K | 28s |

## Known gaps

- Conversation history is in memory; restarting forgets it.
- One browser, one request at a time.
- Session expiry: re-run `auth_setup.py`.
- Reply is returned as text, not sent anywhere.
