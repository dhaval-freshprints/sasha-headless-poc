"""Host-side broker for deal-scoped Freshdesk actions requested by the sandbox."""

from __future__ import annotations

import json
import hashlib
import re
import threading
from pathlib import Path
from typing import Callable

from .service import SampleService

ACTION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
SUPPORTED_ACTIONS = {"show", "sync", "note", "amend"}


class SampleFreshdeskBroker:
    """Execute sandbox requests on the host so Freshdesk credentials stay outside Codex."""

    def __init__(
        self,
        workspace: Path,
        deal_id: str,
        *,
        service_factory: Callable[[], SampleService] | None = None,
        poll_seconds: float = 0.05,
    ) -> None:
        self.deal_id = str(deal_id)
        self.root = workspace / ".sample-freshdesk"
        self.requests = self.root / "requests"
        self.responses = self.root / "responses"
        self.service_factory = service_factory or SampleService
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"sample-freshdesk-{self.deal_id}",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            for request_path in sorted(self.requests.glob("*.json")):
                self._process(request_path)
            self.stop_event.wait(self.poll_seconds)

    def _process(self, request_path: Path) -> None:
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            action_id = str(request.get("action_id") or "")
            if not ACTION_ID.fullmatch(action_id) or request_path.stem != action_id:
                raise ValueError("invalid_action_id")
            response_path = self.responses / f"{action_id}.json"
            if response_path.exists():
                request_path.unlink(missing_ok=True)
                return
            result = self._execute(request)
        except Exception as error:
            action_id = request_path.stem if ACTION_ID.fullmatch(request_path.stem) else "invalid"
            response_path = self.responses / f"{action_id}.json"
            result = {"ok": False, "error": str(error)}
        result["request_fingerprint"] = _request_fingerprint(
            request if "request" in locals() else {"action_id": action_id}
        )
        temporary = response_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, default=str), encoding="utf-8")
        temporary.replace(response_path)
        request_path.unlink(missing_ok=True)

    def _execute(self, request: dict) -> dict:
        action = str(request.get("action") or "")
        if action not in SUPPORTED_ACTIONS:
            return {"ok": False, "error": "unsupported_action"}
        try:
            sample_id = int(request["sample_id"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "invalid_sample_id"}
        if sample_id <= 0:
            return {"ok": False, "error": "invalid_sample_id"}

        service = self.service_factory()
        if action == "show":
            return service.freshdesk_show(self.deal_id, sample_id)
        if action == "sync":
            return service.freshdesk_sync_sample(self.deal_id, sample_id)
        if action == "note":
            body = str(request.get("body") or "").strip()
            if not body:
                return {"ok": False, "error": "body_required"}
            return service.freshdesk_note(
                self.deal_id,
                sample_id,
                body,
                action_id=str(request["action_id"]),
            )
        return service.freshdesk_amend(
            self.deal_id,
            sample_id,
            style_code=_optional_text(request, "style_code"),
            color=_optional_text(request, "color"),
            size=_optional_text(request, "size"),
            product_link=_optional_text(request, "product_link"),
            message=_optional_text(request, "body"),
            action_id=str(request["action_id"]),
        )


def _optional_text(value: dict, key: str) -> str | None:
    text = str(value.get(key) or "").strip()
    return text or None


def _request_fingerprint(request: dict) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
