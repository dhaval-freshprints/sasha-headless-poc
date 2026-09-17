"""
Sasha Brain — hybrid loop (option C).

After every action the model gets BOTH the accessibility tree (names, exact) and a
screenshot (layout, canvas). It acts by name when it can, by coordinates when it must.
No business rules in the system prompt — the CRM, quoter and forms already enforce them.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
import memory
from browser import Browser, VIEWPORT
from llm import make_llm

WORKPLACE = (config.ROOT / "prompts" / "workplace.md").read_text()
PLAYBOOK = (config.ROOT / "prompts" / "playbook.md").read_text()

IDENTITY = f"""You are Sasha, a sales representative at Fresh Prints (custom apparel).

You are logged into the Fresh Prints CRM in a browser. After every action you get two
views of the screen: the accessibility tree (exact names of links, buttons and fields, plus
an EDITABLE FIELDS list) and a screenshot ({VIEWPORT["width"]}x{VIEWPORT["height"]} px).
Use the tree for names and values. Use the screenshot for layout, images, and anything the
tree doesn't show.

How to act:
- Prefer acting by name: `click` (role + name from the tree), `click_text` (plain visible
  text), `fill_field` (label or #index from EDITABLE FIELDS). These are exact.
- Use `click_at` / `type_here` with screenshot coordinates only when nothing in the tree
  matches, e.g. canvas or icon-only controls.
- `fill_field` replaces the field's content and tells you what it now contains. Read that.
- `read_text` gives the page's visible text exactly. Use it to verify what saved.

Below are two references. WORKPLACE is the map of the CRM: where things are and how they
work. PLAYBOOK is how you work and write. When you're done, call `reply_to_client` with
the message to the client.
"""

# One system prompt, three sections. Identical across every turn and every deal, so the
# whole block is a cache hit after the first call. Each provider marks it for caching its own way.
SYSTEM_PROMPT = f"{IDENTITY}\n\n---\n\n{WORKPLACE}\n\n---\n\n{PLAYBOOK}"

NUDGE = (
    "You stopped without replying. Call `reply_to_client` now with your message to the client. "
    "If you could not complete something, say so in the message."
)

XY = {"x": {"type": "integer"}, "y": {"type": "integer"}}

TOOL_SPECS = [
    {"name": "navigate", "description": "Go to a URL.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "look", "description": "Look at the screen again without doing anything.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "read_text", "description": "Get the page's visible text, exactly. Use to verify that what you entered actually saved, or to read a number the screenshot makes hard to see.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "click", "description": "Click an element by its ARIA role and accessible name from the tree.",
     "parameters": {"type": "object", "properties": {"role": {"type": "string"}, "name": {"type": "string"}}, "required": ["role", "name"]}},
    {"name": "click_text", "description": "Click by visible text, for things with no role (spans, menu items, tabs).",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "fill_field",
     "description": "Replace the content of an editable field. `field` = its label, placeholder, nearby heading, or '#N' from EDITABLE FIELDS. Works for inputs, textareas and rich-text editors.",
     "parameters": {"type": "object", "properties": {"field": {"type": "string"}, "text": {"type": "string"}, "press_enter": {"type": "boolean", "default": False}}, "required": ["field", "text"]}},
    {"name": "select_option", "description": "Pick an option in a <select> dropdown by label.",
     "parameters": {"type": "object", "properties": {"field": {"type": "string"}, "option": {"type": "string"}}, "required": ["field", "option"]}},
    {"name": "press_key", "description": "Press a key: Enter, Escape, Tab, ArrowDown, ...",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "click_at", "description": "Fallback: click at screenshot pixel coordinates.",
     "parameters": {"type": "object", "properties": XY, "required": ["x", "y"]}},
    {"name": "type_here", "description": "Fallback: type into whatever has focus (after click_at).",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "scroll", "description": "Scroll the page.",
     "parameters": {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer", "default": 3}}, "required": ["direction"]}},
    {"name": "reply_to_client", "description": "Finish: send this message to the client. Call exactly once, at the end.",
     "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
]


@dataclass
class Step:
    index: int
    tool: str
    args: dict
    result: str          # the tool's own one-line result
    screenshot: str
    tree_chars: int = 0  # size of the accessibility tree the model was given with this step
    tree_head: str = ""  # first few lines of it, so run.json shows what the model saw


@dataclass
class RunResult:
    reply: str
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0
    model_seconds: float = 0.0    # time spent waiting on the LLM
    browser_seconds: float = 0.0  # time spent in browser actions + snapshots

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
            f"out {self.output_tokens:,}"
        )


class Brain:
    def __init__(self, browser: Browser, run_dir: Path, on_event=None):
        """
        on_event(kind, payload) is called as the turn progresses, so a caller can show
        progress. kind is "thinking" (payload: step index) or "step" (payload: Step).
        """
        self.browser = browser
        self.run_dir = run_dir
        self.on_event = on_event or (lambda kind, payload: None)
        self.llm = make_llm()

    def run(self, deal_id: int, client_message: str | None) -> RunResult:
        messages = self._start_messages(deal_id)
        messages.append(self.llm.user_message(self._build_turn_text(deal_id, client_message)))

        result = RunResult(reply="")
        turn_started = time.monotonic()
        nudged = False

        for step_index in range(config.MAX_STEPS):
            self.on_event("thinking", step_index)
            model_started = time.monotonic()
            reply = self.llm.send(SYSTEM_PROMPT, messages, TOOL_SPECS)
            result.model_seconds += time.monotonic() - model_started
            result.input_tokens += reply.input_tokens
            result.output_tokens += reply.output_tokens
            result.cached_tokens += reply.cached_tokens
            messages.append(self.llm.assistant_message(reply))

            if not reply.tool_calls:
                if not result.reply and not nudged:
                    messages.append(self.llm.user_message(NUDGE))
                    nudged = True
                    continue
                result.reply = reply.text
                break

            finished = False
            for tool_call in reply.tool_calls:
                name = tool_call.name
                args = tool_call.args

                if name == "reply_to_client":
                    text = self._reply_text(args)
                    if not text:
                        messages.append(self.llm.tool_result(
                            tool_call.id, "The message was empty. Call reply_to_client again with the full email text in `message`."))
                        continue
                    result.reply = text
                    messages.append(self.llm.tool_result(tool_call.id, "Reply sent."))
                    finished = True
                    continue

                browser_started = time.monotonic()
                output = self._execute(name, args)
                tree = self.browser.snapshot()
                shot_path = self.browser.save_screenshot(self.run_dir / f"step_{step_index:02d}.png")
                result.browser_seconds += time.monotonic() - browser_started
                step = Step(
                    step_index, name, args, output, str(shot_path),
                    tree_chars=len(tree), tree_head="\n".join(tree.splitlines()[:12]),
                )
                result.steps.append(step)
                self.on_event("step", step)
                messages.append(self.llm.tool_result(tool_call.id, f"{output}\n\n{tree}"))
                messages.append(self.llm.screenshot_message(shot_path))

            if finished:
                break

        result.seconds = time.monotonic() - turn_started
        memory.append_transcript(deal_id, client_message, result.reply)
        self._write_run_json(deal_id, client_message, result)
        return result

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

    def _execute(self, tool: str, args: dict) -> str:
        b = self.browser
        try:
            match tool:
                case "navigate":
                    return b.navigate(args["url"])
                case "look":
                    return "Looking."
                case "read_text":
                    return b.read_text()
                case "click":
                    return b.click(args["role"], args["name"])
                case "click_text":
                    return b.click_text(args["text"])
                case "fill_field":
                    return b.fill_field(args["field"], args["text"], args.get("press_enter", False))
                case "select_option":
                    return b.select_option(args["field"], args["option"])
                case "press_key":
                    return b.press_key(args["key"])
                case "click_at":
                    return b.click_at(args["x"], args["y"])
                case "type_here":
                    return b.type_here(args["text"])
                case "scroll":
                    return b.scroll(args["direction"], args.get("amount", 3))
                case _:
                    return f"Unknown tool: {tool}"
        except Exception as error:
            return f"ERROR: {type(error).__name__}: {str(error)[:300]}"

    def _write_run_json(self, deal_id: int, client_message: str | None, result: RunResult) -> None:
        log = {
            "deal_id": deal_id,
            "client_message": client_message,
            "reply": result.reply,
            "seconds": round(result.seconds, 1),
            "model_seconds": round(result.model_seconds, 1),
            "browser_seconds": round(result.browser_seconds, 1),
            "input_tokens": result.input_tokens,
            "cached_tokens": result.cached_tokens,
            "uncached_tokens": result.uncached_tokens,
            "output_tokens": result.output_tokens,
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

