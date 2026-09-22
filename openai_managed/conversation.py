"""Persistent per-deal conversation history for the managed Sasha POC."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any


DEAL_ID = re.compile(r"^[0-9]+$")


class ConversationStore:
    def __init__(self, runs_directory: Path, deal_id: str) -> None:
        if not DEAL_ID.fullmatch(deal_id):
            raise ValueError("deal_id must contain only digits")
        self.deal_id = deal_id
        self.path = (
            runs_directory
            / "conversations"
            / f"DEAL{deal_id}_conversation.json"
        )

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            self._write(self._empty_conversation())
            return []

        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read conversation file: {error}") from error

        if not isinstance(value, dict):
            raise ValueError("Conversation file must contain a JSON object")
        if value.get("deal_id") != self.deal_id:
            raise ValueError("Conversation file deal_id does not match this deal")

        history = value.get("conversation_history")
        if not isinstance(history, list) or not all(
            isinstance(entry, dict) for entry in history
        ):
            raise ValueError("conversation_history must be a JSON array of objects")
        return history

    def append_completed_turn(
        self,
        history: list[dict[str, Any]],
        client_message: str | None,
        message_html: str,
    ) -> None:
        updated_history = list(history)
        if client_message is not None:
            updated_history.append(
                {
                    "role": "client",
                    "content_type": "text",
                    "content": client_message,
                }
            )
        updated_history.append(
            {
                "role": "sasha",
                "content_type": "text/html",
                "content": message_html,
                "delivery_status": "generated",
            }
        )
        self._write(
            {
                "deal_id": self.deal_id,
                "conversation_history": updated_history,
            }
        )

    def _empty_conversation(self) -> dict[str, Any]:
        return {"deal_id": self.deal_id, "conversation_history": []}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)
