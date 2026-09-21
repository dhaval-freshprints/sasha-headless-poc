"""
Talk to Claude. Builds the request, reads the answer. Nothing else knows the wire format.

Tools: Anthropic's browser toolset (executed by toolset_executor.py), catalog verification,
file attachment, and reply_to_client, which ends the turn.
"""

from dataclasses import dataclass, field

from anthropic import Anthropic

import config
from tool_policy import DISABLED_MEMBERS

BROWSER_TOOLSET = {
    "type": "browser_toolset_20260801",
    "configs": {member: {"enabled": False} for member in DISABLED_MEMBERS},
}

# Uploading a client's file. Not a browser-toolset member: the toolset has file_upload, but the
# Design Tool's input is hidden (0x0), so it is not in the page tree and cannot be targeted by ref.
# This tool names the file by the handle the turn was given; the executor sets it on the input.
ATTACH_TOOL = {
    "name": "attach_file",
    "description": (
        "Give one of this turn's files to the file input on the current page, for pages that take "
        "an upload (the Design Tool's Upload, the wizard's Upload Ref. Image). Use this instead of "
        "clicking the Upload tile: clicking it opens the operating system's file dialog, which "
        "cannot be answered. In the Design Tool, set selector to upload-file-input to target "
        "artwork rather than the separate font input. Only files listed in this turn's message can be attached."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file": {"type": "string", "description": "The handle you were given, e.g. file_1."},
            "selector": {"type": "string",
                         "description": "Optional data-testid or id of the input, e.g. upload-file-input."},
        },
        "required": ["file"],
    },
}

CATALOG_TOOL = {
    "name": "inspect_catalog_product",
    "description": (
        "Verify that an exact style appears as a product link on the current public Fresh Prints catalog page. "
        "Use this before recommending a generic product option. An exact completed search with no matching "
        "link returns not_found; an ambiguous page returns unknown."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "style_code": {"type": "string"},
            "color": {"type": "string"},
        },
        "required": ["style_code", "color"],
    },
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

TOOLS = [BROWSER_TOOLSET, ATTACH_TOOL, CATALOG_TOOL, REPLY_TOOL]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict
    toolset_name: str | None   # "browser" for toolset members, None for reply_to_client

    @property
    def is_browser(self) -> bool:
        return self.toolset_name == "browser"

    @property
    def is_attach(self) -> bool:
        return self.name == "attach_file"

    @property
    def is_local_read(self) -> bool:
        return self.name == "inspect_catalog_product"


@dataclass
class Reply:
    text: str
    raw_content: list                       # the response blocks, replayed as-is on the next call
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    stop_reason: str = ""
    request_id: str = ""


class LLM:
    def __init__(self):
        self.client = Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                timeout=config.MODEL_TIMEOUT_SECONDS, max_retries=0)
        self.model = config.MODEL

    def send(self, system: str, messages: list[dict], timeout_seconds: float | None = None) -> Reply:
        timeout = config.MODEL_TIMEOUT_SECONDS
        if timeout_seconds is not None:
            if timeout_seconds <= 0:
                raise TimeoutError("No model-call time remains")
            timeout = min(timeout, timeout_seconds)
        response = self.client.with_options(timeout=timeout).messages.create(
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
            request_id=getattr(response, "_request_id", "") or "",
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
