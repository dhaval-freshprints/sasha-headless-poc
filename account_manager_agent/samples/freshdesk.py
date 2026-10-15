"""The Freshdesk "New Sample Requested" ticket, as Sasha v3 created it (sample_requests/ticket.py).

The requester is Sasha; client details go in the description and custom fields. Logistics ships
from the ticket and replies on it when the sample ships and is delivered.
"""

from __future__ import annotations

import base64
import html
import json
import urllib.error
import urllib.request

from sasha_database.config import env_value

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
