"""Explicit opt-in; an enabled sender never falls back to simulation."""

from dataclasses import dataclass
from urllib.parse import urlparse

from sasha_database.config import env_value, required
from sasha_database.messages import valid_email


def is_enabled() -> bool:
    value = env_value("SASHA_OUTBOUND_ENABLED").lower()
    if value not in {"", "false", "true", "0", "1"}:
        raise ValueError("SASHA_OUTBOUND_ENABLED must be true or false")
    return value in {"true", "1"}


@dataclass(frozen=True)
class OutboundSettings:
    environment: str
    queue_url: str
    region: str
    sender: str

    @classmethod
    def from_environment(cls):
        queue = required("SQS_QUEUE_URL")
        parsed = urlparse(queue)
        if parsed.scheme != "https" or not parsed.hostname or len(parsed.path.strip("/").split("/")) != 2:
            raise ValueError("SQS_QUEUE_URL must be a full HTTPS queue URL")
        sender = required("SASHA_SENDER_EMAIL")
        if not valid_email(sender):
            raise ValueError("SASHA_SENDER_EMAIL must be a valid email address")
        return cls(required("FP_ENVIRONMENT"), queue, required("AWS_REGION"), sender)
