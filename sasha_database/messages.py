"""Structured incoming messages, including legacy manual envelopes."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import re


CONVERSATION_TYPES = {"client_response", "followup", "internal_mail", "outreach"}
SMS_CATEGORIES = ("core_response", "additional_resources", "status_warning", "cta")
MAX_MESSAGE_BYTES = 16777215


def validate_message_body(body: str) -> None:
    if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("Message body exceeds the database MEDIUMTEXT byte limit")


def validate_sms_messages(messages: list) -> None:
    if not isinstance(messages, list):
        raise ValueError("sms_messages must be an array")
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"category", "text"}:
            raise ValueError("Each SMS bubble requires category and text")
        if message["category"] not in SMS_CATEGORIES:
            raise ValueError("Invalid SMS bubble category")
        if not isinstance(message["text"], str) or not message["text"].strip():
            raise ValueError("SMS bubble text must be nonblank text")
    validate_message_body(sms_body(messages))


def sms_body(messages: list[dict]) -> str:
    return "\n\n".join(message["text"] for message in messages)


def read_sms_messages(row: dict) -> list[dict] | None:
    if row.get("message_type") != "SMS" or row.get("direction") != "OUTBOUND":
        return None
    try:
        messages = json.loads(row["body"])
        validate_sms_messages(messages)
    except (ValueError, TypeError):
        return None
    return messages


def conversation_body(row: dict) -> str:
    messages = read_sms_messages(row)
    return sms_body(messages) if messages is not None else row["body"]


def draft_channel(message_html: str, sms_messages: list) -> str:
    if message_html.strip() and sms_messages:
        return "both"
    if message_html.strip():
        return "email"
    if sms_messages:
        return "sms"
    return "none"


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
