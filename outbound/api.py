"""FreshFlow callback; no agent execution is involved."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from sasha_database.storage import validate_deal_id

from .repository import CallbackError
from .service import OutboundService


class ThreadCallback(BaseModel):
    deal_id: str | int
    thread_id: str = Field(min_length=1, max_length=255)
    message_id: str | None = Field(default=None, max_length=255)
    trace_id: str | None = Field(default=None, max_length=255)
    assignment_type: str | None = None

    @field_validator("deal_id", mode="before")
    @classmethod
    def deal(cls, value):
        return str(validate_deal_id(str(value).strip()))

    @field_validator("thread_id")
    @classmethod
    def thread(cls, value):
        if not value.strip():
            raise ValueError("thread_id must not be blank")
        return value.strip()

    @field_validator("message_id", "trace_id", "assignment_type")
    @classmethod
    def optional_text(cls, value):
        return (value or "").strip() or None

    @field_validator("assignment_type")
    @classmethod
    def assignment(cls, value):
        normalized = (value or "").lower()
        if normalized not in {"", "handoff_offer"}:
            raise ValueError("assignment_type must be handoff_offer or empty")
        return normalized or None


def create_router(service_factory=OutboundService.for_callbacks):
    router = APIRouter(prefix="/communication", tags=["communication"])

    @router.post("/thread-id")
    def store_thread_id(request: ThreadCallback):
        service = service_factory()
        if service is None:
            raise HTTPException(503, "Outbound sending is disabled")
        try:
            return service.store_thread_id(request.model_dump())
        except CallbackError as error:
            raise HTTPException(error.status_code, str(error)) from error

    return router
