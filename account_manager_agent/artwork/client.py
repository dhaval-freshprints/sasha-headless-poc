"""Sandbox client helpers for the host artwork-extract broker."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .constants import (
    ACTION_ID,
    BROKER_DIR_NAME,
    DEFAULT_CLIENT_WAIT_SECONDS,
    DEFAULT_WORKSPACE,
    ENV_WORKSPACE,
    REQUESTS_DIR_NAME,
    RESPONSES_DIR_NAME,
    SHAPES,
)


class ArtworkClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def workspace_root() -> Path:
    return Path(os.environ.get(ENV_WORKSPACE) or DEFAULT_WORKSPACE)


def broker_dirs(workspace: Path | None = None) -> tuple[Path, Path]:
    root = (workspace or workspace_root()) / BROKER_DIR_NAME
    return root / REQUESTS_DIR_NAME, root / RESPONSES_DIR_NAME


def normalize_action_id(value: str) -> str:
    action_id = value.strip() or uuid.uuid4().hex
    if not ACTION_ID.fullmatch(action_id):
        raise ArtworkClientError(
            "invalid_action_id", "Action id must be 1-100 safe characters."
        )
    return action_id


def validate_shape(shape: str) -> str:
    if shape != "auto" and shape not in SHAPES:
        raise ArtworkClientError("invalid_shape", "Unsupported shape.")
    return shape


def request_broker(
    payload: dict,
    timeout_seconds: float = DEFAULT_CLIENT_WAIT_SECONDS,
    workspace: Path | None = None,
) -> dict:
    """Atomically submit one request and wait for its host-produced response."""
    requests_dir, responses_dir = broker_dirs(workspace)
    if not requests_dir.is_dir() or not responses_dir.is_dir():
        raise ArtworkClientError(
            "broker_unavailable",
            "The host artwork extraction broker is unavailable.",
        )
    action_id = str(payload["action_id"])
    request_path = requests_dir / f"{action_id}.json"
    response_path = responses_dir / f"{action_id}.json"
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not response_path.exists():
        temporary = request_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(request_path)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if response.pop("request_fingerprint", None) != fingerprint:
                raise ArtworkClientError(
                    "action_id_conflict",
                    "This action id was already used for a different request.",
                )
            return response
        time.sleep(0.05)
    raise ArtworkClientError(
        "broker_timeout", "The host artwork extraction broker did not respond in time."
    )
