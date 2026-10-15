"""Input and output values for one managed Sasha turn."""

from dataclasses import asdict, dataclass
import json
from datetime import date
from typing import Any, Literal
import uuid
from dataclasses import field
from sasha_database.messages import IncomingMessage, CONVERSATION_TYPES, join_messages


def normalize_client_message(message: str | None) -> str | None:
    return message if message and message.strip() else None


def validate_follow_up(
    turn_type: str | None, stage: int | None, as_of_date: str | None,
    days: int | None, client_message: str | None, file_urls: list[str] | tuple[str, ...],
) -> None:
    if turn_type is None:
        if any(value is not None for value in (stage, as_of_date, days)):
            raise ValueError("Follow-up metadata requires turn_type: follow_up")
        return
    if turn_type != "follow_up":
        raise ValueError("Only follow_up is accepted; Account Manager selects outreach or response")
    if type(stage) is not int or not 1 <= stage <= 5:
        raise ValueError("follow_up_stage must be an integer from 1 to 5")
    try:
        if not isinstance(as_of_date, str) or date.fromisoformat(as_of_date).isoformat() != as_of_date:
            raise ValueError()
    except ValueError as error:
        raise ValueError("as_of_date must be an ISO date (YYYY-MM-DD)") from error
    if days is not None and (type(days) is not int or days < 0):
        raise ValueError("days_since_client_reply must be a nonnegative integer")
    if normalize_client_message(client_message) is not None or file_urls:
        raise ValueError("Initial follow-up checks cannot include client messages or artwork URLs")


def _object(properties: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


TEXT = {"type": "string"}
# A confirmed free blank sample to place: one style, color and size.
SAMPLE_REQUEST_SCHEMA = _object({
    "trigger": {"type": "string", "enum": ["client_request", "quality_doubt", "followup_offer"]},
    "style_code": TEXT, "product_link": TEXT, "color": TEXT, "size": TEXT,
    "ship_to_name": TEXT, "ship_to_address": TEXT, "contact_email": TEXT, "contact_phone": TEXT,
    "organization": TEXT,
})
# What a logistics email said about one of this deal's samples.
SAMPLE_UPDATE_SCHEMA = _object({
    "sample_id": {"type": "integer"},
    "status": {"type": "string", "enum": ["shipped", "delivered", "cancelled"]},
    "cancel_reason": TEXT,
})

SASHA_RESULT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "deal_id",
        "status",
        "message_html",
        "notes",
        "convo_type",
        "handoff_reason",
        "isa_decision",
        "sample_request",
        "sample_update",
        "failure_code",
        "failure_message",
    ],
    "properties": {
        "deal_id": {"type": "string"},
        "status": {"type": "string", "enum": ["completed", "handoff", "failed"]},
        "message_html": {"type": "string"},
        "notes": {"type": "string"},
        "convo_type": {"type": ["string", "null"], "enum": ["client_response", "followup", "internal_mail", "outreach", None]},
        "handoff_reason": {"type": "string"},
        "isa_decision": {"type": "string", "enum": ["", "accepted", "rejected", "unclear"]},
        "sample_request": {"anyOf": [SAMPLE_REQUEST_SCHEMA, {"type": "null"}]},
        "sample_update": {"anyOf": [SAMPLE_UPDATE_SCHEMA, {"type": "null"}]},
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
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    incoming_messages: tuple[IncomingMessage, ...] = ()
    turn_type: Literal["follow_up"] | None = None
    follow_up_stage: int | None = None
    as_of_date: str | None = None
    days_since_client_reply: int | None = None
    handoff_context: dict | None = None  # Set by the runner when the deal is already handed off.
    sample_context: dict | None = None  # This deal's samples and the sample limits, set by the runner.

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_message", normalize_client_message(self.client_message))
        validate_follow_up(self.turn_type, self.follow_up_stage, self.as_of_date,
                           self.days_since_client_reply, self.client_message or join_messages(self.incoming_messages), self.file_urls)


@dataclass(frozen=True)
class SashaResult:
    deal_id: str
    status: Literal["completed", "handoff", "failed"]
    message_html: str = ""
    failure_code: str = ""
    failure_message: str = ""
    notes: str = ""
    convo_type: str | None = None
    handoff_reason: str = ""
    isa_decision: str = ""
    sample_request: dict | None = None
    sample_update: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, value: str) -> "SashaResult":
        result = cls(**json.loads(value))
        if result.status not in {"completed", "handoff", "failed"}:
            raise ValueError("Invalid result status")
        if result.isa_decision not in {"", "accepted", "rejected", "unclear"}:
            raise ValueError("Invalid isa_decision")
        for name in ("deal_id", "message_html", "notes", "handoff_reason", "failure_code", "failure_message"):
            if not isinstance(getattr(result, name), str):
                raise ValueError(f"Result {name} must be text")
        if result.status == "completed" and result.message_html.strip() and result.convo_type not in CONVERSATION_TYPES:
            raise ValueError("A generated draft requires a valid convo_type")
        if result.status == "completed" and not result.message_html.strip() and result.convo_type is not None:
            raise ValueError("No-reply results must have null convo_type")
        return result
