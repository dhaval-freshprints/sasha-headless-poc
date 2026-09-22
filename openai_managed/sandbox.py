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
from urllib.parse import urlparse

from .task import SashaTask


SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")
SASHA_SKILL_DIRECTORY = Path(__file__).with_name("capabilities") / "sasha-sales"


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    auth_directory: Path
    runs_directory: Path


@dataclass(frozen=True)
class SandboxHandle:
    container_name: str
    run_directory: Path
    workspace_directory: Path
    browser_profile_directory: Path


class DockerSandbox:
    def __init__(self, config: SandboxConfig, executor_api_key: str) -> None:
        self.config = config
        self.executor_api_key = executor_api_key.strip()
        self.handle: SandboxHandle | None = None
        if not self.executor_api_key:
            raise ValueError("OPENAI_EXECUTOR_API_KEY must be set")

    def prepare(self, task: SashaTask, task_message: str) -> SandboxHandle:
        auth_directory = self.config.auth_directory.resolve()
        if not auth_directory.is_dir() or not any(auth_directory.iterdir()):
            raise FileNotFoundError(
                f"Managed browser profile not found at {auth_directory}. "
                "Run scripts/setup_openai_managed_auth.py first."
            )

        safe_task_id = SAFE_NAME.sub("-", task.task_id).strip("-.") or "sasha"
        run_directory = (
            self.config.runs_directory / f"{safe_task_id}-{uuid.uuid4().hex[:8]}"
        ).resolve()
        workspace_directory = run_directory / "workspace"
        profile_directory = workspace_directory / "browser-profile"
        capabilities_directory = workspace_directory / "capabilities"

        workspace_directory.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copytree(auth_directory, profile_directory)
            (workspace_directory / "artifacts").mkdir()
            capabilities_directory.mkdir()
            shutil.copytree(
                SASHA_SKILL_DIRECTORY,
                capabilities_directory / "sasha-sales",
            )
            (workspace_directory / "TASK.md").write_text(task_message, encoding="utf-8")
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
        except Exception:
            shutil.rmtree(run_directory)
            raise

        self.handle = SandboxHandle(
            container_name=f"sasha-managed-{uuid.uuid4().hex[:12]}",
            run_directory=run_directory,
            workspace_directory=workspace_directory,
            browser_profile_directory=profile_directory,
        )
        return self.handle

    def start(self, environment_id: str, remote_url: str) -> None:
        handle = self._require_handle()
        parsed_url = urlparse(remote_url)
        if parsed_url.scheme not in {"https", "wss"} or not parsed_url.netloc:
            raise ValueError("Agents API returned an invalid executor remote URL")
        if not environment_id.strip():
            raise ValueError("Agents API returned an empty environment ID")

        process_environment = dict(os.environ)
        process_environment["CODEX_API_KEY"] = self.executor_api_key
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
                "--env",
                "CODEX_API_KEY",
                "--mount",
                f"type=bind,src={handle.workspace_directory},dst=/workspace",
                self.config.image,
                "codex",
                "exec-server",
                "--remote",
                remote_url,
                "--environment-id",
                environment_id,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=process_environment,
        )

    def logs(self) -> str:
        handle = self._require_handle()
        process = subprocess.run(
            ["docker", "logs", "--tail", "300", handle.container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return (process.stdout + process.stderr).strip()

    def stop(self) -> None:
        if self.handle is None:
            return
        subprocess.run(
            ["docker", "stop", "--time", "10", self.handle.container_name],
            check=False,
            capture_output=True,
            text=True,
        )

    def remove_browser_profile(self) -> None:
        if self.handle is None:
            return
        profile = self.handle.browser_profile_directory.resolve()
        workspace = self.handle.workspace_directory.resolve()
        if profile.parent != workspace:
            raise RuntimeError("Refusing to remove a profile outside the run workspace")
        if profile.exists():
            shutil.rmtree(profile)

    def _require_handle(self) -> SandboxHandle:
        if self.handle is None:
            raise RuntimeError("prepare must be called before using the sandbox")
        return self.handle
