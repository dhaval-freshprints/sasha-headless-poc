"""
What the brain needs from a language model. Five methods, nothing provider-specific.

The brain builds a list of messages and calls send(). Each provider turns those messages
into its own wire format and turns the answer back into a Reply. The brain never sees a
provider object.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class LLM:
    """Implement these five in a provider file. Each returns a message dict in the provider's shape."""

    def send(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        """Call the model. `tools` is the neutral spec list: {name, description, parameters}."""
        raise NotImplementedError

    def assistant_message(self, reply: Reply) -> dict:
        """The assistant's turn, to append to messages before adding tool results."""
        raise NotImplementedError

    def tool_result(self, call_id: str, text: str) -> dict:
        raise NotImplementedError

    def screenshot_message(self, png_path: Path) -> dict:
        raise NotImplementedError

    def user_message(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def plain_assistant_message(self, text: str) -> dict:
        return {"role": "assistant", "content": text}
