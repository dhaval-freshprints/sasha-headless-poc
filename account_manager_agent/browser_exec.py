"""Serialized, timeout-bounded browser command runner for the Account Manager sandbox.

Codex writes short Playwright scripts under /workspace and runs them through this wrapper.
Only one browser command runs at a time. When CDP is unhealthy, the wrapper restarts the
kept-open browser once and retries the command once.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

WORKSPACE = Path("/workspace")
ARTIFACTS = WORKSPACE / "artifacts"
LOCK_PATH = ARTIFACTS / "browser.lock"
DIAGNOSTICS_PATH = ARTIFACTS / "browser_diagnostics.json"
CDP_VERSION_URL = "http://127.0.0.1:9222/json/version"
START_BROWSER = ["node", "/opt/sasha/start_browser.js"]
DEFAULT_TIMEOUT_SECONDS = 90
LOCK_WAIT_SECONDS = 120
CDP_TIMEOUT_SECONDS = 2.0
CLIENT_FILES_ROOT = PurePosixPath("/workspace/client-files")
SET_INPUT_FILES_CALL = re.compile(r"\.setInputFiles\s*\(")
STRING_LITERAL = re.compile(r"""(?P<quote>['"`])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)""")
FORBIDDEN_UPLOAD = re.compile(
    r"\b(?:Buffer|createReadStream|readFileSync|readFile)\b|buffer\s*:",
    re.IGNORECASE,
)
PATH_LIKE = re.compile(
    r"^(?:/|\./|\.\./)|[/\\]|\.(?:png|jpe?g|gif|webp|svg|pdf|ai|eps)$",
    re.IGNORECASE,
)


class BrowserExecError(Exception):
    def __init__(self, code: str, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def workspace_script(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = WORKSPACE / path
    path = path.resolve()
    workspace = WORKSPACE.resolve()
    if path != workspace and workspace not in path.parents:
        raise BrowserExecError("invalid_script", "Browser scripts must live under /workspace.")
    if path.suffix not in {".js", ".cjs", ".mjs"}:
        raise BrowserExecError("invalid_script", "Browser scripts must be Node.js files.")
    if not path.is_file():
        raise BrowserExecError("missing_script", f"Script not found: {path}")
    return path


def _call_argument_text(source: str, open_paren_index: int) -> str:
    depth = 0
    index = open_paren_index
    while index < len(source):
        character = source[index]
        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    break
                index += 1
            index += 1
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return source[open_paren_index + 1 : index]
        index += 1
    raise BrowserExecError(
        "invalid_upload_path",
        "Unclosed setInputFiles call; upload paths could not be validated.",
    )


def approved_client_file_path(value: str) -> bool:
    """Return True when value is a literal path under /workspace/client-files."""
    candidate = value.strip()
    if not candidate or ".." in candidate or "$" in candidate:
        return False
    path = PurePosixPath(candidate)
    if not path.is_absolute():
        return False
    try:
        relative = path.relative_to(CLIENT_FILES_ROOT)
    except ValueError:
        return False
    return bool(relative.parts)


def validate_upload_paths(script: Path) -> None:
    """Reject Playwright uploads that are not literal /workspace/client-files paths."""
    source = script.read_text(encoding="utf-8")
    if "setInputFiles" not in source:
        return
    found_call = False
    for match in SET_INPUT_FILES_CALL.finditer(source):
        found_call = True
        arguments = _call_argument_text(source, match.end() - 1)
        if FORBIDDEN_UPLOAD.search(arguments):
            raise BrowserExecError(
                "invalid_upload_path",
                "setInputFiles may only use literal paths under /workspace/client-files; "
                "buffers and dynamic file content are blocked.",
                {"script": str(script)},
            )
        literals = [item.group("value") for item in STRING_LITERAL.finditer(arguments)]
        path_literals = [value for value in literals if PATH_LIKE.search(value)]
        if not path_literals:
            raise BrowserExecError(
                "invalid_upload_path",
                "setInputFiles requires a literal path under /workspace/client-files.",
                {"script": str(script)},
            )
        for value in path_literals:
            if "${" in value or not approved_client_file_path(value):
                raise BrowserExecError(
                    "invalid_upload_path",
                    "Uploads must use literal paths directly under /workspace/client-files.",
                    {"script": str(script), "path": value},
                )
    if "setInputFiles" in source and not found_call:
        raise BrowserExecError(
            "invalid_upload_path",
            "setInputFiles usage could not be validated; use locator.setInputFiles('/workspace/client-files/...').",
            {"script": str(script)},
        )


def cdp_health() -> dict:
    try:
        with urllib.request.urlopen(CDP_VERSION_URL, timeout=CDP_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
        return {
            "ok": True,
            "browser": payload.get("Browser"),
            "webSocketDebuggerUrl": payload.get("webSocketDebuggerUrl"),
        }
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
        return {"ok": False, "error": str(error)}


def process_snapshot() -> list[str]:
    try:
        process = subprocess.run(
            ["ps", "-eo", "pid,ppid,pcpu,pmem,etime,comm,args"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    lines = []
    for line in process.stdout.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("chrome", "playwright", "browser_keeper", "node")):
            lines.append(line[:500])
        if len(lines) >= 40:
            break
    return lines


def write_diagnostics(payload: dict) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    history = []
    if DIAGNOSTICS_PATH.is_file():
        try:
            existing = json.loads(DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                history = existing
        except (OSError, ValueError, json.JSONDecodeError):
            history = []
    history.append(payload)
    DIAGNOSTICS_PATH.write_text(json.dumps(history[-20:], indent=2), encoding="utf-8")


def acquire_lock(timeout_seconds: float = LOCK_WAIT_SECONDS) -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, f"{os.getpid()}\n".encode())
            return fd
        except FileExistsError:
            try:
                owner = int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0])
                os.kill(owner, 0)
            except (OSError, ValueError, IndexError):
                LOCK_PATH.unlink(missing_ok=True)
                continue
            time.sleep(0.1)
    raise BrowserExecError("lock_timeout", "Timed out waiting for the browser lock.")


def release_lock(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def restart_browser() -> dict:
    process = subprocess.run(
        START_BROWSER + ["--restart"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"status": "failed", "reason": (process.stderr or process.stdout or "").strip()}
    payload["exit_code"] = process.returncode
    return payload


def run_script(script: Path, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(script)],
        cwd=str(WORKSPACE),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def execute(script: Path, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    validate_upload_paths(script)
    fd = acquire_lock()
    started = time.time()
    restarted = False
    try:
        health = cdp_health()
        if not health.get("ok"):
            restart = restart_browser()
            restarted = True
            health = cdp_health()
            if not health.get("ok"):
                diagnostics = {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "script": str(script),
                    "cdp": health,
                    "restart": restart,
                    "processes": process_snapshot(),
                }
                write_diagnostics(diagnostics)
                raise BrowserExecError(
                    "browser_unavailable",
                    "Chrome CDP did not answer after one restart.",
                    diagnostics,
                )
        try:
            process = run_script(script, timeout_seconds)
        except subprocess.TimeoutExpired as error:
            diagnostics = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "script": str(script),
                "timeout_seconds": timeout_seconds,
                "cdp": cdp_health(),
                "processes": process_snapshot(),
                "stdout": (error.stdout or "")[-4000:],
                "stderr": (error.stderr or "")[-4000:],
            }
            write_diagnostics(diagnostics)
            raise BrowserExecError(
                "command_timeout",
                f"Browser command exceeded {timeout_seconds:.0f}s.",
                diagnostics,
            ) from error

        if process.returncode != 0 and not restarted and not cdp_health().get("ok"):
            restart = restart_browser()
            restarted = True
            if cdp_health().get("ok"):
                try:
                    process = run_script(script, timeout_seconds)
                except subprocess.TimeoutExpired as error:
                    diagnostics = {
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "script": str(script),
                        "timeout_seconds": timeout_seconds,
                        "restart": restart,
                        "cdp": cdp_health(),
                        "processes": process_snapshot(),
                        "stdout": (error.stdout or "")[-4000:],
                        "stderr": (error.stderr or "")[-4000:],
                    }
                    write_diagnostics(diagnostics)
                    raise BrowserExecError(
                        "command_timeout",
                        f"Browser command exceeded {timeout_seconds:.0f}s after restart.",
                        diagnostics,
                    ) from error

        result = {
            "ok": process.returncode == 0,
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "restarted": restarted,
            "elapsed_seconds": round(time.time() - started, 3),
            "script": str(script),
        }
        if process.returncode != 0:
            diagnostics = {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **result,
                "cdp": cdp_health(),
                "processes": process_snapshot(),
            }
            write_diagnostics(diagnostics)
            result["diagnostics"] = diagnostics
        return result
    finally:
        release_lock(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Account Manager browser script safely.")
    parser.add_argument("script", help="Path under /workspace to a Node Playwright script")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Hard timeout for the script (default 90).",
    )
    args = parser.parse_args()
    try:
        script = workspace_script(args.script)
        if args.timeout_seconds <= 0 or args.timeout_seconds > 300:
            raise BrowserExecError("invalid_timeout", "timeout-seconds must be between 0 and 300.")
        result = execute(script, args.timeout_seconds)
    except BrowserExecError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error), **error.diagnostics}))
        sys.exit(1)
    sys.stdout.write(result.get("stdout") or "")
    if result.get("stderr"):
        sys.stderr.write(result["stderr"])
    if not result["ok"]:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "command_failed",
                    "exit_code": result["exit_code"],
                    "restarted": result["restarted"],
                    "script": result["script"],
                }
            ),
            file=sys.stderr,
        )
        sys.exit(result["exit_code"] or 1)


if __name__ == "__main__":
    # Avoid leaving orphaned child processes when the wrapper is killed.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    main()
