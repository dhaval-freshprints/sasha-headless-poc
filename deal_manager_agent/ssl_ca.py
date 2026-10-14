"""Resolve the RDS TLS CA bundle for MySQL connections."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ENV_BOOTSTRAPPED = False


def bootstrap_deal_manager_env() -> None:
    """Load repo `.env` once so background threads see CRM settings."""
    global _ENV_BOOTSTRAPPED
    if _ENV_BOOTSTRAPPED:
        return
    path = ROOT / ".env"
    if path.is_file():
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
    _ENV_BOOTSTRAPPED = True


def resolve_ssl_ca_path() -> Path:
    """Return a readable CA bundle path from NON_PROD_SSL_CA or repo-local defaults."""
    bootstrap_deal_manager_env()
    raw = os.environ.get("NON_PROD_SSL_CA", "").strip()
    candidates: list[Path] = []
    if raw:
        path = Path(raw).expanduser()
        candidates.append(path if path.is_absolute() else ROOT / path)
    else:
        candidates.extend(sorted(ROOT.glob("*_rds_ca.pem")))
        for name in ("global-bundle.pem", "rds-combined-ca-bundle.pem", "rds-ca.pem"):
            candidates.append(ROOT / name)
    for path in candidates:
        resolved = path.resolve()
        if resolved.is_file():
            return resolved
    if raw:
        raise ValueError(
            f"NON_PROD_SSL_CA must point to a readable RDS CA bundle (tried {raw!r})"
        )
    raise ValueError(
        "NON_PROD_SSL_CA must be configured for deal lookup "
        f"(set it in {ROOT / '.env'} or place a *_rds_ca.pem file in {ROOT})"
    )


def ensure_ssl_ca_env() -> Path:
    """Resolve the CA bundle and mirror it into NON_PROD_SSL_CA for PyMySQL."""
    path = resolve_ssl_ca_path()
    os.environ["NON_PROD_SSL_CA"] = str(path)
    return path
