"""
Sasha Brain: the loop.

Each model turn returns a batch of browser tool calls. We run them in order, stop at the
first failure, and send every result back together. The model decides when to look
(screenshot, read_page, get_page_text); we do not push a view after every action.
No business rules in the system prompt: the CRM, quoter and forms already enforce them.
"""

import hashlib
import json
from importlib.metadata import version
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import memory
import product_memory
from browser import Browser, VIEWPORT
from llm import LLM, ToolCall
from reply_preflight import validate_reply
from toolset_executor import NOT_EXECUTED, PageStillProcessing, StaleRef, ToolsetExecutor, stale_ref_message
from run_limits import RunLimits
from tool_policy import DISABLED_MEMBERS, arrow_press_count, validate_tool_call

WORKPLACE = (config.ROOT / "prompts" / "workplace.md").read_text()
PLAYBOOK = (config.ROOT / "prompts" / "playbook.md").read_text()

IDENTITY = f"""You are Sasha, a sales representative at Fresh Prints (custom apparel).

You are logged into the Fresh Prints CRM in a browser and you drive it with the browser
tools. The viewport is {VIEWPORT["width"]}x{VIEWPORT["height"]} px. Read the page before you
act on it, and look again after any write to confirm what saved. `get_page_text` gives the
visible text with control-state annotations; use it to read prices and numbers. Fill fields with `form_input`: it
also handles search boxes and dropdowns that filter as you type, and picks the matching
option for you. Act, then observe the relevant result. A tool can report that the page is
still processing; inspect again before dependent actions. Do not add fixed waits.
For a generic catalog option, call `inspect_catalog_product` on its exact style before leaving
the catalog. On the quoter, use `get_page_text` after the final inputs settle and read the exact
selected style, color, quantity, price, tax, shipping and delivery before quoting the client.
Use at most three small arrow-key calls before requesting a screenshot and looking at it
on the next model turn. Stop after two attempts show no movement; inspect selection,
focus and the print region instead of repeating keys. A pressed key does not prove movement.

Below are two references. WORKPLACE is the map of the CRM: where things are and how they
work. PLAYBOOK is how you work and write. When you're done, call `reply_to_client` with
the message to the client, written as HTML (see PLAYBOOK, Format).
"""

# One system prompt, three sections. Identical across every turn and every deal, so the
# whole block is a cache hit after the first call.
SYSTEM_PROMPT = f"{IDENTITY}\n\n---\n\n{WORKPLACE}\n\n---\n\n{PLAYBOOK}"

NUDGE = (
    "That came back as plain text, not a tool call, so nothing was executed. If you meant to act, "
    "call the tool. When the work is done, call `reply_to_client` with your message to the client; "
    "if you could not complete something, say so in the message."
)
MAX_NUDGES = 3
NO_REPLY = "(no reply was sent: the turn ended without reply_to_client)"
INCOMPLETE_REPLY = (
    "I couldn't finish and verify the update. Some changes may already have saved, "
    "so the proof needs to be checked before another change is made.\n\nBest,\nSasha"
)
VERIFICATION_NOTE = (
    "The work budget is reached. Do not make further changes or retry a save. "
    "You have a short read-only window to inspect the current page and reopen the proof "
    "using navigate/switch_tab. Then reply with only what you verified; explain anything unfinished."
)


@dataclass
class Step:
    index: int
    batch: int           # which model turn this call came from
    tool: str
    args: dict
    result: str          # the tool's own result, or "[image]" for screenshots
    is_error: bool
    screenshot: str      # the debug PNG taken after this batch (for humans, never sent to the model)
    tree_chars: int = 0  # size of a read_page / find result, so run.json shows what the model saw
    seconds: float = 0.0
    timings: dict = field(default_factory=dict)
    outcome: str = "executed"


@dataclass
class RunResult:
    reply: str
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    model_seconds: float = 0.0    # time spent waiting on the LLM
    browser_seconds: float = 0.0  # time spent in browser actions
    screenshots_sent: int = 0     # images the model asked for
    stale_refs: int = 0
    batch_halts: int = 0
    recording_seconds: float = 0.0
    model_calls: list[dict] = field(default_factory=list)
    recordings: list[dict] = field(default_factory=list)
    catalog_products: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    stop_reason: str = ""

    @property
    def uncached_tokens(self) -> int:
        return self.input_tokens - self.cached_tokens

    def summary(self) -> str:
        """One line: what this turn cost in time and tokens."""
        return (
            f"{len(self.steps)} steps · {self.seconds:.0f}s "
            f"(model {self.model_seconds:.0f}s, browser {self.browser_seconds:.0f}s) · "
            f"tokens in {self.input_tokens:,} "
            f"({self.cached_tokens:,} cached, {self.uncached_tokens:,} fresh) · "
            f"out {self.output_tokens:,} · "
            f"images {self.screenshots_sent}, stale refs {self.stale_refs}, halts {self.batch_halts} "
            f"· recording {self.recording_seconds:.1f}s · stopped: {self.stop_reason or 'completed'}"
        )


class Brain:
    def __init__(self, browser: Browser, run_dir: Path, on_event=None):
        """
        on_event(kind, payload) is called as the turn progresses, so a caller can show
        progress. kind is "thinking" (payload: batch index) or "step" (payload: Step).
        """
        self.browser = browser
        self.run_dir = run_dir
        self.on_event = on_event or (lambda kind, payload: None)
        self.llm = LLM()
        self.executor = ToolsetExecutor(browser)

    def run(self, deal_id: int, client_message: str | None, attachments=None) -> RunResult:
        self.deal_id = deal_id
        self.prior_products = product_memory.load(deal_id)
        self.executor.attachments = attachments
        messages = self._start_messages(deal_id)
        messages.append(self.llm.user_message(self._build_turn_text(deal_id, client_message, attachments)))
        result = RunResult(reply="", metadata=_run_metadata())
        self.limits = RunLimits()
        self.movement_calls = 0
        self.movement_presses = 0
        self.movement_observed = False
        nudges = 0
        verification_announced = False

        # Reserve two model turns for checking an in-flight save and reporting the result.
        for batch in range(config.MAX_BATCHES + 2):
            if batch >= config.MAX_BATCHES:
                self.limits.start_verification("batch_limit")
            self.limits.update()
            if self.limits.reason and not verification_announced:
                messages.append(self.llm.user_message(VERIFICATION_NOTE))
                verification_announced = True
            if self.limits.seconds_left() <= 0:
                break
            reply = self._call_model(batch, messages, result)
            if reply is None:
                break
            messages.append(self.llm.assistant_message(reply))
            if not reply.tool_calls:
                if nudges >= MAX_NUDGES:
                    result.stop_reason = "missing_tool_call"
                    break
                messages.append(self.llm.user_message(NUDGE))
                nudges += 1
                continue
            blocks, finished = self._run_batch(batch, reply.tool_calls, result)
            self._record_batch_image(batch, result)
            messages.append(self.llm.results_message(blocks))
            if finished:
                break

        if not result.reply:
            result.reply = INCOMPLETE_REPLY
            result.stop_reason = result.stop_reason or self.limits.reason or "batch_limit"
        elif self.limits.reason:
            result.stop_reason = self.limits.reason
        else:
            result.stop_reason = "completed"
        result.seconds = time.monotonic() - self.limits.started
        result.metadata["actions_reserved"] = self.limits.actions
        result.metadata["verification_actions"] = self.limits.verification_actions
        product_memory.save_verified(deal_id, result.catalog_products)
        memory.append_transcript(deal_id, client_message, result.reply)
        self._write_run_json(deal_id, client_message, result)
        return result

    def _call_model(self, batch: int, messages: list[dict], result: RunResult):
        self.on_event("thinking", batch)
        started = time.monotonic()
        record = {"batch": batch}
        try:
            reply = self.llm.send(SYSTEM_PROMPT, messages, timeout_seconds=self.limits.seconds_left())
        except Exception as error:
            record["error_type"] = type(error).__name__
            result.stop_reason = "model_error"
            return None
        else:
            result.input_tokens += reply.input_tokens
            result.output_tokens += reply.output_tokens
            result.cached_tokens += reply.cached_tokens
            record.update(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                          cached_tokens=reply.cached_tokens, request_id=reply.request_id,
                          stop_reason=reply.stop_reason)
            return reply
        finally:
            record["seconds"] = time.monotonic() - started
            result.model_seconds += record["seconds"]
            result.model_calls.append(record)

    # ---- one batch ----------------------------------------------------------

    def _run_batch(self, batch: int, calls: list[ToolCall], result: RunResult) -> tuple[list[dict], bool]:
        if self.movement_observed:
            self.movement_calls = 0
            self.movement_presses = 0
            self.movement_observed = False
        blocks = []
        blocked = ""
        finished = False
        for call in calls:
            if blocked or finished:
                content = blocked or "Not executed: the final reply has already been produced."
                blocks.append(self._result_block(call, content, True))
                self._record(batch, call, content, True, result, outcome="not_executed")
                continue
            if call.name == "reply_to_client" and not call.is_browser:
                blocks.append(self._handle_reply(call, result))
                finished = bool(result.reply)
                continue
            content, is_error, seconds, timings, outcome = self._execute(call, result)
            blocks.append(self._result_block(call, content, is_error))
            self._record(batch, call, content, is_error, result, seconds, timings, outcome)
            if is_error:
                result.batch_halts += 1
                blocked = NOT_EXECUTED
        return blocks, finished

    def _result_block(self, call: ToolCall, content, is_error: bool) -> dict:
        if call.is_browser:
            return self.llm.browser_result(call.id, content, is_error)
        return self.llm.tool_result(call.id, _content_as_text(content), is_error)

    def _movement_checkpoint(self, call: ToolCall) -> str:
        presses = arrow_press_count(call.name, call.args)
        if not presses:
            return ""
        if self.movement_calls >= 3 or self.movement_presses + presses > config.MAX_KEY_REPEAT:
            return (
                "Needs observation: no more movement was executed. Request screenshot or zoom, "
                "then inspect it on the next turn before another small movement. "
                "If two attempts did not move the object, correct selection/focus/print region instead."
            )
        self.movement_observed = False
        self.movement_calls += 1
        self.movement_presses += presses
        return ""

    def _execute(self, call: ToolCall, result: RunResult):
        started = time.monotonic()
        timings = {}
        outcome = "rejected"
        try:
            if not call.is_browser and not call.is_attach and not call.is_local_read:
                raise ValueError(f"Unknown tool: {call.name}")
            validate_tool_call(call.name, call.args)
            denied = self.limits.reserve_action(call.name)
            if denied:
                return denied, True, 0.0, {}, "budget_rejected"
            checkpoint = self._movement_checkpoint(call)
            if checkpoint:
                return checkpoint, True, 0.0, {}, "needs_observation"
            outcome = "executed"
            try:
                content = self.executor.run(call.name, call.args)
                if call.is_local_read:
                    content = self._capture_local_read(call.name, content, result)
            finally:
                timings = dict(self.executor.last_timings)
            if call.name in ("screenshot", "zoom"):
                result.screenshots_sent += 1
                self.movement_observed = True
            is_error = False
        except StaleRef as stale:
            result.stale_refs += 1
            content, is_error = stale_ref_message(str(stale)), True
            outcome = "error"
        except PageStillProcessing as pending:
            content, is_error = str(pending), True
            outcome = "pending"
        except Exception as error:
            content, is_error = f"Error: {type(error).__name__}: {str(error)[:300]}", True
            if outcome == "executed":
                outcome = "error"
        seconds = time.monotonic() - started
        result.browser_seconds += seconds
        return content, is_error, seconds, timings, outcome

    def _record_batch_image(self, batch: int, result: RunResult) -> None:
        steps = [step for step in result.steps
                 if step.batch == batch and step.outcome in ("executed", "error", "pending")]
        if not steps:
            return
        started = time.monotonic()
        record = {"batch": batch}
        try:
            path = self.browser.save_screenshot(self.run_dir / f"batch_{batch:02d}.png")
            record.update(self.browser.last_capture)
            for step in steps:
                step.screenshot = str(path)
        except Exception as error:
            record["error_type"] = type(error).__name__
        finally:
            elapsed = time.monotonic() - started
            record["recording_seconds"] = elapsed
            result.recording_seconds += elapsed
            result.browser_seconds += elapsed  # Preserve the old aggregate's meaning.
            result.recordings.append(record)

    def _handle_reply(self, call: ToolCall, result: RunResult) -> dict:
        text = self._reply_text(call.args)
        if not text:
            return self.llm.tool_result(call.id, "The message was empty. Call reply_to_client with message text.", True)
        catalog_required = any(
            step.tool == "navigate" and "freshprints.com/products" in str(step.args.get("url", ""))
            for step in result.steps
        )
        errors = validate_reply(
            text,
            result.catalog_products,
            catalog_required,
            self.prior_products,
        )
        if errors:
            detail = " ".join(errors)
            return self.llm.tool_result(
                call.id,
                f"Reply not sent. Correct these evidence problems: {detail}",
                True,
            )
        result.reply = text
        return self.llm.tool_result(call.id, "Reply sent.")

    def _capture_local_read(self, name: str, content: str, result: RunResult) -> str:
        record = json.loads(content)
        if name == "inspect_catalog_product":
            record["source_turn"] = self.run_dir.name
            result.catalog_products.append(record)
        return json.dumps(record, sort_keys=True)

    def _record(self, batch: int, call: ToolCall, content, is_error: bool, result: RunResult,
                seconds: float = 0.0, timings: dict | None = None, outcome: str = "executed") -> None:
        text = _content_as_text(content)
        step = Step(
            index=len(result.steps), batch=batch, tool=call.name, args=call.args,
            result=text, is_error=is_error, screenshot="", seconds=seconds,
            timings=timings or {}, outcome=outcome,
            tree_chars=len(text) if call.name in ("read_page", "find") else 0,
        )
        result.steps.append(step)
        self.on_event("step", step)

    # ---- helpers ------------------------------------------------------------

    def _start_messages(self, deal_id: int) -> list[dict]:
        """
        Every turn starts fresh: the transcript plus verified product identity references.
        No tool calls, page trees or screenshots from earlier turns. Sasha re-reads dynamic
        values such as price and stock each time.
        """
        messages: list[dict] = []
        transcript = memory.load_transcript(deal_id)
        references = product_memory.describe(self.prior_products)
        context = []
        if transcript:
            context.append(f"Conversation so far with this client:\n\n{transcript}")
        if references:
            context.append(
                "Verified product references from earlier turns follow. These establish identity only. "
                "Prices, stock, shipping and delivery are dynamic and must be rechecked when needed.\n\n"
                f"{references}"
            )
        if context:
            messages.append(self.llm.user_message("\n\n".join(context)))
            messages.append(self.llm.plain_assistant_message("Understood. I have the conversation and verified product identities."))
        return messages

    def _build_turn_text(self, deal_id: int, client_message: str | None, attachments=None) -> str:
        url = config.deal_url(deal_id)
        files = ""
        if attachments:
            files = (
                "\n\nThe client sent these files with this message. They are already downloaded; "
                "put one on a page that takes an upload with `attach_file`, naming it by its handle. "
                "You cannot open the files directly. Inspect their preview and the canvas after upload.\n\n"
                f"{attachments.describe()}"
            )
        if client_message is None:
            return (f"Deal: {url}\n\nTurn: initial outreach. "
                    f"Follow the playbook's Initial outreach section.{files}")
        return (
            f"Deal: {url}\n\n"
            f"Turn: client reply. The client just said:\n\n\"{client_message}\"\n\n"
            f"Follow the playbook.{files}"
        )

    def _write_run_json(self, deal_id: int, client_message: str | None, result: RunResult) -> None:
        log = {
            "deal_id": deal_id,
            "model": config.MODEL,
            "tools": "browser_toolset_20260801",
            "client_message": client_message,
            "reply": result.reply,
            "seconds": round(result.seconds, 1),
            "model_seconds": round(result.model_seconds, 1),
            "browser_seconds": round(result.browser_seconds, 1),
            "input_tokens": result.input_tokens,
            "cached_tokens": result.cached_tokens,
            "uncached_tokens": result.uncached_tokens,
            "output_tokens": result.output_tokens,
            "screenshots_sent": result.screenshots_sent,
            "stale_refs": result.stale_refs,
            "batch_halts": result.batch_halts,
            "stop_reason": result.stop_reason,
            "recording_seconds": result.recording_seconds,
            "model_calls": result.model_calls,
            "recordings": result.recordings,
            "catalog_products": result.catalog_products,
            "metadata": result.metadata,
            "steps": [step.__dict__ for step in result.steps],
        }
        (self.run_dir / "run.json").write_text(json.dumps(log, indent=2))

    @staticmethod
    def _reply_text(args: dict) -> str:
        """The email text, or empty string if the model sent nothing usable."""
        if "message" in args:
            return str(args["message"]).strip()
        for value in args.values():
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""


def _content_as_text(content) -> str:
    """What to store in run.json for a result: its text, with images summarised."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        match block.get("type"):
            case "text":
                parts.append(block["text"])
            case "image":
                parts.append("[image]")
            case "browser_state":
                parts.append("[browser_state: " + ", ".join(t["tab_id"] for t in block["tabs"]) + "]")
    return " ".join(parts)


def _run_metadata() -> dict:
    source_files = (
        "brain.py", "browser.py", "catalog_state.py", "memory.py", "observation_state.py",
        "product_memory.py", "reply_preflight.py", "toolset_executor.py",
        "tool_policy.py", "run_limits.py", "llm.py", "config.py",
    )
    hashes = {name: hashlib.sha256((config.ROOT / name).read_bytes()).hexdigest() for name in source_files}
    return {
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "source_file_sha256_at_start": hashes,
        "anthropic_version": version("anthropic"),
        "disabled_members": list(DISABLED_MEMBERS),
        "limits": {name: getattr(config, name) for name in (
            "MAX_BATCHES", "MAX_ACTIONS", "MAX_TURN_SECONDS", "MAX_VERIFICATION_ACTIONS",
            "VERIFICATION_SECONDS", "MODEL_TIMEOUT_SECONDS", "MAX_KEY_REPEAT")},
        "model_max_retries": 0,
    }
