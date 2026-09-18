"""
Talk to Claude. Builds the request, reads the answer. Nothing else knows the wire format.

Tools: Anthropic's browser toolset (executed by toolset_executor.py) plus one custom tool,
reply_to_client, which ends the turn.
"""

from dataclasses import dataclass, field

from anthropic import Anthropic

import config

# Members we never need on this CRM. A smaller schema is cheaper and gives the model less to wander into.
# `wait` is off because every action already waits for the page to settle (browser.settle); with it on,
# the model padded almost every action with 2-3s.
DISABLED_MEMBERS = ["left_mouse_down", "left_mouse_up", "hold_key", "left_click_drag", "middle_click", "wait"]

BROWSER_TOOLSET = {
    "type": "browser_toolset_20260801",
    "configs": {member: {"enabled": False} for member in DISABLED_MEMBERS},
}

REPLY_TOOL = {
    "name": "reply_to_client",
    "description": "Finish: send this message to the client. Call exactly once, at the end.",
    "input_schema": {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
}

TOOLS = [BROWSER_TOOLSET, REPLY_TOOL]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    toolset_name: str | None   # "browser" for toolset members, None for reply_to_client

    @property
    def is_browser(self) -> bool:
        return self.toolset_name == "browser"


@dataclass
class Reply:
    text: str
    raw_content: list                       # the response blocks, replayed as-is on the next call
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    stop_reason: str = ""


class LLM:
    def __init__(self):
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.model = config.MODEL

    def send(self, system: str, messages: list[dict]) -> Reply:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=_with_cache_breakpoint(messages),
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, args=dict(block.input), toolset_name=block.toolset_name)
            for block in response.content if block.type == "tool_use"
        ]
        usage = response.usage
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return Reply(
            text=text,
            raw_content=list(response.content),
            tool_calls=tool_calls,
            input_tokens=usage.input_tokens + cached + created,   # total, the way the brain counts it
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
            stop_reason=response.stop_reason or "",
        )

    # ---- message builders ---------------------------------------------------

    @staticmethod
    def user_message(text: str) -> dict:
        return {"role": "user", "content": text}

    @staticmethod
    def plain_assistant_message(text: str) -> dict:
        return {"role": "assistant", "content": text}

    @staticmethod
    def assistant_message(reply: Reply) -> dict:
        # Replay the blocks exactly as returned (text, tool_use, and any thinking blocks).
        return {"role": "assistant", "content": reply.raw_content}

    @staticmethod
    def browser_result(call_id: str, content, is_error: bool = False) -> dict:
        """One result for one toolset member call. `content` is a string or a list of blocks."""
        block = {"type": "tool_result", "tool_use_id": call_id, "toolset_name": "browser", "content": content}
        if is_error:
            block["is_error"] = True
        return block

    @staticmethod
    def tool_result(call_id: str, text: str, is_error: bool = False) -> dict:
        """One result for the custom reply_to_client tool."""
        block = {"type": "tool_result", "tool_use_id": call_id, "content": text}
        if is_error:
            block["is_error"] = True
        return block

    @staticmethod
    def results_message(blocks: list[dict]) -> dict:
        """All results for one model turn go back together, in call order."""
        return {"role": "user", "content": blocks}


def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """
    Anthropic caches everything up to the last cache_control marker. The system prompt carries
    one; marking the last message too makes the whole conversation so far a cache hit next call.
    """
    if not messages:
        return messages
    marked = list(messages)
    last = dict(marked[-1])
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    else:
        content = list(content)
        content[-1] = dict(content[-1])
    content[-1]["cache_control"] = {"type": "ephemeral"}
    last["content"] = content
    marked[-1] = last
    return marked
