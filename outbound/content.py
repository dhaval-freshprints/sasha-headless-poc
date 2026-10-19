"""Customer email subjects and final SMS preparation."""

import re
from html import unescape

from account_manager_agent.sms_links import shorten_sms_messages


def root_subject(subject: str) -> str:
    return re.sub(r"^(?:(?:re|fwd):\s*)+", "", (subject or "").strip(), flags=re.I).strip()


def reply_subject(subject: str) -> str:
    root = root_subject(subject)
    return f"Re: {root}" if root else ""


def customer_reply_subject(task) -> str:
    for message in reversed(task.incoming_messages):
        if message.convo_type == "internal_mail":
            continue
        if message.message_type == "EMAIL":
            return reply_subject(message.subject)
        return ""
    return ""


def customer_subject(task, result, thread: dict) -> str:
    if result.convo_type == "outreach":
        return "Fresh Prints here to help!"
    subject = reply_subject(thread.get("subject"))
    if subject:
        return subject
    if result.convo_type == "client_response":
        return customer_reply_subject(task) or "Re: Fresh Prints here to help!"
    return "Re: Fresh Prints here to help!"


def prepare_sms(messages: list[dict], report) -> list[dict]:
    plain = []
    for message in messages:
        text = unescape(re.sub(r"<[^>]*>", " ", message["text"]))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("An SMS bubble is empty after removing HTML")
        plain.append({**message, "text": text})
    return shorten_sms_messages(plain, report)


def phone_number(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if phone.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    return ""
