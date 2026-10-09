"""Input and output values for one managed Sasha turn."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Literal


Workflow = Literal["outreach", "client-response-orchestrator"]
WORKFLOWS = ("outreach", "client-response-orchestrator")
TurnType = Literal["outreach", "response", "follow_up"]


def validate_workflow(workflow: str, client_message: str | None) -> None:
    if workflow not in WORKFLOWS:
        raise ValueError("workflow must be outreach or client-response-orchestrator")
    if workflow == "client-response-orchestrator" and not (
        client_message and client_message.strip()
    ):
        raise ValueError("client-response-orchestrator requires a client message")


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
    workflow: Workflow | None = field(default=None, kw_only=True)
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

    def __post_init__(self) -> None:
        if self.resolved_turn_type() == "follow_up":
            if self.workflow is not None:
                validate_workflow(self.workflow, self.client_message)
            return
        if self.workflow is None:
            raise TypeError("workflow is a required keyword argument")
        validate_workflow(self.workflow, self.client_message)


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
