"""Resolver configuration, independent of sales application destinations."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ssl_ca import bootstrap_deal_manager_env, resolve_ssl_ca_path

ROOT = Path(__file__).resolve().parents[1]


def env_value(name: str, *aliases: str) -> str:
    bootstrap_deal_manager_env()
    for key in (name, *aliases):
        found = os.environ.get(key, "").strip()
        if found:
            return found
    return ""


def required(name: str, *aliases: str) -> str:
    value = env_value(name, *aliases)
    if not value:
        label = name if not aliases else f"{name} (or {aliases[0]})"
        raise ValueError(f"{label} must be configured for deal lookup")
    return value


def required_schema(role: str) -> str:
    """Schema name for main / operations / crm roles."""
    role = role.upper()
    direct = env_value(f"DEAL_MANAGER_{role}_SCHEMA")
    if direct:
        return direct
    if role == "MAIN":
        return required(
            "DEAL_MANAGER_MAIN_SCHEMA",
            "NON_PROD_CRM_DB",
            "NON_PROD_V3_DB",
        )
    if role == "CRM":
        return required(
            "DEAL_MANAGER_CRM_SCHEMA",
            "NON_PROD_CRM_DB",
            "NON_PROD_V3_DB",
        )
    if role == "OPERATIONS":
        return required("DEAL_MANAGER_OPERATIONS_SCHEMA", "NON_PROD_SASHA_DB")
    raise ValueError(f"Unknown schema role {role}")


def export_database_environment() -> None:
    """Publish aliased .env values under the database helper's names for host-side queries.

    The helper stays standalone (it also runs inside the agent container), so the host
    resolves aliases such as NON_PROD_CRM_USER here instead of in the helper.
    """
    values = {
        "NON_PROD_USER": env_value("NON_PROD_USER", "NON_PROD_CRM_USER", "NON_PROD_V3_USER"),
        "NON_PROD_PASS": env_value("NON_PROD_PASS", "NON_PROD_CRM_PASS", "NON_PROD_V3_PASS"),
        "NON_PROD_SSL_CA": str(resolve_ssl_ca_path()),
        **{f"DEAL_MANAGER_{role}_SCHEMA": required_schema(role)
           for role in ("MAIN", "OPERATIONS", "CRM")},
    }
    for name, value in values.items():
        if value:
            os.environ.setdefault(name, value)


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
        bootstrap_deal_manager_env()
        certificate = resolve_ssl_ca_path()
        database = {
            "NON_PROD_HOST": required("NON_PROD_HOST"),
            "NON_PROD_USER": required("NON_PROD_USER", "NON_PROD_CRM_USER", "NON_PROD_V3_USER"),
            "NON_PROD_PASS": required("NON_PROD_PASS", "NON_PROD_CRM_PASS", "NON_PROD_V3_PASS"),
            "DEAL_MANAGER_MAIN_SCHEMA": required_schema("MAIN"),
            "DEAL_MANAGER_OPERATIONS_SCHEMA": required_schema("OPERATIONS"),
            "DEAL_MANAGER_CRM_SCHEMA": required_schema("CRM"),
        }
        for role in ("MAIN", "OPERATIONS", "CRM"):
            name = f"DEAL_MANAGER_{role}_SCHEMA"
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", database[name]):
                raise ValueError(f"{name} must contain 1–64 letters, digits, underscores or hyphens")
        database["NON_PROD_PORT"] = env_value("NON_PROD_PORT") or "3306"
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
