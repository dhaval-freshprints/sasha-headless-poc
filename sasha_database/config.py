"""Shared database settings and container environment configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ENV_BOOTSTRAPPED = False


def bootstrap_deal_manager_env() -> None:
    """Load repo `.env` once so background threads see database settings."""
    global _ENV_BOOTSTRAPPED
    if _ENV_BOOTSTRAPPED:
        return
    path = ROOT / ".env"
    if path.is_file():
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    _ENV_BOOTSTRAPPED = True


SCHEMA_VARIABLES = {
    "main": "DEAL_MANAGER_MAIN_SCHEMA",
    "operations": "DEAL_MANAGER_OPERATIONS_SCHEMA",
}
DATABASE_ENVIRONMENT_VARIABLES = (
    "NON_PROD_HOST", "NON_PROD_PORT", "NON_PROD_USER", "NON_PROD_PASS",
    *SCHEMA_VARIABLES.values(),
)


def env_value(name: str, *aliases: str) -> str:
    bootstrap_deal_manager_env()
    for key in (name, *aliases):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def required(name: str, *aliases: str) -> str:
    value = env_value(name, *aliases)
    if not value:
        raise ValueError(f"{name} must be configured")
    return value


def quote_schema(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Schema names must contain 1–64 letters, digits, underscores or hyphens")
    return f"`{value}`"


def required_schema(role: str) -> str:
    role = role.upper()
    aliases = {
        "MAIN": ("NON_PROD_CRM_DB", "NON_PROD_V3_DB"),
        "OPERATIONS": ("NON_PROD_SASHA_DB",),
    }
    if role not in aliases:
        raise ValueError(f"Unknown schema role {role}")
    value = required(f"DEAL_MANAGER_{role}_SCHEMA", *aliases[role])
    quote_schema(value)
    return value


def configured_schemas() -> dict[str, str]:
    return {role: required_schema(role) for role in SCHEMA_VARIABLES}


@dataclass(frozen=True)
class DatabaseSettings:
    values: dict[str, str] = field(repr=False)

    @classmethod
    def from_environment(cls):
        values = {
            "NON_PROD_HOST": required("NON_PROD_HOST"),
            "NON_PROD_PORT": env_value("NON_PROD_PORT") or "3306",
            "NON_PROD_USER": required("NON_PROD_USER", "NON_PROD_CRM_USER", "NON_PROD_V3_USER"),
            "NON_PROD_PASS": required("NON_PROD_PASS", "NON_PROD_CRM_PASS", "NON_PROD_V3_PASS"),
            **{variable: required_schema(role) for role, variable in SCHEMA_VARIABLES.items()},
        }
        if not 1 <= int(values["NON_PROD_PORT"]) <= 65535:
            raise ValueError("NON_PROD_PORT must be between 1 and 65535")
        return cls(values)


def container_database_options(settings: DatabaseSettings) -> tuple[list[str], dict[str, str]]:
    """Only named database variables enter the container; values never enter argv."""
    values = {name: settings.values[name] for name in DATABASE_ENVIRONMENT_VARIABLES
              if name in settings.values}
    options = [
        "--mount", f"type=bind,src={Path(__file__).resolve().parent},dst=/opt/sasha_database,readonly",
        "--env", "PYTHONPATH=/opt",
    ]
    for name in values:
        options.extend(["--env", name])
    return options, {**os.environ, **values}


def export_database_environment() -> None:
    settings = DatabaseSettings.from_environment()
    for name, value in settings.values.items():
        os.environ.setdefault(name, value)
