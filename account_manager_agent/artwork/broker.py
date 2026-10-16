"""Host-side broker for artwork extraction requested by the sandbox."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path, PurePosixPath
from typing import Callable

from .constants import (
    ACTION_ID,
    BROKER_DIR_NAME,
    CLIENT_FILES_NAME,
    DEFAULT_WORKSPACE,
    REQUESTS_DIR_NAME,
    RESPONSES_DIR_NAME,
    SHAPES,
)
from .service import ExtractionError, extract
from .settings import ArtworkSettings


class ArtworkExtractBroker:
    """Run sandbox extract requests on the host so OpenAI credentials stay outside Codex."""

    def __init__(
        self,
        workspace: Path,
        settings: ArtworkSettings,
        *,
        extract_fn: Callable[..., dict] | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.settings = settings
        self.root = self.workspace / BROKER_DIR_NAME
        self.requests = self.root / REQUESTS_DIR_NAME
        self.responses = self.root / RESPONSES_DIR_NAME
        self.extract_fn = extract_fn or extract
        self.poll_seconds = (
            poll_seconds if poll_seconds is not None else settings.poll_seconds
        )
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.requests.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="artwork-extract-broker",
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
        request: dict | None = None
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
            if isinstance(error, ExtractionError):
                result = {"ok": False, "error": error.code, "message": str(error)}
            else:
                result = {"ok": False, "error": "broker_error", "message": str(error)}
        result["request_fingerprint"] = _request_fingerprint(
            request if request is not None else {"action_id": action_id}
        )
        temporary = response_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, default=str), encoding="utf-8")
        temporary.replace(response_path)
        request_path.unlink(missing_ok=True)

    def _execute(self, request: dict) -> dict:
        action_id = str(request.get("action_id") or "")
        if not ACTION_ID.fullmatch(action_id):
            return {"ok": False, "error": "invalid_action_id", "message": "Invalid action id."}
        shape = str(request.get("shape") or "auto")
        if shape != "auto" and shape not in SHAPES:
            return {"ok": False, "error": "invalid_shape", "message": "Unsupported shape."}
        file_path = str(request.get("file") or "").strip()
        if not file_path:
            return {"ok": False, "error": "invalid_file", "message": "A file path is required."}
        container_path = PurePosixPath(file_path)
        expected_parent = PurePosixPath(DEFAULT_WORKSPACE) / CLIENT_FILES_NAME
        if container_path.parent != expected_parent or container_path.name in {"", ".", ".."}:
            return {
                "ok": False,
                "error": "invalid_file",
                "message": f"Use a supplied file directly under {expected_parent}.",
            }
        host_path = self.workspace / CLIENT_FILES_NAME / container_path.name
        keep = str(request.get("keep") or "")
        try:
            result = self.extract_fn(
                str(host_path),
                keep,
                shape,
                workspace=self.workspace,
                api_key=self.settings.api_key,
                model=self.settings.model,
                api_url=self.settings.api_url,
                timeout_seconds=self.settings.timeout_seconds,
            )
            return self._containerize_paths(result)
        except ExtractionError as error:
            return {"ok": False, "error": error.code, "message": str(error)}

    def _containerize_paths(self, result: dict) -> dict:
        converted = dict(result)
        workspace = self.workspace.resolve()
        for field in ("source", "path", "artifact"):
            value = converted.get(field)
            if not value:
                continue
            host_path = Path(str(value)).resolve()
            try:
                relative = host_path.relative_to(workspace)
            except ValueError as error:
                raise ExtractionError(
                    "invalid_output",
                    f"Artwork service returned {field} outside the run workspace.",
                ) from error
            converted[field] = str(PurePosixPath(DEFAULT_WORKSPACE) / relative.as_posix())
        return converted


def _request_fingerprint(request: dict) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
