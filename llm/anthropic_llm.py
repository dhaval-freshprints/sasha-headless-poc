"""
Anthropic Messages API shape. System prompt is a top-level field; tool calls and tool
results are content blocks; images are base64 sources.
"""

import base64
import copy
from pathlib import Path

from anthropic import Anthropic

from llm.base import LLM, Reply, ToolCall


class AnthropicLLM(LLM):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self.client = Anthropic(api_key=api_key, base_url=base_url or None)
        self.model = model

    def send(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                for t in tools
            ],
            messages=self._with_cache_breakpoint(messages),
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        tool_calls = [
            ToolCall(id=block.id, name=block.name, args=dict(block.input))
            for block in response.content if block.type == "tool_use"
        ]
        usage = response.usage
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        return Reply(
            text=text,
            tool_calls=tool_calls,
            # Anthropic reports cached tokens separately from input_tokens; the brain
            # expects input_tokens to be the total, so add them back.
            input_tokens=usage.input_tokens + cached + int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            output_tokens=usage.output_tokens,
            cached_tokens=cached,
        )

    @staticmethod
    def _with_cache_breakpoint(messages: list[dict]) -> list[dict]:
        """
        Anthropic caches everything up to the last cache_control marker. The system prompt
        carries one, but that only covers ~5K tokens; the transcript, tool results and
        screenshots after it would be re-sent fresh every call. Marking the last message
        makes the whole conversation so far a cache hit on the next call.
        """
        if not messages:
            return messages
        marked = copy.deepcopy(messages)
        last = marked[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1]["cache_control"] = {"type": "ephemeral"}
        return marked

    def assistant_message(self, reply: Reply) -> dict:
        blocks: list[dict] = []
        if reply.text:
            blocks.append({"type": "text", "text": reply.text})
        for call in reply.tool_calls:
            blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.args})
        return {"role": "assistant", "content": blocks}

    def tool_result(self, call_id: str, text: str) -> dict:
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}],
        }

    def screenshot_message(self, png_path: Path) -> dict:
        data = base64.b64encode(png_path.read_bytes()).decode()
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": "Screenshot:"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
            ],
        }
