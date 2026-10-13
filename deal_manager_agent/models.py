"""Inputs and validated decisions for the independent deal resolver."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender_email: str = Field(min_length=3, max_length=254)
    subject: str = Field(default="", max_length=1000)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("sender_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
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
