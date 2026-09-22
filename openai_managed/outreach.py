"""Input and output values for managed outreach."""

from dataclasses import asdict, dataclass
import json
from typing import Literal


OUTREACH_RESULT_JSON_SCHEMA = {
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
class OutreachTask:
    deal_id: str
    task_id: str
    deal_url: str


@dataclass(frozen=True)
class OutreachResult:
    deal_id: str
    status: Literal["completed", "failed"]
    message_html: str = ""
    failure_code: str = ""
    failure_message: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: str) -> "OutreachResult":
        return cls(**json.loads(value))
