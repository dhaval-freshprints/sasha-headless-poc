"""Resolve AM's optional database access using the existing Deal Manager settings."""

from dataclasses import dataclass, field
from pathlib import Path
import re

from deal_manager_agent.settings import env_value, required, required_schema
from deal_manager_agent.ssl_ca import resolve_ssl_ca_path


@dataclass(frozen=True)
class CustomerLinkDatabase:
    values: dict[str, str] = field(repr=False)
    certificate: Path


def load_customer_link_database() -> CustomerLinkDatabase | None:
    """Incomplete settings disable only the helper, not ordinary sales work."""
    try:
        values = {
            "NON_PROD_HOST": required("NON_PROD_HOST"),
            "NON_PROD_USER": required("NON_PROD_USER", "NON_PROD_CRM_USER", "NON_PROD_V3_USER"),
            "NON_PROD_PASS": required("NON_PROD_PASS", "NON_PROD_CRM_PASS", "NON_PROD_V3_PASS"),
            "NON_PROD_PORT": env_value("NON_PROD_PORT") or "3306",
        }
        for role in ("MAIN", "OPERATIONS", "CRM"):
            schema = required_schema(role)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", schema):
                return None
            values[f"DEAL_MANAGER_{role}_SCHEMA"] = schema
        if not 1 <= int(values["NON_PROD_PORT"]) <= 65535:
            return None
        return CustomerLinkDatabase(values, resolve_ssl_ca_path())
    except (ValueError, OSError):
        return None
