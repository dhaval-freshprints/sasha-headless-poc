"""
Sasha Brain: the loop.

Each model turn returns a batch of browser tool calls. We run them in order, stop at the
first failure, and send every result back together. The model decides when to look
(screenshot, read_page, get_page_text); we do not push a view after every action.
No business rules in the system prompt: the CRM, quoter and forms already enforce them.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import memory
from browser import Browser, VIEWPORT
from llm import LLM, ToolCall
from toolset_executor import NOT_EXECUTED, StaleRef, ToolsetExecutor, stale_ref_message

WORKPLACE = (config.ROOT / "prompts" / "workplace.md").read_text()
PLAYBOOK = (config.ROOT / "prompts" / "playbook.md").read_text()

IDENTITY = f"""You are Sasha, a sales representative at Fresh Prints (custom apparel).

You are logged into the Fresh Prints CRM in a browser and you drive it with the browser
tools. The viewport is {VIEWPORT["width"]}x{VIEWPORT["height"]} px. Read the page before you
act on it, and look again after any write to confirm what saved. `get_page_text` gives the
visible text exactly; use it to read prices and numbers. Fill fields with `form_input`: it
also handles search boxes and dropdowns that filter as you type, and picks the matching
option for you. Every action already waits for the page to settle before it returns, so act,
then look; there is no need to pause between them.

Below are two references. WORKPLACE is the map of the CRM: where things are and how they
work. PLAYBOOK is how you work and write. When you're done, call `reply_to_client` with
the message to the client.
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
            f"images {self.screenshots_sent}, stale refs {self.stale_refs}, halts {self.batch_halts}"
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

    def run(self, deal_id: int, client_message: str | None) -> RunResult:
        messages = self._start_messages(deal_id)
        messages.append(self.llm.user_message(self._build_turn_text(deal_id, client_message)))

        result = RunResult(reply="")
        turn_started = time.monotonic()
        nudges = 0

        for batch in range(config.MAX_STEPS):
            self.on_event("thinking", batch)
            model_started = time.monotonic()
            reply = self.llm.send(SYSTEM_PROMPT, messages)
            result.model_seconds += time.monotonic() - model_started
            result.input_tokens += reply.input_tokens
            result.output_tokens += reply.output_tokens
            result.cached_tokens += reply.cached_tokens
            messages.append(self.llm.assistant_message(reply))

            if not reply.tool_calls:
                # Text instead of a tool call. Only reply_to_client counts as a reply: text that
                # looks like a tool call must never reach the client. Nudge, then give up.
                if result.reply or nudges >= MAX_NUDGES:
                    break
                messages.append(self.llm.user_message(NUDGE))
                nudges += 1
                continue

            browser_started = time.monotonic()
            blocks, finished = self._run_batch(batch, reply.tool_calls, result)
            shot_path = self.browser.save_screenshot(self.run_dir / f"batch_{batch:02d}.png")
            for step in result.steps:
                if step.batch == batch:
                    step.screenshot = str(shot_path)
            result.browser_seconds += time.monotonic() - browser_started
            messages.append(self.llm.results_message(blocks))

            if finished:
                break

        result.seconds = time.monotonic() - turn_started
        memory.append_transcript(deal_id, client_message, result.reply or NO_REPLY)
        self._write_run_json(deal_id, client_message, result)
        return result

    # ---- one batch ----------------------------------------------------------

    def _run_batch(self, batch: int, calls: list[ToolCall], result: RunResult) -> tuple[list[dict], bool]:
        """Run the calls in order. After the first browser failure, the rest are not executed."""
        blocks: list[dict] = []
        failed = False
        finished = False
        for call in calls:
            if not call.is_browser:
                blocks.append(self._handle_reply(call, result))
                finished = finished or bool(result.reply)
                continue
            if failed:
                blocks.append(self.llm.browser_result(call.id, NOT_EXECUTED, is_error=True))
                self._record(batch, call, NOT_EXECUTED, True, result)
                continue
            content, is_error = self._execute(call, result)
            if is_error:
                failed = True
                result.batch_halts += 1
            blocks.append(self.llm.browser_result(call.id, content, is_error))
            self._record(batch, call, content, is_error, result)
        return blocks, finished

    def _execute(self, call: ToolCall, result: RunResult) -> tuple[str | list, bool]:
        try:
            content = self.executor.run(call.name, call.args)
        except StaleRef as stale:
            result.stale_refs += 1
            return stale_ref_message(str(stale)), True
        except Exception as error:
            return f"Error: {type(error).__name__}: {str(error)[:300]}", True
        if call.name in ("screenshot", "zoom"):
            result.screenshots_sent += 1
        return content, False

    def _handle_reply(self, call: ToolCall, result: RunResult) -> dict:
        text = self._reply_text(call.args)
        if not text:
            return self.llm.tool_result(
                call.id, "The message was empty. Call reply_to_client again with the full email text in `message`.")
        result.reply = text
        return self.llm.tool_result(call.id, "Reply sent.")

    def _record(self, batch: int, call: ToolCall, content, is_error: bool, result: RunResult) -> None:
        text = _content_as_text(content)
        step = Step(
            index=len(result.steps), batch=batch, tool=call.name, args=call.args,
            result=text, is_error=is_error, screenshot="",
            tree_chars=len(text) if call.name in ("read_page", "find") else 0,
        )
        result.steps.append(step)
        self.on_event("step", step)

    # ---- helpers ------------------------------------------------------------

    def _start_messages(self, deal_id: int) -> list[dict]:
        """
        Every turn starts fresh: the system prompt plus what the client and Sasha have said.
        No tool calls, page trees or screenshots from earlier turns. Sasha re-reads the CRM
        each time, like a rep opening the thread and then the deal.
        """
        messages: list[dict] = []
        transcript = memory.load_transcript(deal_id)
        if transcript:
            messages.append(self.llm.user_message(f"Conversation so far with this client:\n\n{transcript}"))
            messages.append(self.llm.plain_assistant_message("Understood. I have the conversation so far."))
        return messages

    def _build_turn_text(self, deal_id: int, client_message: str | None) -> str:
        url = config.deal_url(deal_id)
        if client_message is None:
            return f"Deal: {url}\n\nTurn: initial outreach. Follow the playbook's Initial outreach section."
        return (
            f"Deal: {url}\n\n"
            f"Turn: client reply. The client just said:\n\n\"{client_message}\"\n\n"
            "Follow the playbook."
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
