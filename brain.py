"""
Sasha Brain — hybrid loop (option C).

After every action the model gets BOTH the accessibility tree (names, exact) and a
screenshot (layout, canvas). It acts by name when it can, by coordinates when it must.
No business rules in the system prompt — the CRM, quoter and forms already enforce them.
"""

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

import config
import memory
from browser import Browser, VIEWPORT

OUTREACH_GUIDE = (config.ROOT / "prompts" / "initial_outreach.md").read_text()
REPLY_GUIDE = (config.ROOT / "prompts" / "client_reply.md").read_text()

SYSTEM_PROMPT = f"""You are Sasha, a sales representative at Fresh Prints (custom apparel).

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

Work the way a careful human rep would:
- Look before you act. Open the deal and read it before replying.
- Use the CRM, quoter and forms for real facts. Never invent a price, stock level or date.
- When you submit a form on the client's behalf, the form must actually contain what the
  client asked for. Fill the description / notes field with their request in plain words.
- After any action that changes data, look again and confirm it saved and that what you
  entered is there. Reporting success you did not see is the worst mistake you can make.
- Never delete anything. If a dialog asks to confirm a delete, answer No.
- If you need information only the client can give, ask them for it in your reply.
- If something is blocked or unclear, say so in your reply rather than guessing.
- Some buttons open a new tab. You are always shown the newest tab.

When you have what you need, call `reply_to_client` with a short, friendly message written
as Sasha. Prices, quantities and dates in the reply must be values you saw on screen.
"""

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

TOOLS = [{"type": "function", "function": spec} for spec in TOOL_SPECS]


@dataclass
class Step:
    index: int
    tool: str
    args: dict
    result: str
    screenshot: str


@dataclass
class RunResult:
    reply: str
    steps: list[Step] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0


class Brain:
    def __init__(self, browser: Browser, run_dir: Path):
        self.browser = browser
        self.run_dir = run_dir
        self.client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)

    def run(self, deal_id: int, client_message: str | None) -> RunResult:
        messages = memory.load_history(deal_id)
        if not messages:
            messages.append({"role": "system", "content": SYSTEM_PROMPT})
        messages.append({"role": "user", "content": self._build_turn_text(deal_id, client_message)})

        result = RunResult(reply="")

        for step_index in range(config.MAX_STEPS):
            response = self.client.chat.completions.create(
                model=config.MODEL, max_tokens=2048, tools=TOOLS, messages=messages,
            )
            result.input_tokens += response.usage.prompt_tokens
            result.output_tokens += response.usage.completion_tokens
            assistant = response.choices[0].message
            messages.append(self._assistant_as_dict(assistant))

            tool_calls = assistant.tool_calls or []
            if not tool_calls:
                if not result.reply and not self._nudged(messages):
                    messages.append({"role": "user", "content": NUDGE})
                    continue
                result.reply = assistant.content or ""
                break

            finished = False
            for tool_call in tool_calls:
                name = tool_call.function.name
                args = self._parse_args(tool_call.function.arguments)

                if name == "reply_to_client":
                    result.reply = self._reply_text(args)
                    messages.append(self._tool_result(tool_call.id, "Reply sent."))
                    finished = True
                    continue

                output = self._execute(name, args)
                tree = self.browser.snapshot()
                shot_path = self.browser.save_screenshot(self.run_dir / f"step_{step_index:02d}.png")
                result.steps.append(Step(step_index, name, args, output, str(shot_path)))
                messages.append(self._tool_result(tool_call.id, f"{output}\n\n{tree}"))
                messages.append(self._screenshot_message(shot_path))

            if finished:
                break

        memory.save_history(deal_id, self._compact_old_screenshots(messages))
        memory.append_transcript(deal_id, client_message, result.reply)
        self._write_run_json(deal_id, client_message, result)
        return result

    # ---- helpers ------------------------------------------------------------

    def _build_turn_text(self, deal_id: int, client_message: str | None) -> str:
        url = config.deal_url(deal_id)
        if client_message is None:
            return (
                f"Deal: {url}\n\n"
                "Write the initial outreach to this client. Read the deal first, then follow "
                "the guide below exactly.\n\n"
                f"{OUTREACH_GUIDE}"
            )
        return (
            f"Deal: {url}\n\n"
            f"The client just replied:\n\n\"{client_message}\"\n\n"
            "Work out what they need, do it in the CRM, then reply. Follow the guide below.\n\n"
            f"{REPLY_GUIDE}"
        )

    def _execute(self, tool: str, args: dict) -> str:
        b = self.browser
        try:
            match tool:
                case "navigate":
                    return b.navigate(args["url"])
                case "look":
                    return "Looking."
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

    @staticmethod
    def _screenshot_message(path: Path) -> dict:
        data = base64.b64encode(path.read_bytes()).decode()
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": "Screenshot:"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
            ],
        }

    @staticmethod
    def _compact_old_screenshots(messages: list[dict]) -> list[dict]:
        """Keep the last few screenshots in saved history; older ones become a note."""
        keep = config.SCREENSHOTS_TO_KEEP
        image_indexes = [
            i for i, m in enumerate(messages)
            if m.get("role") == "user" and isinstance(m.get("content"), list)
        ]
        for i in image_indexes[:-keep] if keep else image_indexes:
            messages[i] = {"role": "user", "content": "[earlier screenshot omitted]"}
        return messages

    def _write_run_json(self, deal_id: int, client_message: str | None, result: RunResult) -> None:
        log = {
            "deal_id": deal_id,
            "client_message": client_message,
            "reply": result.reply,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "steps": [step.__dict__ for step in result.steps],
        }
        (self.run_dir / "run.json").write_text(json.dumps(log, indent=2))

    @staticmethod
    def _reply_text(args: dict) -> str:
        if "message" in args:
            return str(args["message"])
        for value in args.values():
            if isinstance(value, str) and value.strip():
                return value
        return json.dumps(args)

    @staticmethod
    def _nudged(messages: list[dict]) -> bool:
        return any(m.get("content") == NUDGE for m in messages)

    @staticmethod
    def _tool_result(tool_call_id: str, content: str) -> dict:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}

    @staticmethod
    def _assistant_as_dict(message) -> dict:
        entry: dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in message.tool_calls
            ]
        return entry

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw}
