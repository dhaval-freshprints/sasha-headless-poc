"""Estimate and print the OpenAI model cost for one managed run."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any


ASTRA_INPUT_PRICE_PER_MILLION = Decimal("10.00")
ASTRA_CACHED_INPUT_PRICE_PER_MILLION = Decimal("1.00")
ASTRA_OUTPUT_PRICE_PER_MILLION = Decimal("50.00")
TOKENS_PER_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class CostEstimate:
    model: str
    status: str
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_cost_usd: float | None = None
    note: str = ""
    session_id: str = ""
    raw_usage: Any = None
    rates_usd_per_million: dict[str, str] = field(default_factory=dict)
    usage_attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_cost(model: str, usage: Any) -> CostEstimate:
    if model != "gpt-6-astra":
        return _unavailable(model, f"No pricing is configured for model {model}")
    if not isinstance(usage, dict):
        return _unavailable(model, "OpenAI did not return token usage")

    try:
        input_tokens = int(usage["input_tokens"])
        output_tokens = int(usage["output_tokens"])
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        cached_input_tokens = int(input_details.get("cached_tokens", 0))
        reasoning_tokens = int(output_details.get("reasoning_tokens", 0))
    except (KeyError, TypeError, ValueError):
        return _unavailable(model, "OpenAI returned incomplete token usage")

    cached_input_tokens = min(max(cached_input_tokens, 0), input_tokens)
    uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
    cost = (
        Decimal(uncached_input_tokens) * ASTRA_INPUT_PRICE_PER_MILLION
        + Decimal(cached_input_tokens) * ASTRA_CACHED_INPUT_PRICE_PER_MILLION
        + Decimal(output_tokens) * ASTRA_OUTPUT_PRICE_PER_MILLION
    ) / TOKENS_PER_MILLION

    return CostEstimate(
        model=model,
        status="estimated",
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        estimated_cost_usd=round(float(cost), 8),
        rates_usd_per_million={
            "input": str(ASTRA_INPUT_PRICE_PER_MILLION),
            "cached_input": str(ASTRA_CACHED_INPUT_PRICE_PER_MILLION),
            "output": str(ASTRA_OUTPUT_PRICE_PER_MILLION),
        },
        note=(
            "Uses published Standard input, cached-input, and output token rates. "
            "It excludes cache-write, long-context, service-tier, and separate tool charges."
        ),
    )


class CostReporter:
    def __init__(self, write: Callable[[str], None]) -> None:
        self.write = write

    def report(self, estimate: CostEstimate) -> None:
        self.write(f"[pricing] Model: {estimate.model}")
        if estimate.status != "estimated":
            self.write("[pricing] Estimated OpenAI cost: unavailable")
            self.write(f"[pricing] Reason: {estimate.note}")
            return

        self.write(f"[pricing] Input tokens: {estimate.input_tokens}")
        self.write(f"[pricing] Cached input tokens: {estimate.cached_input_tokens}")
        self.write(f"[pricing] Output tokens: {estimate.output_tokens}")
        self.write(f"[pricing] Reasoning tokens: {estimate.reasoning_tokens}")
        self.write(
            f"[pricing] Estimated OpenAI cost: ${estimate.estimated_cost_usd:.8f}"
        )
        self.write(f"[pricing] Note: {estimate.note}")


def _unavailable(model: str, reason: str) -> CostEstimate:
    return CostEstimate(model=model, status="unavailable", note=reason)
