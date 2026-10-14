"""Inputs and validated decisions for the independent deal resolver."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


def normalize_phone(value: str, country: str | None = None) -> str:
    """Return E.164. A national number needs an explicit US/CA country, as in the collector."""
    if not re.fullmatch(r"[+\d () .-]+", value.strip()):
        raise ValueError("Phone must not include extensions or letters")
    digits = re.sub(r"\D", "", value)
    if value.strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if country in ("US", "CA") and len(digits) == 10:
        return "+1" + digits
    if country in ("US", "CA") and len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise ValueError("Supply an international phone number or an explicit US/CA country")


class LookupRequest(BaseModel):
    """One inbound EMAIL (sender_email) or SMS (sender_phone, E.164) to resolve."""

    model_config = ConfigDict(extra="forbid")
    sender_email: str | None = Field(default=None, min_length=3, max_length=254)
    sender_phone: str | None = Field(default=None, max_length=32)
    thread_id: str | None = Field(default=None, min_length=1, max_length=500)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1, max_length=20000)

    @property
    def channel(self) -> Literal["EMAIL", "SMS"]:
        return "SMS" if self.sender_phone else "EMAIL"

    @model_validator(mode="after")
    def require_a_sender(self) -> "LookupRequest":
        if not self.sender_email and not self.sender_phone:
            raise ValueError("Supply a sender email address or phone number")
        return self

    @field_validator("sender_phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        return None if value is None else normalize_phone(value)

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        if value.count("@") != 1 or any(character.isspace() for character in value):
            raise ValueError("Enter a complete sender email address")
        local, domain = value.split("@")
        if not local or not domain or "." not in domain:
            raise ValueError("Enter a complete sender email address")
        return value

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter the email body")
        return value


class ResultObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceField(ResultObject):
    name: str
    value: str


class Evidence(ResultObject):
    query_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    primary_key: str = Field(min_length=1)
    fields: list[EvidenceField]
    interpretation: str


class Candidate(ResultObject):
    deal_id: str = Field(pattern=r"^[1-9][0-9]*$")
    supports: list[str]
    conflicts: list[str]


class LookupResult(ResultObject):
    status: Literal["matched", "needs_review", "no_match", "error"]
    deal_id: str | None
    deal_title: str | None
    confidence: Literal["high", "medium", "low", "none"]
    reason: str = Field(min_length=1)
    candidate_deals: list[Candidate]
    evidence: list[Evidence]
    limitations: list[str]
    next_check: str | None

    @model_validator(mode="after")
    def validate_decision(self, info: ValidationInfo) -> "LookupResult":
        if self.status != "matched":
            if self.deal_id is not None or self.deal_title is not None:
                raise ValueError("Only a matched decision may select a deal")
            return self
        if not self.deal_id or not self.deal_id.isdigit() or int(self.deal_id) < 1:
            raise ValueError("A match requires a positive deal ID")
        if self.confidence != "high" or not self.evidence:
            raise ValueError("A match requires high confidence and evidence")
        candidates = [item for item in self.candidate_deals if item.deal_id == self.deal_id]
        if len(candidates) != 1 or candidates[0].conflicts:
            raise ValueError("The matched candidate must have no unresolved conflicts")
        main_schema = (info.context or {}).get("main_schema")
        if not main_schema:
            raise ValueError("Match validation requires the configured main schema")
        if not any(
            item.table == f"{main_schema}.deals" and item.primary_key == self.deal_id
            for item in self.evidence
        ):
            raise ValueError("A match requires evidence citing the verified deal row")
        return self
