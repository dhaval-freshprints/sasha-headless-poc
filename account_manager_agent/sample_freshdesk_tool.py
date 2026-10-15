"""Agent-facing Freshdesk operations for open sample tickets on the current deal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from account_manager_agent.samples.service import SampleService

CONTEXT_PATH = Path("/opt/sasha/customer_link_context.json")


class SampleFreshdeskError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def deal_id_from_context() -> str:
    if not CONTEXT_PATH.is_file():
        raise SampleFreshdeskError("context_missing", "Sample Freshdesk tool context is not mounted.")
    context = json.loads(CONTEXT_PATH.read_text(encoding="utf-8"))
    deal_id = str(context.get("deal_id") or "").strip()
    if not deal_id.isdigit():
        raise SampleFreshdeskError("invalid_deal", "The current deal id is missing from tool context.")
    return deal_id


def positive_sample_id(value: str) -> int:
    if not str(value).isdigit():
        raise SampleFreshdeskError("invalid_sample_id", "Sample id must be a positive integer.")
    number = int(value)
    if number <= 0:
        raise SampleFreshdeskError("invalid_sample_id", "Sample id must be a positive integer.")
    return number


def run_command(command: str, sample_id: int, deal_id: str, body: str | None, private: bool,
                style_code: str | None, color: str | None, size: str | None) -> dict:
    service = SampleService()
    if command == "show":
        return service.freshdesk_show(deal_id, sample_id)
    if command == "sync":
        return service.freshdesk_sync_sample(deal_id, sample_id)
    if command == "reply":
        if not body or not body.strip():
            raise SampleFreshdeskError("body_required", "reply requires --body text.")
        return service.freshdesk_reply(deal_id, sample_id, body, private=private)
    if command == "amend":
        if not any((style_code, color, size)):
            raise SampleFreshdeskError(
                "fields_required",
                "amend requires at least one of --style-code, --color, or --size.",
            )
        return service.freshdesk_amend(
            deal_id,
            sample_id,
            style_code=style_code,
            color=color,
            size=size,
            message=body,
        )
    raise SampleFreshdeskError("unknown_command", f"Unknown command: {command}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freshdesk sample ticket operations for the current deal.")
    parser.add_argument("command", choices=("show", "sync", "reply", "amend"))
    parser.add_argument("sample_id")
    parser.add_argument("--body", default="")
    parser.add_argument("--private", action="store_true", help="Private note instead of a public thread reply.")
    parser.add_argument("--style-code", default="")
    parser.add_argument("--color", default="")
    parser.add_argument("--size", default="")
    args = parser.parse_args()
    try:
        deal_id = deal_id_from_context()
        sample_id = positive_sample_id(args.sample_id)
        result = run_command(
            args.command,
            sample_id,
            deal_id,
            args.body or None,
            args.private,
            args.style_code or None,
            args.color or None,
            args.size or None,
        )
    except SampleFreshdeskError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}))
        sys.exit(1)
    print(json.dumps(result, default=str))
    if not result.get("ok", True):
        sys.exit(1)


if __name__ == "__main__":
    main()
