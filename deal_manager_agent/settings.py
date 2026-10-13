"""Resolver configuration, independent of sales application destinations."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be configured for deal lookup")
    return value


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
    certificate: Path
    connection_timeout: float = 90
    turn_timeout: float = 600

    @property
    def schemas(self) -> dict[str, str]:
        return {role: self.database[f"DEAL_MANAGER_{role.upper()}_SCHEMA"]
                for role in ("main", "operations", "crm")}

    @classmethod
    def from_environment(cls) -> "DealManagerSettings":
        certificate = Path(required("NON_PROD_SSL_CA")).expanduser().resolve()
        if not certificate.is_file():
            raise ValueError("NON_PROD_SSL_CA must point to a readable RDS CA bundle")
        database = {name: required(name) for name in (
            "NON_PROD_HOST", "NON_PROD_USER", "NON_PROD_PASS",
            "DEAL_MANAGER_MAIN_SCHEMA", "DEAL_MANAGER_OPERATIONS_SCHEMA", "DEAL_MANAGER_CRM_SCHEMA",
        )}
        for role in ("MAIN", "OPERATIONS", "CRM"):
            name = f"DEAL_MANAGER_{role}_SCHEMA"
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", database[name]):
                raise ValueError(f"{name} must contain 1–64 letters, digits, underscores or hyphens")
        database["NON_PROD_PORT"] = os.environ.get("NON_PROD_PORT", "3306")
        port = int(database["NON_PROD_PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("NON_PROD_PORT must be between 1 and 65535")
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
            certificate=certificate,
            turn_timeout=timeout,
        )
