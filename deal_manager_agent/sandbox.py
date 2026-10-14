"""Database-only workspace using the existing Codex executor connection."""

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from openai_managed.sandbox import DockerSandbox, SandboxConfig, SandboxHandle

from sasha_database.config import DatabaseSettings, container_database_options

from .models import LookupRequest
from .settings import DealManagerSettings


PACKAGE = Path(__file__).resolve().parent


class DealManagerSandbox(DockerSandbox):
    def __init__(self, settings: DealManagerSettings):
        super().__init__(SandboxConfig(settings.image, settings.directory), settings.executor_api_key)
        self.settings = settings

    def prepare(self, request: LookupRequest) -> SandboxHandle:
        directory = (self.settings.directory / uuid.uuid4().hex).resolve()
        workspace = directory / "workspace"
        workspace.mkdir(parents=True)
        self.handle = SandboxHandle(f"deal-manager-{uuid.uuid4().hex[:12]}", directory, workspace)
        for name in ("deal-manager-database", "deal-manager-resolve-deal"):
            shutil.copytree(PACKAGE / "skills" / name, workspace / "capabilities" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (workspace / "message.json").write_text(json.dumps({
            **request.model_dump(exclude_none=True), "channel": request.channel,
            "received_at": None,
        }, indent=2), encoding="utf-8")
        (workspace / "database_schemas.json").write_text(
            json.dumps(self.settings.schemas, indent=2), encoding="utf-8",
        )
        return self.handle

    def start_container(self) -> None:
        handle = self._require_handle()
        options, environment = container_database_options(
            DatabaseSettings(self.settings.database))
        subprocess.run([
            "docker", "run", "--detach", "--rm", "--name", handle.container_name,
            "--memory", "1g", "--cpus", "1",
            "--mount", f"type=bind,src={handle.workspace_directory},dst=/workspace",
            *options,
            self.config.image, "sleep", "infinity",
        ], check=True, capture_output=True, text=True, env=environment)
