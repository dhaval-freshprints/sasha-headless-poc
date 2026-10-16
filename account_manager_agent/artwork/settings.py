"""Host-side artwork extraction settings from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .constants import (
    DEFAULT_API_URL,
    DEFAULT_EXTRACT_TIMEOUT_SECONDS,
    DEFAULT_MIN_PRINT_PPI,
    DEFAULT_MODEL,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TARGET_PRINT_PPI,
    ENV_API_KEY,
    ENV_API_URL,
    ENV_EXTRACT_TIMEOUT,
    ENV_MIN_PRINT_PPI,
    ENV_MODEL,
    ENV_TARGET_PRINT_PPI,
)


def _positive_float(raw: str, *, name: str, maximum: float) -> float:
    value = float(raw)
    if value <= 0 or value > maximum:
        raise ValueError(f"{name} must be between 0 and {maximum:g}")
    return value


@dataclass(frozen=True)
class ArtworkSettings:
    """Credentials and Image API options that stay on the host."""

    api_key: str
    model: str = DEFAULT_MODEL
    api_url: str = DEFAULT_API_URL
    timeout_seconds: float = DEFAULT_EXTRACT_TIMEOUT_SECONDS
    poll_seconds: float = DEFAULT_POLL_SECONDS
    min_print_ppi: float = DEFAULT_MIN_PRINT_PPI
    target_print_ppi: float = DEFAULT_TARGET_PRINT_PPI

    @classmethod
    def from_environment(cls, values: dict[str, str] | None = None) -> "ArtworkSettings":
        source = values if values is not None else os.environ
        api_key = (source.get(ENV_API_KEY) or "").strip()
        model = (source.get(ENV_MODEL) or "").strip() or DEFAULT_MODEL
        api_url = (source.get(ENV_API_URL) or "").strip() or DEFAULT_API_URL
        timeout_raw = (source.get(ENV_EXTRACT_TIMEOUT) or "").strip()
        if timeout_raw:
            timeout_seconds = _positive_float(
                timeout_raw, name=ENV_EXTRACT_TIMEOUT, maximum=900
            )
        else:
            timeout_seconds = DEFAULT_EXTRACT_TIMEOUT_SECONDS
        min_raw = (source.get(ENV_MIN_PRINT_PPI) or "").strip()
        target_raw = (source.get(ENV_TARGET_PRINT_PPI) or "").strip()
        min_print_ppi = (
            _positive_float(min_raw, name=ENV_MIN_PRINT_PPI, maximum=1200)
            if min_raw
            else DEFAULT_MIN_PRINT_PPI
        )
        target_print_ppi = (
            _positive_float(target_raw, name=ENV_TARGET_PRINT_PPI, maximum=1200)
            if target_raw
            else DEFAULT_TARGET_PRINT_PPI
        )
        if target_print_ppi < min_print_ppi:
            raise ValueError(
                f"{ENV_TARGET_PRINT_PPI} must be greater than or equal to {ENV_MIN_PRINT_PPI}"
            )
        return cls(
            api_key=api_key,
            model=model,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
            min_print_ppi=min_print_ppi,
            target_print_ppi=target_print_ppi,
        )
