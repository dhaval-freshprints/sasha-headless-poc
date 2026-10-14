"""Input and output values for one managed Sasha turn."""

from dataclasses import asdict, dataclass
import json
from datetime import date
from typing import Any, Literal


def normalize_client_message(message: str | None) -> str | None:
    return message if message and message.strip() else None


def validate_follow_up(
    turn_type: str | None, as_of_date: str | None,
    days: int | None, client_message: str | None, file_urls: list[str] | tuple[str, ...],
) -> None:
    if turn_type is None:
        if any(value is not None for value in (as_of_date, days)):
            raise ValueError("Follow-up metadata requires turn_type: follow_up")
        return
    if turn_type != "follow_up":
        raise ValueError("Only follow_up is accepted; Account Manager selects outreach or response")
    try:
        if not isinstance(as_of_date, str) or date.fromisoformat(as_of_date).isoformat() != as_of_date:
            raise ValueError()
    except ValueError as error:
        raise ValueError("as_of_date must be an ISO date (YYYY-MM-DD)") from error
    if days is not None and (type(days) is not int or days < 0):
        raise ValueError("days_since_client_reply must be a nonnegative integer")
    if normalize_client_message(client_message) is not None or file_urls:
        raise ValueError("Initial follow-up checks cannot include client messages or artwork URLs")


SASHA_RESULT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "deal_id",
        "status",
        "message_html",
        "failure_code",
        "failure_message",
    ],
    "properties": {
        "deal_id": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "failed"]},
        "message_html": {"type": "string"},
        "failure_code": {"type": "string"},
        "failure_message": {"type": "string"},
    },
}


@dataclass(frozen=True)
class SashaTask:
    deal_id: str
    task_id: str
    deal_url: str
    client_message: str | None = None
    conversation_history: tuple[dict[str, Any], ...] = ()
    file_urls: tuple[str, ...] = ()
    deal_notes: str = ""
    turn_type: Literal["follow_up"] | None = None
    as_of_date: str | None = None
    days_since_client_reply: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_message", normalize_client_message(self.client_message))
        validate_follow_up(self.turn_type, self.as_of_date,
                           self.days_since_client_reply, self.client_message, self.file_urls)


@dataclass(frozen=True)
class SashaResult:
    deal_id: str
    status: Literal["completed", "failed"]
    message_html: str = ""
    failure_code: str = ""
    failure_message: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: str) -> "SashaResult":
        return cls(**json.loads(value))
