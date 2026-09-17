# Sasha browser POC — setup and run

Sasha drives the Fresh Prints QA CRM in a real browser and writes emails to clients.
This gets it running on your machine with Claude via the Anthropic API.

Needs: Python 3.12+, an Anthropic API key, the QA CRM login.

## 1. Get the code

```bash
cd sasha-browser-poc
```

## 2. Python environment

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
```

The last line downloads the browser Playwright drives (~150 MB, once).

## 3. Configuration

```bash
cp .env.example .env
```

Open `.env` and set these:

```
FP_PASSWORD=<QA CRM password for qatest@yopmail.com>

LLM_PROVIDER=anthropic
LLM_BASE_URL=
LLM_API_KEY=sk-ant-...
MODEL=<a current Claude model name>
```

Leave `LLM_BASE_URL` empty; the Anthropic SDK uses its default endpoint. For `MODEL`, use
the newest Opus-class model that supports tool use and images. Get the exact name from
Anthropic's model list rather than guessing; names change:

```bash
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $LLM_API_KEY" -H "anthropic-version: 2023-06-01" \
  | python3 -c "import json,sys; print('\n'.join(m['id'] for m in json.load(sys.stdin)['data']))"
```

Everything else in `.env.example` can stay as it is.

## 4. Log in to the CRM (once)

```bash
.venv/bin/python auth_setup.py
```

A browser window opens, logs in with the credentials from `.env`, and saves the session
to `./auth/`. Every later run starts logged in. If a run later says it's on the login page,
run this again; the QA session expires now and then.

## 5. Run a turn

Pick a deal id from the QA sales pipeline (`https://v4-qa.internal-fp.com/dashboard/sales-pipeline`).

First message to the client (Sasha reads the deal, writes the intro):

```bash
.venv/bin/python run_cli.py <deal_id> --once
```

Client replied (Sasha reads the deal, does what's needed in the CRM, replies):

```bash
.venv/bin/python run_cli.py <deal_id> -m "What's the price for 30?"
```

Interactive, one client message after another:

```bash
.venv/bin/python run_cli.py <deal_id>
```

Watch the browser instead of running headless:

```bash
.venv/bin/python run_cli.py <deal_id> -m "..." --headed
```

Forget the conversation so far and start the deal over:

```bash
.venv/bin/python run_cli.py <deal_id> --reset --once
```

## 6. What you'll see

```
[deal 303688 · client reply] working...
  [00] navigate {...} -> Now at .../deal?id=303688  [tree 6K]
  [01] click {'role': 'link', 'name': '#576394'} -> ...
  [02] fill_field {'field': '#5', 'text': '40'} -> ...
  [03] click {'role': 'button', 'name': 'Cancel'} -> ...

--- Sasha ---
At 40 bags it's $40.91 each, so $1,636.40 total, with free standard shipping.

Best,
Sasha

[4 steps · 39s (model 22s, browser 17s) · tokens in 106,044 (75,434 cached, 30,610 fresh) · out 903]
```

One line per browser action while it runs, then the email, then the cost.

## 7. Where things land

```
runs/deal_<id>/transcript.md              what the client and Sasha said. Sasha's only memory.
runs/deal_<id>/turn_<time>/run.json       every step, tokens, timing
runs/deal_<id>/turn_<time>/step_NN.png    screenshot after each step
```

To see what Sasha did on a turn, open the screenshots in order.

## 8. Changing how Sasha behaves

Two files, no code:

```
prompts/workplace.md     where things are in the CRM (URLs, field names, what a page shows)
prompts/playbook.md      how Sasha works and writes
```

Edit, save, run again. They're read fresh on every run.

## 9. Switching model provider

Same code, one setting:

| Want | `.env` |
|---|---|
| Claude via Anthropic | `LLM_PROVIDER=anthropic`, `LLM_BASE_URL=` empty, Anthropic key, Claude model name |
| GPT via OpenAI | `LLM_PROVIDER=openai`, `LLM_BASE_URL=` empty, OpenAI key, GPT model name |
| Internal gateway | `LLM_PROVIDER=gateway`, `LLM_BASE_URL=http://192.168.29.70:8317/v1`, gateway key, any model it lists |

## 10. Running as a server (optional)

```bash
.venv/bin/uvicorn api:app --port 8100
```

```
POST /simulate            {"deal_id": 303688, "client_message": "price for 30?"}   (omit client_message for outreach)
POST /reset               {"deal_id": 303688}
GET  /transcript/303688
```

Or with Docker: `docker compose up --build`. Copy `./auth/` in first, or run `auth_setup.py` inside the container with a display.

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| Sasha says she's on the login page | QA session expired | `auth_setup.py` again |
| `KeyError: 'LLM_API_KEY'` | `.env` missing or key blank | fill it in |
| 401 from the model API | wrong key for the provider | check `LLM_PROVIDER` matches the key |
| 404 model not found | model name wrong for that provider | check the provider's model list |
| `playwright` can't find chromium | step 2, last line skipped | `.venv/bin/playwright install chromium` |
| A turn hits 40 steps and stops | model is stuck on a page it can't read | look at the screenshots; usually a map gap in `workplace.md` |
