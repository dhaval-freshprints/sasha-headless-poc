"""Sandbox client for the host-side, deal-scoped sample Freshdesk broker."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

BROKER_ROOT = Path("/workspace/.sample-freshdesk")
DEFAULT_TIMEOUT_SECONDS = 35.0


class SampleFreshdeskError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def positive_sample_id(value: str) -> int:
    if not str(value).isdigit():
        raise SampleFreshdeskError("invalid_sample_id", "Sample id must be a positive integer.")
    number = int(value)
    if number <= 0:
        raise SampleFreshdeskError("invalid_sample_id", "Sample id must be a positive integer.")
    return number


def request_broker(payload: dict, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """Atomically submit one request and wait for its host-produced response."""
    requests = BROKER_ROOT / "requests"
    responses = BROKER_ROOT / "responses"
    if not requests.is_dir() or not responses.is_dir():
        raise SampleFreshdeskError("broker_unavailable", "The host Freshdesk broker is unavailable.")
    action_id = str(payload["action_id"])
    request_path = requests / f"{action_id}.json"
    response_path = responses / f"{action_id}.json"
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
                raise SampleFreshdeskError(
                    "action_id_conflict",
                    "This action id was already used for a different request.",
                )
            return response
        time.sleep(0.05)
    raise SampleFreshdeskError("broker_timeout", "The host Freshdesk broker did not respond in time.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freshdesk sample ticket operations for the current deal.")
    parser.add_argument("command", choices=("show", "sync", "note", "amend"))
    parser.add_argument("sample_id")
    parser.add_argument("--body", default="")
    parser.add_argument("--style-code", default="")
    parser.add_argument("--color", default="")
    parser.add_argument("--size", default="")
    parser.add_argument("--product-link", default="")
    parser.add_argument("--action-id", default="", help="Reuse the same id to make retries idempotent.")
    args = parser.parse_args()
    try:
        sample_id = positive_sample_id(args.sample_id)
        if args.command == "note" and not args.body.strip():
            raise SampleFreshdeskError("body_required", "note requires --body text.")
        if args.command == "amend" and not any(
            value.strip() for value in (args.style_code, args.color, args.size, args.product_link)
        ):
            raise SampleFreshdeskError(
                "fields_required",
                "amend requires --style-code, --color, --size, or --product-link.",
            )
        action_id = args.action_id.strip() or uuid.uuid4().hex
        if not action_id.replace("-", "").replace("_", "").isalnum() or len(action_id) > 100:
            raise SampleFreshdeskError("invalid_action_id", "Action id must be 1-100 safe characters.")
        result = request_broker({
            "action_id": action_id,
            "action": args.command,
            "sample_id": sample_id,
            "body": args.body,
            "style_code": args.style_code,
            "color": args.color,
            "size": args.size,
            "product_link": args.product_link,
        })
    except SampleFreshdeskError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}))
        sys.exit(1)
    print(json.dumps(result, default=str))
    if not result.get("ok", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
