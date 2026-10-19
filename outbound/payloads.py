"""FreshFlow wire contracts, matching production Sasha's senders."""

from datetime import datetime, timezone


ASSIGNMENTS = {
    "outreach": "INITIAL_OUTREACH",
    "client_response": "CLIENT_RESPONSE",
    "followup": "SIMPLE_FOLLOW_UP",
    "internal_mail": "INTERNAL_EMAIL",
}
UNICODE_REPLACEMENTS = str.maketrans({
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
})


def email_payload(*, trace_id, deal_id, account_id, assignment, sender,
                  recipient, subject, body, thread_id=None, cc=()):
    return {
        "type": "sasha-email-trigger", "traceId": trace_id,
        "payload": {
            "status": "completed", "deal_id": str(deal_id),
            "account_id": str(account_id) if account_id is not None else None,
            "assignment_type": assignment, "sasha_output_id": None, "is_sasha_v3": True,
            "workflow_result": {"success": True, "result": {"draft": {
                "email_subject": subject.translate(UNICODE_REPLACEMENTS),
                "email_body": body.translate(UNICODE_REPLACEMENTS),
                "recipient_email": recipient, "sender_email": sender,
                "thread_id": thread_id, "cc_email": list(cc),
            }}},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def sms_payload(*, trace_id, contact, message, assignment):
    return {
        "type": "send-text-message", "traceId": trace_id,
        "payload": {
            "phoneNumber": contact["phone"], "message": message,
            "email": contact.get("email") or "", "firstName": contact.get("first_name") or "",
            "lastName": contact.get("last_name") or "", "messageType": assignment,
            "sashaVersion": "v3",
        },
    }


def sms_attributes(deal_id, account_id):
    attributes = {"source": "sasha_ai", "type": "send-text-message", "deal_id": str(deal_id)}
    if account_id is not None:
        attributes["account_id"] = str(account_id)
    return attributes
