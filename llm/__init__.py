"""Pick the provider from config. The brain calls make_llm() and never sees which one it got."""

import config
from llm.base import LLM, Reply, ToolCall


def make_llm() -> LLM:
    match config.LLM_PROVIDER:
        case "openai":
            from llm.openai_llm import OpenAILLM
            return OpenAILLM(config.LLM_API_KEY, config.MODEL, config.LLM_BASE_URL, cache_marker=False)
        case "gateway":
            from llm.openai_llm import OpenAILLM
            return OpenAILLM(config.LLM_API_KEY, config.MODEL, config.LLM_BASE_URL, cache_marker=True)
        case "anthropic":
            from llm.anthropic_llm import AnthropicLLM
            return AnthropicLLM(config.LLM_API_KEY, config.MODEL, config.LLM_BASE_URL)
        case other:
            raise ValueError(f"LLM_PROVIDER must be openai, anthropic or gateway, not '{other}'")


__all__ = ["make_llm", "LLM", "Reply", "ToolCall"]
