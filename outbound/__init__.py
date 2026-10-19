"""Hand Sasha's final drafts to FreshFlow and save email thread IDs."""

from .models import EmailRequest
from .service import OutboundService

__all__ = ["EmailRequest", "OutboundService"]
