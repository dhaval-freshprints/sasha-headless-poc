"""The Freshdesk "New Sample Requested" ticket, as Sasha v3 created it (sample_requests/ticket.py).

The requester is Sasha; client details go in the description and custom fields. Logistics ships
from the ticket and replies on it when the sample ships and is delivered.
"""

from __future__ import annotations

import base64
import html
import json
import re
import urllib.error
import urllib.request

from sasha_database.config import env_value

TICKET_ID_PATTERN = re.compile(r"/tickets/(\d+)")

SUBJECT = "New Sample Requested for Fresh Prints"
TAGS = ["sasha-sample", "sasha"]
TICKET_TYPE = "Sample Orders"
SOURCE = 10  # Outbound Email — Bot (12) is not accepted by this tenant.
PRIORITY = 1
DEFAULT_GROUP_ID = 1069000474780
PURPOSE_BY_TRIGGER = {
    "client_request": "Client requested a sample",
    "quality_doubt": "Client Approval for an Order",
    "followup_offer": "Sample offered to re-engage client",
}
OPS_NOTE = ("BLANK sample only (no print or decoration). Please reply on this ticket thread when the "
            "sample ships (include tracking) and again when it is delivered.")


def _esc(value) -> str:
    return html.escape(str(value or "").strip() or "—", quote=True)


def ticket_id_from_url(url: str | None) -> int | None:
    """Parse a Freshdesk agent URL saved on sasha_sample_requests."""
    match = TICKET_ID_PATTERN.search(str(url or ""))
    return int(match.group(1)) if match else None


def _conversation_text(conversation: dict) -> str:
    for key in ("body_text", "body"):
        value = conversation.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def summarize_conversations(conversations: list[dict], limit: int = 8) -> list[dict]:
    """Return newest conversation evidence with source metadata and bounded plain text."""
    summaries = []
    ordered = sorted(
        conversations,
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )
    for conversation in ordered[:limit]:
        text = _conversation_text(conversation)
        if not text:
            continue
        one_line = " ".join(text.split())
        summaries.append({
            "id": conversation.get("id"),
            "created_at": conversation.get("created_at"),
            "incoming": conversation.get("incoming"),
            "private": conversation.get("private"),
            "from_email": conversation.get("from_email"),
            "text": one_line[:1000] + ("…" if len(one_line) > 1000 else ""),
        })
    return summaries


def infer_sample_status_from_thread(conversations: list[dict], current_status: str) -> str | None:
    """Infer a forward sample status from Freshdesk thread text. Never moves backward."""
    if current_status in ("delivered", "cancelled"):
        return None
    messages = [
        _conversation_text(item).lower()
        for item in conversations
        if item.get("incoming", True) is not False
    ]
    combined = "\n".join(message for message in messages if message)
    if not combined.strip():
        return None
    cancellation = re.search(r"\b(cancelled|canceled)\b", combined)
    cancellation_negated = re.search(r"\b(not|isn't|wasn't|never)\s+(cancelled|canceled)\b", combined)
    if cancellation and not cancellation_negated:
        return "cancelled"
    if re.search(r"\b(delivered|delivery confirmed)\b", combined) and current_status in ("placed", "shipped"):
        return "delivered"
    if current_status == "placed" and re.search(
        r"\b(shipped|in transit)\b|tracking(?:\s+(?:number|details|link))?\s*[:#]",
        combined,
    ):
        return "shipped"
    return None


def ticket_payload(sample: dict, deal_id, organization: str, requester_email: str,
                   group_id: int = DEFAULT_GROUP_ID) -> dict:
    purpose = PURPOSE_BY_TRIGGER.get(sample.get("trigger"), PURPOSE_BY_TRIGGER["client_request"])
    link = (sample.get("product_link") or "").strip()
    link_html = f'<a href="{_esc(link)}">{_esc(link)}</a>' if link else "—"
    description = (
        f"<p><strong>Purpose:</strong> {_esc(purpose)}</p>"
        f"<p>Deal ID: {_esc(deal_id)}</p>"
        "<p><strong>Shipping Information</strong></p>"
        f"<p>Organization: {_esc(organization)}<br>Contact Name: {_esc(sample.get('ship_to_name'))}<br>"
        f"Address: {_esc(sample.get('ship_to_address'))}</p>"
        "<p><strong>Item #1 Details</strong></p>"
        f"<p>Style Code: {_esc(sample.get('style_code'))}<br>Apparel Color: {_esc(sample.get('color'))}<br>"
        f"Size: {_esc(sample.get('size'))}<br>Link: {link_html}</p>"
        f"<p><em>{_esc(OPS_NOTE)}</em></p>"
    )
    custom = {
        "cf_ticket_source": "Sasha V3",
        "cf_style_code": sample.get("style_code"), "cf_product_code": sample.get("style_code"),
        "cf_product_codes": sample.get("style_code"), "cf_garment_color": sample.get("color"),
        "cf_todelivery_address": sample.get("ship_to_address"), "cf_client_full_name": sample.get("ship_to_name"),
        "cf_client_email": sample.get("contact_email"), "cf_lead_phone_number": sample.get("contact_phone"),
        "cf_company": organization, "cf_organization_text": organization, "cf_deal_id": str(deal_id),
        "cf_order_page_v3_link": link,
    }
    return {
        "email": requester_email, "name": "Sasha", "subject": SUBJECT, "description": description,
        "priority": PRIORITY, "status": 2, "source": SOURCE, "type": TICKET_TYPE, "group_id": group_id,
        "tags": TAGS, "custom_fields": {key: value for key, value in custom.items() if str(value or "").strip()},
    }


class FreshdeskClient:
    def __init__(self, domain: str, api_key: str, requester_email: str, group_id: int = DEFAULT_GROUP_ID,
                 opener=urllib.request.urlopen):
        self.domain = domain.strip().rstrip("/").removeprefix("https://")
        self.api_key = api_key
        self.requester_email = requester_email
        self.group_id = group_id
        self.opener = opener

    @classmethod
    def from_environment(cls) -> "FreshdeskClient | None":
        """None when FRESHDESK_DOMAIN or FRESHDESK_API_KEY is not set: samples are stored, not ticketed."""
        domain, api_key = env_value("FRESHDESK_DOMAIN"), env_value("FRESHDESK_API_KEY")
        if not (domain and api_key):
            return None
        group = env_value("FRESHDESK_SAMPLE_GROUP_ID")
        return cls(domain, api_key, env_value("FRESHDESK_REQUESTER_EMAIL", "SASHA_SENDER_EMAIL"),
                   int(group) if group.isdigit() else DEFAULT_GROUP_ID)

    def ticket_url(self, ticket_id) -> str:
        return f"https://{self.domain}/a/tickets/{ticket_id}"

    def create_sample_ticket(self, sample: dict, deal_id, organization: str) -> str:
        """Create the ticket and return its link."""
        payload = ticket_payload(sample, deal_id, organization, self.requester_email, self.group_id)
        token = base64.b64encode(f"{self.api_key}:X".encode()).decode()
        request = urllib.request.Request(
            f"https://{self.domain}/api/v2/tickets", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"})
        try:
            with self.opener(request, timeout=30) as response:
                ticket_id = int(json.loads(response.read() or b"{}")["id"])
        except urllib.error.HTTPError as error:
            with error:
                raise RuntimeError(f"Freshdesk refused the ticket (HTTP {error.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as error:
            raise RuntimeError(f"Freshdesk request failed ({type(error).__name__})") from None
        return self.ticket_url(ticket_id)

    def _authorized_request(self, path: str, method: str = "GET", payload: dict | None = None):
        token = base64.b64encode(f"{self.api_key}:X".encode()).decode()
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"https://{self.domain}/api/v2{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            with error:
                details = error.read().decode("utf-8", errors="replace")[:500]
                suffix = f": {details}" if details else ""
                raise RuntimeError(f"Freshdesk request failed (HTTP {error.code}){suffix}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise RuntimeError(f"Freshdesk request failed ({type(error).__name__})") from None

    def get_ticket(self, ticket_id: int) -> dict:
        return self._authorized_request(f"/tickets/{ticket_id}")

    def get_conversations(self, ticket_id: int) -> list[dict]:
        conversations = []
        for page in range(1, 6):
            payload = self._authorized_request(
                f"/tickets/{ticket_id}/conversations?per_page=100&page={page}"
            )
            batch = list(payload if isinstance(payload, list) else payload.get("conversations", []))
            conversations.extend(batch)
            if len(batch) < 100:
                break
        return conversations

    def reply_to_ticket(self, ticket_id: int, body_html: str) -> dict:
        """Public reply on the ticket thread (visible to Sample Orders / logistics)."""
        return self._authorized_request(
            f"/tickets/{ticket_id}/reply",
            method="POST",
            payload={"body": body_html},
        )

    def add_private_note(self, ticket_id: int, body_html: str) -> dict:
        return self._authorized_request(
            f"/tickets/{ticket_id}/notes",
            method="POST",
            payload={"body": body_html, "private": True},
        )

    def update_ticket(self, ticket_id: int, fields: dict) -> dict:
        return self._authorized_request(f"/tickets/{ticket_id}", method="PUT", payload=fields)
