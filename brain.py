"""
Sasha Brain.

One model, one loop: look at the page, pick an action, repeat, then reply.
No business rules in the prompt — the CRM, quoter and forms already enforce them.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

import config
import memory
from browser import Browser

OUTREACH_GUIDE = (config.ROOT / "prompts" / "initial_outreach.md").read_text()
REPLY_GUIDE = (config.ROOT / "prompts" / "client_reply.md").read_text()

SYSTEM_PROMPT = """You are Sasha, a sales representative at Fresh Prints (custom apparel).

You are logged into the Fresh Prints CRM in a browser. You can see pages and act on them
using the tools provided. Work the way a careful human rep would:

- Open the deal first and read it (client, proof, product, quantity, dates) before replying.
- Use the CRM, quoter and forms for real facts. Never invent a price, stock level or date.
- After any action that changes data, look at the page again to confirm it actually saved.
- If you need information only the client can give, ask them for it in your reply.
- If something is blocked or unclear, say so in your reply rather than guessing.

When you have what you need, call `reply_to_client` with a short, friendly message written
as Sasha. Prices, quantities and dates in the reply must be values you saw on screen.
"""

NUDGE = (
    "You stopped without replying. Call `reply_to_client` now with your message to the client. "
    "If you could not complete something, say so in the message."
)

# Plain function specs; wrapped into OpenAI's {"type": "function", "function": …} below.
TOOL_SPECS = [
    {
        "name": "navigate",
        "description": "Go to a URL.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "snapshot",
        "description": "Get the accessibility tree of the current page (what is on screen).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click an element by its ARIA role and accessible name, as shown in the snapshot.",
        "parameters": {
            "type": "object",
            "properties": {"role": {"type": "string"}, "name": {"type": "string"}},
            "required": ["role", "name"],
        },
    },
    {
        "name": "type_text",
        "description": "Type into a textbox (replaces existing content). Optionally press Enter after.",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "text": {"type": "string"},
                "press_enter": {"type": "boolean", "default": False},
            },
            "required": ["role", "name", "text"],
        },
    },
    {
        "name": "select_option",
        "description": "Pick an option in a <select> dropdown by its visible label.",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "name": {"type": "string"},
                "option": {"type": "string"},
            },
            "required": ["role", "name", "option"],
        },
    },
    {
        "name": "reply_to_client",
        "description": "Finish: send this message to the client. Call exactly once, at the end.",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
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
                model=config.MODEL,
                max_tokens=2048,
                tools=TOOLS,
                messages=messages,
            )
            result.input_tokens += response.usage.prompt_tokens
            result.output_tokens += response.usage.completion_tokens
            assistant = response.choices[0].message
            messages.append(self._assistant_as_dict(assistant))

            tool_calls = assistant.tool_calls or []
            if not tool_calls:
                # Model stopped without calling reply_to_client. Nudge once; if it still
                # doesn't, fall back to whatever text it wrote.
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
                shot = self.browser.screenshot(self.run_dir / f"step_{step_index:02d}.png")
                result.steps.append(Step(step_index, name, args, output, str(shot)))
                messages.append(self._tool_result(tool_call.id, output))

            if finished:
                break

        memory.save_history(deal_id, messages)
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
        try:
            match tool:
                case "navigate":
                    return self.browser.navigate(args["url"])
                case "snapshot":
                    return self.browser.snapshot()
                case "click":
                    return self.browser.click(args["role"], args["name"])
                case "type_text":
                    return self.browser.type_text(
                        args["role"], args["name"], args["text"], args.get("press_enter", False)
                    )
                case "select_option":
                    return self.browser.select_option(args["role"], args["name"], args["option"])
                case _:
                    return f"Unknown tool: {tool}"
        except Exception as error:
            return f"ERROR: {type(error).__name__}: {error}"

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
        """Models sometimes use a different key than the schema says. Take any string value."""
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
        """Keep history as plain dicts so it round-trips through any OpenAI-compatible server."""
        entry: dict = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in message.tool_calls
            ]
        return entry

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw}
