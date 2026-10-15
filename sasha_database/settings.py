"""Ops-tunable values from Sasha v3's sasha_settings table (key, value, status ACTIVE/INACTIVE)."""

from __future__ import annotations

from .config import DatabaseSettings
from .connection import transaction


class SashaSettings:
    def __init__(self, settings=None, transaction_factory=transaction):
        self.settings = settings or DatabaseSettings.from_environment()
        self.transaction = transaction_factory

    def values(self, *keys: str) -> dict[str, str]:
        """Active values for these keys; missing or inactive keys are left out."""
        placeholders = ",".join(["%s"] * len(keys))
        with self.transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT `key`,`value` FROM sasha_settings WHERE `key` IN ({placeholders}) "
                               "AND status='ACTIVE'", keys)
                return {row["key"]: str(row["value"]).strip() for row in cursor.fetchall()}
