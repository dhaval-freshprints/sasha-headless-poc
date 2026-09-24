"""Disposable Docker environment for one managed Sasha turn."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.parse import urlparse

from .task import SashaTask


SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")
SASHA_SKILL_DIRECTORY = Path(__file__).with_name("capabilities") / "sasha-sales"
AUTHENTICATION_SCRIPT = Path(__file__).with_name("setup_auth.js")
BROWSER_KEEPER_SCRIPT = Path(__file__).with_name("browser_keeper.js")
START_BROWSER_SCRIPT = Path(__file__).with_name("start_browser.js")
BROWSER_READY_STATUSES = {"started", "running"}


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    runs_directory: Path


@dataclass(frozen=True)
class SandboxHandle:
    container_name: str
    run_directory: Path
    workspace_directory: Path


class DockerSandbox:
    def __init__(self, config: SandboxConfig, executor_api_key: str) -> None:
        self.config = config
        self.executor_api_key = executor_api_key.strip()
        self.handle: SandboxHandle | None = None
        self.executor_process: subprocess.Popen[str] | None = None
        self.executor_log: IO[str] | None = None
        if not self.executor_api_key:
            raise ValueError("OPENAI_EXECUTOR_API_KEY must be set")

    def prepare(self, task: SashaTask) -> SandboxHandle:
        safe_task_id = SAFE_NAME.sub("-", task.task_id).strip("-.") or "sasha"
        run_directory = (
            self.config.runs_directory / f"{safe_task_id}-{uuid.uuid4().hex[:8]}"
        ).resolve()
        workspace_directory = run_directory / "workspace"
        capabilities_directory = workspace_directory / "capabilities"

        workspace_directory.mkdir(parents=True, exist_ok=False)
        try:
            (workspace_directory / "artifacts").mkdir()
            capabilities_directory.mkdir()
            shutil.copytree(
                SASHA_SKILL_DIRECTORY,
                capabilities_directory / "sasha-sales",
            )
            (workspace_directory / "task.json").write_text(
                json.dumps(
                    {
                        "deal_id": task.deal_id,
                        "task_id": task.task_id,
                        "deal_url": task.deal_url,
                        "client_message": task.client_message,
                        "conversation_history": list(task.conversation_history),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (workspace_directory / "executor.log").touch()
        except Exception:
            shutil.rmtree(run_directory)
            raise

        self.handle = SandboxHandle(
            container_name=f"sasha-managed-{uuid.uuid4().hex[:12]}",
            run_directory=run_directory,
            workspace_directory=workspace_directory,
        )
        return self.handle

    def save_task_message(self, task_message: str) -> None:
        handle = self._require_handle()
        (handle.workspace_directory / "TASK.md").write_text(
            task_message, encoding="utf-8"
        )

    def remove_client_files(self) -> None:
        handle = self._require_handle()
        directory = handle.workspace_directory / "client-files"
        if directory.exists():
            shutil.rmtree(directory)

    def start_container(self) -> None:
        handle = self._require_handle()
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                handle.container_name,
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--shm-size",
                "1g",
                "--tmpfs",
                "/browser-profile:rw,nosuid,nodev,noexec,size=512m",
                "--mount",
                f"type=bind,src={handle.workspace_directory},dst=/workspace",
                "--mount",
                f"type=bind,src={AUTHENTICATION_SCRIPT},dst=/opt/sasha/setup_auth.js,readonly",
                "--mount",
                f"type=bind,src={BROWSER_KEEPER_SCRIPT},dst=/opt/sasha/browser_keeper.js,readonly",
                "--mount",
                f"type=bind,src={START_BROWSER_SCRIPT},dst=/opt/sasha/start_browser.js,readonly",
                self.config.image,
                "sleep",
                "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def authenticate(
        self,
        deal_url: str,
        login_url: str,
        login_user: str,
        login_password: str,
    ) -> None:
        handle = self._require_handle()
        credentials = json.dumps(
            {
                "deal_url": deal_url,
                "login_url": login_url,
                "login_user": login_user,
                "login_password": login_password,
            }
        )
        process = subprocess.run(
            [
                "docker",
                "exec",
                "--interactive",
                handle.container_name,
                "node",
                "/opt/sasha/setup_auth.js",
            ],
            input=credentials,
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            details = (process.stderr or process.stdout).strip()
            raise RuntimeError(f"QA authentication failed: {details}")
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("QA authentication returned invalid output") from error
        if result.get("status") != "authenticated":
            raise RuntimeError("QA authentication did not confirm the deal page")

    def start_browser(self) -> None:
        handle = self._require_handle()
        process = subprocess.run(
            [
                "docker",
                "exec",
                handle.container_name,
                "node",
                "/opt/sasha/start_browser.js",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            details = (process.stderr or process.stdout).strip()
            raise RuntimeError(f"Browser start returned invalid output: {details}") from error
        if result.get("status") not in BROWSER_READY_STATUSES:
            raise RuntimeError(f"Browser did not start: {result.get('reason', '')}")

    def connect_executor(self, environment_id: str, remote_url: str) -> None:
        handle = self._require_handle()
        parsed_url = urlparse(remote_url)
        if parsed_url.scheme not in {"https", "wss"} or not parsed_url.netloc:
            raise ValueError("Agents API returned an invalid executor remote URL")
        if not environment_id.strip():
            raise ValueError("Agents API returned an empty environment ID")

        process_environment = dict(os.environ)
        process_environment["CODEX_API_KEY"] = self.executor_api_key
        log_path = handle.workspace_directory / "executor.log"
        self.executor_log = log_path.open("w", encoding="utf-8")
        try:
            self.executor_process = subprocess.Popen(
                [
                    "docker",
                    "exec",
                    "--env",
                    "CODEX_API_KEY",
                    handle.container_name,
                    "codex",
                    "exec-server",
                    "--remote",
                    remote_url,
                    "--environment-id",
                    environment_id,
                ],
                stdout=self.executor_log,
                stderr=subprocess.STDOUT,
                text=True,
                env=process_environment,
            )
        except Exception:
            self.executor_log.close()
            self.executor_log = None
            raise

    def executor_exit_code(self) -> int | None:
        if self.executor_process is None:
            return None
        return self.executor_process.poll()

    def logs(self) -> str:
        handle = self._require_handle()
        if self.executor_log is not None:
            self.executor_log.flush()
        return (handle.workspace_directory / "executor.log").read_text(
            encoding="utf-8"
        )

    def stop(self) -> None:
        if self.handle is None:
            return
        subprocess.run(
            ["docker", "stop", "--time", "10", self.handle.container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if self.executor_process is not None:
            try:
                self.executor_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.executor_process.terminate()
                self.executor_process.wait(timeout=5)
            self.executor_process = None
        if self.executor_log is not None:
            self.executor_log.close()
            self.executor_log = None

    def _require_handle(self) -> SandboxHandle:
        if self.handle is None:
            raise RuntimeError("prepare must be called before using the sandbox")
        return self.handle
