# OpenAI-managed Sasha POC

Sasha handles one Fresh Prints QA sales turn through an OpenAI managed agent
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
              +--> sign in to Fresh Prints QA
              +--> start a disposable Docker browser environment
              +--> connect the OpenAI managed agent session
              +--> collect structured result JSON and artifacts
```

Every turn uses a new container and an empty browser profile. The runner signs
in before the agent starts, passes credentials to the fixed login script over
standard input, and removes the container when the turn ends.

## Requirements

- Python 3.12 or newer
- Docker
- An OpenAI application API key
- An OpenAI executor API key
- Fresh Prints QA credentials

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

Fill in the required values in `.env`, then build the disposable executor
image:

```bash
docker build -t sasha-openai-managed:local .
```

## Run

Generate initial outreach:

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

Deal records and web job state are written under `runs/webapp/deals/` and
`runs/webapp/jobs/`. Existing job records are automatically added to the deal
list. If the web server restarts, completed jobs remain viewable. A job that was
queued or running when the server stopped is marked failed because its original
background worker no longer exists.

## Time limit

Sasha may work for up to 20 minutes per managed turn. Change
`OPENAI_MANAGED_TURN_TIMEOUT_SECONDS` if a different limit is required.
Container preparation, QA authentication, artifact collection, and cleanup are
outside that managed-turn limit.

## Output

Run artifacts are stored under `runs/openai-managed/`. Each completed turn also
updates the deal conversation at:

```text
runs/openai-managed/conversations/DEAL<deal_id>_conversation.json
```

Downloaded client artwork is removed after the turn.

## OpenAI-managed source

- `openai_managed/runner.py`: session lifecycle and result collection
- `openai_managed/sandbox.py`: disposable Docker environment
- `openai_managed/setup_auth.js`: QA authentication inside the container
- `openai_managed/conversation.py`: per-deal conversation history
- `openai_managed/attachments.py`: temporary client artwork downloads
- `openai_managed/SASHA01_agent_instructions.md`: stable agent boundaries
- `openai_managed/capabilities/sasha-sales/`: Sasha skill and references
- `scripts/run_openai_managed_sasha.py`: CLI entry point

## Tests

The tests do not make live OpenAI calls or start a real browser:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m unittest discover -s webapp/tests -t . -v
```
