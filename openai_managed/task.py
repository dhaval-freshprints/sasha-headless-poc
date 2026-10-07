"""Input and output values for one managed Sasha turn."""

from dataclasses import asdict, dataclass
import json
from typing import Any, Literal


TurnType = Literal["outreach", "response", "follow_up"]

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
    turn_type: TurnType | None = None
    follow_up_stage: int | None = None
    as_of_date: str | None = None
    days_since_client_reply: int | None = None

    def resolved_turn_type(self) -> TurnType:
        if self.turn_type is not None:
            return self.turn_type
        if self.client_message is None:
            return "outreach"
        return "response"


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
