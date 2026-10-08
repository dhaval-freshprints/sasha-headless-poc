"""Input and output values for one managed Sasha turn."""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Literal


Workflow = Literal["outreach", "client-response-orchestrator"]
WORKFLOWS = ("outreach", "client-response-orchestrator")


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
    workflow: Workflow = field(kw_only=True)
    client_message: str | None = None
    conversation_history: tuple[dict[str, Any], ...] = ()
    file_urls: tuple[str, ...] = ()
    deal_notes: str = ""

    def __post_init__(self) -> None:
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
