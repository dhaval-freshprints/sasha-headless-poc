# Sasha Browser POC — Architecture Walkthrough

A guide for explaining this project to the team. Every claim below cites a file and line.
Verified against the working tree at commit `75d3dbb`.

---

## 1. The one-sentence version

Sasha is a sales rep that does her job **by driving a real logged-in browser**, not by calling
APIs. One LLM loop picks a browser action, sees the result, and repeats until it writes a reply
to the client.

Total application code: **1,119 lines** across 8 Python files and 2 prompt files
(`wc -l *.py prompts/*.md`).

---

## 2. The five moving parts

| Part | File | Lines | Job |
|---|---|---|---|
| Config | `config.py` | 31 | Read `.env`, build deal URLs |
| Browser Hands | `browser.py` | 254 | See the page, act on the page |
| Brain | `brain.py` | 312 | The loop + tool definitions + system prompt |
| Memory | `memory.py` | 42 | Per-deal transcript on disk |
| Prompts | `prompts/*.md` | 192 | The map (workplace) + the judgment (playbook) |
| Entry points | `run_cli.py`, `api.py` | 174 | Terminal runner, HTTP server |
| Reporting | `report.py` | 78 | Roll up all runs into a table |

### How they depend on each other

```
run_cli.py ──┐
             ├──> Brain(browser, run_dir).run(deal_id, client_message)
api.py    ───┘         │
                       ├──> browser.py   (Playwright / Chromium)
                       ├──> memory.py    (transcript.md)
                       ├──> config.py    (.env)
                       └──> OpenAI SDK   (any OpenAI-compatible endpoint)
```

Both entry points do the **same three things** (`run_cli.py:66-78`, `api.py:52-57`):
create a timestamped `run_dir`, construct a `Brain`, call `.run()`. The CLI prints the result;
the API returns it as JSON. There is no logic difference between them.

---

## 3. The core loop — the thing to explain first

Everything else is support. The loop is `Brain.run()` at `brain.py:143-200`.

```
for step_index in range(MAX_STEPS):        # brain.py:151, MAX_STEPS=40 (config.py:25)
    response = model.chat.completions(...)  # brain.py:153
    if no tool calls:  nudge once, else stop
    for each tool call:
        if reply_to_client:  capture reply, finish   # brain.py:172-177
        output = self._execute(name, args)           # brain.py:180
        tree    = browser.snapshot()                 # brain.py:181
        shot    = browser.save_screenshot(...)       # brain.py:182
        feed back: tool result = "output + tree", then a separate image message
```

**The key design decision is on `brain.py:187-188`:** after every single action the model gets
**two** messages back — the tool result carrying the accessibility tree, and a second user
message carrying the screenshot. Two senses, every step.

### Why two senses (this is the headline result)

The README reports the same task ("change the text to Welcome"), same model, three tool designs:

| Tool design | Steps | Time | Result |
|---|---|---|---|
| accessibility tree only | 10 | 58s | revision submitted with **empty** note |
| screenshot + pixels only | 40 (cap) | 9m 21s | opened a delete dialog, never submitted |
| **tree + screenshot** | **5** | **59s** | **correct** |

*(Source: `README.md` "Why hybrid" table. These are the author's recorded measurements, not
something I re-ran.)*

The tree gives **exact names**; the screenshot gives **layout and canvas**. Neither alone is
enough for a real CRM.

### The termination guarantee

The loop can end three ways, all in `brain.py:160-192`:
1. Model calls `reply_to_client` → `finished = True` → break (`brain.py:175-177`)
2. Model returns text with no tool call → gets **one** `NUDGE` (`brain.py:161-164`), then stops
3. `MAX_STEPS` (40) is exhausted

The nudge is fired at most once, guarded by `_nudged()` (`brain.py:299`), which scans the message
list for the NUDGE string. Worth calling out: this prevents an infinite "you forgot to reply" loop.

---

## 4. Browser Hands — 12 tools, two tiers

Tools are declared once in `TOOL_SPECS` (`brain.py:64-92`) and dispatched by a `match` statement
in `_execute` (`brain.py:206-233`). Adding a tool means touching exactly those two places.

**Tier 1 — act by name (preferred, exact):**
`navigate`, `click` (ARIA role + name), `click_text`, `fill_field`, `select_option`, `press_key`

**Tier 2 — act by pixel (fallback, for canvas and icon-only controls):**
`click_at`, `type_here`

**Sense / control:** `look`, `read_text`, `scroll`, `reply_to_client`

The system prompt tells the model the tier order explicitly (`brain.py:36-40`):
*"Use `click_at` / `type_here` with screenshot coordinates only when nothing in the tree matches."*

### The trick that made `fill_field` work

Ordinary accessibility trees leave many CRM inputs unnamed. So `browser.py` builds its own
**EDITABLE FIELDS** list (`browser.py:_editables`, lines 178-216) using injected JavaScript that
finds, for each visible input, *the closest short text physically above it* — filtering for
elements that are leaf nodes, visible, not covered by another element, under 60 chars, and
vertically within 90px with horizontal overlap (`browser.py:191-205`).

Then `fill_field` **reads back what it wrote** (`browser.py:126`):

```python
return f"Filled '{field}' with {len(text)} characters. Now contains: {self._value_of(target)!r}"
```

The model sees the field's actual new content, so a silently-failed write is visible immediately.
Fields can also be addressed positionally as `#3` (`browser.py:169-172`).

### Small robustness details worth a mention

- `page` always returns **the newest tab** (`browser.py:44-47`), so a button that opens a tab
  doesn't strand the model.
- `_settle()` (`browser.py:249-254`) waits 700ms then up to 4s for `networkidle`, swallowing
  timeouts. It runs after every action.
- `_dismiss_toasts()` (`browser.py:228-237`) closes notification banners because they cover
  controls in screenshots.
- `_not_found()` (`browser.py:244-246`) returns an actionable message that tells the model to
  re-snapshot or fall back to coordinates — errors are teaching messages, not stack traces.
- `_execute` wraps everything in try/except and returns `ERROR: <type>: <msg[:300]>`
  (`brain.py:234-235`). A browser crash becomes an observation, not a dead run.

---

## 5. Prompts — a deliberate two-file split

Both files load at import time (`brain.py:23-24`) and are concatenated into one system message
with **one** cache marker (`brain.py:51-55`):

```python
SYSTEM_PROMPT = f"{IDENTITY}\n\n---\n\n{WORKPLACE}\n\n---\n\n{PLAYBOOK}"
SYSTEM_MESSAGE = {"role": "system", "content": [{..., "cache_control": {"type": "ephemeral"}}]}
```

Because this block is byte-identical across every turn and every deal, it is a cache hit after
the first call. A real run bears this out: `runs/deal_303827/turn_20260917_172828/run.json`
shows `input_tokens: 387,794` with `cached_tokens: 327,018` — **84% cached on that turn.**

**The split rule (`prompts/workplace.md:3-4`, `prompts/playbook.md:3-4`):**

- **workplace.md** = facts. A line belongs here if it has a URL, a button name, or a field label.
  *"If a line needs the words 'always', 'never' or 'prefer', it belongs in playbook.md instead."*
- **playbook.md** = judgment. *"If a section needs a priority order to resolve conflicts with
  another section, something has gone wrong."*

This is the most transferable idea in the repo: **map and judgment are different kinds of
knowledge with different change rates.** The map changes when the CRM ships a new page; the
judgment changes when sales policy changes.

Note the stated principle "no business rules in the prompt" (`brain.py:5`) is about *policy*
(pricing rules, discounts) living in the CRM's own forms. The playbook does contain operating
rules — e.g. `playbook.md:12` *"After any write, look again and confirm it saved."*

---

## 6. Memory — transcript only, and why

`memory.py` is 42 lines and stores exactly one thing per deal: `runs/deal_<id>/transcript.md`,
the client ↔ Sasha messages (`memory.py:31-38`).

Each turn starts fresh (`brain.py:204-215`): system prompt, then the transcript as a single user
message, then a canned assistant acknowledgement. **No tool calls, page trees, or screenshots
from earlier turns are carried forward.**

### The bug that caused this design

From `README.md` "Memory: transcript only" — with full model history carried forward, once Sasha
had said "confirmed" about stock **without checking**, every later stock question on that deal
repeated the claim, through **four turns and two playbook lines** telling her to check every time.

> *"The model followed its own precedent in the conversation over the system prompt."*

Switching to transcript-only memory, same deal, same question: she ran the stock checker, 4 steps,
claim was true. Also ~6× fewer tokens per turn (97K vs 590K).

This is the single best story for the team. **A wrong claim in conversation history outweighs a
correct rule in the system prompt.** The fix was architectural (drop the history), not more
prompting. It matches commit `b9df433` *"Memory is the transcript only; drop full model history
between turns."*

**The tradeoff, stated honestly in the README:** anything Sasha learned but didn't say is
forgotten (a proof ID, a style code). She re-reads it. A few extra steps on some turns.

---

## 7. Observability

Every turn writes `runs/deal_<id>/turn_<timestamp>/run.json` (`brain.py:255-269`) plus one PNG
per step. Verified on disk — that run.json has keys:
`deal_id, client_message, reply, seconds, model_seconds, browser_seconds, input_tokens,
cached_tokens, uncached_tokens, output_tokens, steps`, and each step has
`index, tool, args, result, screenshot, tree_chars, tree_head`.

Two things this buys you:

- **Time is split** into `model_seconds` vs `browser_seconds` (`brain.py:152-183`), so you can
  tell whether a slow turn was the LLM or the page.
- **`tree_chars` + `tree_head`** (`brain.py:99-100`) record how big the tree was and its first 12
  lines, so you can see what the model actually saw without storing megabytes.

`report.py` rolls every `run.json` into one table with totals and averages
(`python report.py`, `python report.py --csv`). **It is not mentioned in the README** — worth
adding, since it is how you'd measure any change you make.

---

## 8. Deployment

- `auth_setup.py` (36 lines) runs **headed**, logs in once, saves a persistent Chromium profile
  to `./auth` (`auth_setup.py:16-32`). Every later run launches with
  `launch_persistent_context(user_data_dir=AUTH_DIR)` (`browser.py:27-33`) and is already logged in.
- `Dockerfile` uses `mcr.microsoft.com/playwright/python:v1.63.0-noble` — Chromium and OS deps
  are already in the image.
- `docker-compose.yml` mounts `./auth` and `./runs`, and sets `shm_size: "1gb"` with the comment
  *"Chromium needs more than Docker's 64MB default."*
- `.env.example` points at QA: `FP_BASE_URL=https://v4-qa.internal-fp.com`.

---

## 9. Known gaps — say these out loud

From the README's own "Known gaps":
- One browser, one request at a time.
- Session expiry: re-run `auth_setup.py`.
- Reply is returned as text, **not sent anywhere**.

Confirmed in code, and worth adding:

1. **No concurrency guard.** `api.py:28` holds a single module-level `_browser`, and there is no
   lock anywhere in `api.py` (verified by grep). Two simultaneous `POST /simulate` calls would
   drive the *same* browser. Fine for a POC; a hard blocker for multi-user.
2. **`Meta+a` is macOS-only.** `browser.py:117` uses `Meta+a` to select-all before typing. Inside
   the Linux Docker image that is the wrong modifier (`Control+a`). **(Assumption)** this makes
   `fill_field` append instead of replace under Docker — I did not run the container to confirm.
   Testing `fill_field` in Docker would settle it.
3. **Screenshots can overwrite within a turn.** The filename is `step_{step_index:02d}.png`
   (`brain.py:182`) using the *loop* index, but the inner loop iterates over all tool calls in one
   response (`brain.py:170`). If the model returns two tool calls at once, the second overwrites
   the first's PNG. The `Step` records still both exist in `run.json`.
4. **`history.json` is dead.** `memory.py:39` deletes it on `clear()`, but nothing writes it
   (verified: `grep -rn "history.json" --include=*.py` returns only that one line). Leftover from
   the pre-`b9df433` design; files remain in older run dirs.

---

## 10. Suggested 10-minute talk track

1. **The premise** — no API integration; she uses the CRM the way a rep does. (30s)
2. **Show the loop** — `brain.py:151-192`. Act → tree + screenshot → repeat → reply. (2 min)
3. **Why hybrid** — the three-design table. Tree-only submitted an empty form; pixels-only hit
   the step cap. (2 min)
4. **The EDITABLE FIELDS trick** — label each input by the text physically above it, and read back
   what you wrote. (2 min)
5. **The memory story** — a wrong "confirmed" in history beat two playbook lines across four
   turns; deleting history fixed it. (2 min)
6. **Prompt split** — map vs judgment, and the test for which file a line goes in. (1 min)
7. **Gaps** — single browser, no lock, reply goes nowhere. (30s)

If you only have 3 minutes: **the loop**, **why hybrid**, **the memory story**.

---

## Assumptions in this document

- **`Meta+a` breaks `fill_field` in Docker** (§9.2). Based on reading `browser.py:117` plus the
  Linux base image in the `Dockerfile`. I did not run the container. Running one `fill_field`
  call in Docker confirms or breaks it.
- **The README's benchmark tables** (step counts, timings, the three-design comparison) are the
  author's recorded measurements. I verified one run.json's token numbers directly; I did not
  re-run any task.

Everything else above is cited to a file and line I read.
