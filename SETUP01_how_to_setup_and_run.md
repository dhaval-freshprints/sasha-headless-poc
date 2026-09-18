# Sasha browser POC — setup and run

Sasha drives the Fresh Prints QA CRM in a real browser and writes emails to clients.
This gets it running on your machine with Claude via the Anthropic API and Anthropic's browser toolset.

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

ANTHROPIC_API_KEY=sk-ant-...
MODEL=claude-opus-5
```

`MODEL` must support the browser toolset (`browser_toolset_20260801`): `claude-opus-5`,
`claude-sonnet-5`, `claude-fable-5-1` or `claude-opus-4-8`. To list what your key can see:

```bash
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
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
  [00] navigate {'url': '.../deal?id=303688'} -> Navigated to ...
  [01] read_page {'filter': 'interactive'} -> link "#576394" [ref_12] ...  [6K]
  [02] left_click {'target': {'type': 'ref', 'ref': 'ref_12'}} -> Clicked element ref_12.
  [03] form_input {'target': {...'ref_5'}, 'value': 40} -> Filled ref_5 with 2 characters. Now contains: '40'
  [04] get_page_text {} -> ...
  [05] left_click {'target': {...}} -> Clicked element ref_31.

--- Sasha ---
At 40 bags it's $40.91 each, so $1,636.40 total, with free standard shipping.

Best,
Sasha

[6 steps · 39s (model 22s, browser 17s) · tokens in 106,044 (75,434 cached, 30,610 fresh) · out 903 · images 1, stale refs 0, halts 0]
```

One line per browser call while it runs (several per model turn), then the email, then the cost. The numbers above are illustrative; no toolset turn has been measured yet.

## 7. Where things land

```
runs/deal_<id>/transcript.md              what the client and Sasha said. Sasha's only memory.
runs/deal_<id>/turn_<time>/run.json       every step, tokens, timing
runs/deal_<id>/turn_<time>/batch_NN.png   screenshot after each batch of calls (for you, not the model)
```

To see what Sasha did on a turn, open the screenshots in order.

## 8. Changing how Sasha behaves

Two files, no code:

```
prompts/workplace.md     where things are in the CRM (URLs, field names, what a page shows)
prompts/playbook.md      how Sasha works and writes
```

Edit, save, run again. They're read fresh on every run.

## 9. Running as a server (optional)

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
| `KeyError: 'ANTHROPIC_API_KEY'` | `.env` missing or key blank | fill it in |
| 401 from the model API | wrong key | check `ANTHROPIC_API_KEY` |
| 404 model not found, or 400 mentioning `browser_toolset` | model name wrong, or it doesn't support the toolset | use one of the models listed in step 3 |
| result says `ref_N is stale` often | the page re-rendered under the model | expected now and then; if every turn, look at the screenshots |
| `playwright` can't find chromium | step 2, last line skipped | `.venv/bin/playwright install chromium` |
| A turn hits 40 model turns and stops | model is stuck on a page it can't read | look at the screenshots; usually a map gap in `workplace.md` |
