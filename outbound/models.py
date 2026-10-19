"""Requests and callback identities, independent of the agent runtime."""

from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, uuid5


@dataclass(frozen=True)
class EmailRequest:
    deal_id: str
    source_id: str
    purpose: str
    recipient: str
    subject: str
    body: str
    thread_id: str | None = None
    cc: tuple[str, ...] = ()
    offer_id: int | None = None
    convo_type: str = "client_response"


@dataclass
class OutboundMessage:
    trace_id: str
    deal_id: str
    source_id: str
    purpose: str
    channel: str
    position: int
    recipient: str
    payload: dict
    attributes: dict = field(default_factory=dict)
    offer_id: int | None = None


def message_trace_id(environment: str, deal_id: str, source_id: str, purpose: str,
                     channel: str, position: int) -> str:
    key = "/".join((environment, str(deal_id), source_id, purpose, channel, str(position)))
    return "sasha-poc-" + uuid5(NAMESPACE_URL, key).hex


def email_trace_id(record_type: str, record_id: int) -> str:
    return f"sasha-poc-{record_type}-{record_id}"
