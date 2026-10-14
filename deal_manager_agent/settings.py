"""Resolver configuration, independent of sales application destinations."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from sasha_database.config import (
    DatabaseSettings, bootstrap_deal_manager_env, env_value, required, required_schema,
    export_database_environment,
)

ROOT = Path(__file__).resolve().parents[1]


def storage_directory() -> Path:
    return Path(os.environ.get("DEAL_MANAGER_DIRECTORY", ROOT / "runs" / "deal-manager-agent")).expanduser()


@dataclass(frozen=True)
class DealManagerSettings:
    model: str
    reasoning_effort: str
    executor_api_key: str = field(repr=False)
    image: str
    directory: Path
    database: dict[str, str] = field(repr=False)
    connection_timeout: float = 90
    turn_timeout: float = 600

    @property
    def schemas(self) -> dict[str, str]:
        return {role: self.database[f"DEAL_MANAGER_{role.upper()}_SCHEMA"]
                for role in ("main", "operations")}

    @classmethod
    def from_environment(cls) -> "DealManagerSettings":
        bootstrap_deal_manager_env()
        configuration = DatabaseSettings.from_environment()
        database = configuration.values
        timeout = float(os.environ.get("DEAL_MANAGER_TIMEOUT_SECONDS", "600"))
        if not 1 <= timeout <= 3600:
            raise ValueError("DEAL_MANAGER_TIMEOUT_SECONDS must be between 1 and 3600")
        return cls(
            model=required("OPENAI_AGENT_MODEL"),
            reasoning_effort=os.environ.get("OPENAI_AGENT_REASONING_EFFORT", "medium"),
            executor_api_key=required("OPENAI_EXECUTOR_API_KEY"),
            image=os.environ.get("DEAL_MANAGER_IMAGE", "deal-manager-agent:local"),
            directory=storage_directory() / "executions",
            database=database,
            turn_timeout=timeout,
        )
