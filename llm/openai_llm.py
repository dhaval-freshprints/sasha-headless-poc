"""
OpenAI chat-completions shape. Works for api.openai.com and for any OpenAI-compatible
gateway (set base_url).
"""

import base64
import json
from pathlib import Path

from openai import OpenAI

from llm.base import LLM, Reply, ToolCall


class OpenAILLM(LLM):
    def __init__(self, api_key: str, model: str, base_url: str | None = None, cache_marker: bool = False):
        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model = model
        # Anthropic-style cache_control on the system block. Gateways that front Claude need
        # it; api.openai.com caches automatically and does not need it.
        self.cache_marker = cache_marker

    def send(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        response = self.client.chat.completions.create(
            model=self.model,
            tools=[{"type": "function", "function": spec} for spec in tools],
            messages=[self._system_message(system)] + messages,
            **self._output_limit(),
        )
        message = response.choices[0].message
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        return Reply(
            text=message.content or "",
            tool_calls=[
                ToolCall(id=c.id, name=c.function.name, args=self._parse_args(c.function.arguments))
                for c in (message.tool_calls or [])
            ],
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cached_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        )

    def assistant_message(self, reply: Reply) -> dict:
        entry: dict = {"role": "assistant", "content": reply.text}
        if reply.tool_calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}}
                for c in reply.tool_calls
            ]
        return entry

    def tool_result(self, call_id: str, text: str) -> dict:
        return {"role": "tool", "tool_call_id": call_id, "content": text}

    def screenshot_message(self, png_path: Path) -> dict:
        data = base64.b64encode(png_path.read_bytes()).decode()
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": "Screenshot:"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
            ],
        }

    def _output_limit(self) -> dict:
        # Gateways and older models want max_tokens. cache_marker doubles as "this is a gateway".
        if self.cache_marker:
            return {"max_tokens": 2048}
        # Current OpenAI models want max_completion_tokens, and reasoning models refuse
        # function tools on chat-completions unless reasoning is off.
        return {"max_completion_tokens": 2048, "reasoning_effort": "none"}

    def _system_message(self, system: str) -> dict:
        if not self.cache_marker:
            return {"role": "system", "content": system}
        return {
            "role": "system",
            "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        }

    @staticmethod
    def _parse_args(raw: str) -> dict:
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw}
