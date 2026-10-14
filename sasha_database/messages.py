"""Structured incoming messages, including legacy manual envelopes."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import re


CONVERSATION_TYPES = {"client_response", "followup", "internal_mail", "outreach"}
MAX_MESSAGE_BYTES = 16777215


def validate_message_body(body: str) -> None:
    if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("Message body exceeds the database MEDIUMTEXT byte limit")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value))


@dataclass(frozen=True)
class IncomingMessage:
    body: str
    sender_name: str = ""
    message_type: str = "EMAIL"
    subject: str | None = None
    external_message_id: str | None = None
    external_thread_id: str | None = None
    attachments: tuple[str, ...] = ()
    message_timestamp: str = field(default_factory=lambda: utc_now().isoformat())
    content_type: str = "text"
    convo_type: str = "client_response"
    row_id: int | None = None

    def __post_init__(self):
        if not self.body.strip():
            raise ValueError("Incoming message body cannot be empty")
        validate_message_body(self.body)
        if self.message_type not in {"EMAIL", "SMS"}:
            raise ValueError("Incoming messages must be EMAIL or SMS")
        if self.content_type not in {"text", "text/html"}:
            raise ValueError("Unsupported message content type")
        if self.convo_type not in {"client_response", "internal_mail"}:
            raise ValueError("Unsupported incoming conversation type")
        for name, limit in (("sender_name", 255), ("subject", 500),
                            ("external_message_id", 255), ("external_thread_id", 255)):
            if len(getattr(self, name) or "") > limit:
                raise ValueError(f"{name} must be {limit} characters or fewer")
        if self.message_type == "SMS" and not self.sender_name.strip():
            raise ValueError("SMS requires a sender phone number")
        if self.message_type == "EMAIL" and self.sender_name and not valid_email(self.sender_name):
            raise ValueError("Email sender must be an email address")
        self.timestamp()

    def timestamp(self) -> datetime:
        value = datetime.fromisoformat(self.message_timestamp.replace("Z", "+00:00"))
        if value.tzinfo:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def to_dict(self) -> dict:
        return asdict(self)

    def prompt(self) -> str:
        fields = [("Channel", self.message_type), ("From", self.sender_name), ("Subject", self.subject)]
        header = "\n".join(f"{name}: {value}" for name, value in fields if value)
        return f"{header}\n\n{self.body}"


def incoming_message(text: str, attachments=(), default: IncomingMessage | None = None) -> IncomingMessage:
    """Parse the existing form/CLI envelope once; ordinary text stays intact."""
    header, separator, body = text.partition("\n\n")
    fields = {}
    if separator:
        lines = header.splitlines()
        allowed = {"channel", "from", "to", "cc", "subject"}
        for line in lines:
            name, colon, value = line.partition(":")
            if not colon or name.lower() not in allowed:
                fields = {}
                break
            fields[name.lower()] = value.strip()
    if not fields:
        body = text
    channel = fields.get("channel", default.message_type if default else "EMAIL").upper()
    sender = fields.get("from", default.sender_name if default else "")
    from .config import env_value
    domains = (env_value("INTERNAL_SENDER_DOMAINS") or "freshprints.com").split(",")
    internal = channel == "EMAIL" and sender.rsplit("@", 1)[-1].lower() in {
        domain.strip().lower().lstrip("@") for domain in domains}
    return IncomingMessage(
        body=body, sender_name=sender, message_type=channel,
        subject=fields.get("subject", default.subject if default else None),
        external_thread_id=default.external_thread_id if default else None,
        attachments=tuple(attachments), convo_type="internal_mail" if internal else "client_response",
    )


def join_messages(messages) -> str | None:
    return "\n\n--- Next incoming message ---\n\n".join(message.prompt() for message in messages) or None
