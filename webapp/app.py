"""FastAPI entry point for the Sasha web app."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from openai_managed.environment import ApplicationEnvironment
from openai_managed.task import normalize_client_message, validate_follow_up
from openai_managed.notes import NotesStore

from .deals import Deal, DealStore
from .jobs import JobManager, WebRun
from .deal_manager_jobs import DealManagerJobs
from deal_manager_agent.models import LookupRequest
from deal_manager_agent.settings import storage_directory


ROOT = Path(__file__).resolve().parent.parent
WEB_DIRECTORY = Path(__file__).resolve().parent
STATIC_DIRECTORY = WEB_DIRECTORY / "static"


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


class RunRequest(BaseModel):
    deal_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)
    client_message: str | None = Field(default=None, max_length=20_000)
    file_urls: list[str] = Field(default_factory=list, max_length=10)

    turn_type: Literal["follow_up"] | None = None
    follow_up_stage: int | None = Field(default=None, strict=True, ge=1, le=5)
    as_of_date: str | None = None
    days_since_client_reply: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def validate_turn(self):
        validate_follow_up(self.turn_type, self.follow_up_stage, self.as_of_date,
                           self.days_since_client_reply, self.client_message, self.file_urls)
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_workflow(cls, value):
        if isinstance(value, dict) and "workflow" in value:
            raise ValueError("Remove workflow; Account Manager chooses its work from the conversation")
        return value

    @field_validator("client_message")
    @classmethod
    def empty_message_is_none(cls, value: str | None) -> str | None:
        return normalize_client_message(value)

    @field_validator("file_urls")
    @classmethod
    def validate_file_urls(cls, values: list[str]) -> list[str]:
        cleaned = []
        for value in values:
            url = value.strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Each artwork URL must use HTTP or HTTPS")
            if len(url) > 2_048:
                raise ValueError("Artwork URLs must be 2,048 characters or fewer")
            cleaned.append(url)
        return cleaned


class DealRequest(BaseModel):
    deal_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)


class AdditionalMessageRequest(BaseModel):
    submission_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    text: str = Field(min_length=1, max_length=20_000)

    @field_validator("text")
    @classmethod
    def require_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Client message cannot be blank")
        return value


def create_app(
    job_manager: JobManager | None = None,
    deal_store: DealStore | None = None,
    lookup_manager: DealManagerJobs | None = None,
) -> FastAPI:
    load_dotenv(ROOT / ".env")
    manager = job_manager or JobManager(_jobs_directory())
    deals = deal_store or DealStore(_deals_directory())
    lookups = lookup_manager or DealManagerJobs(storage_directory() / "jobs")
    _backfill_deals(deals, manager.list_all())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        manager.shutdown()
        lookups.shutdown()

    app = FastAPI(title="Account Manager", lifespan=lifespan)
    app.state.job_manager = manager
    app.state.deal_store = deals
    app.state.lookup_manager = lookups
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIRECTORY), name="static")

    @app.get("/", response_class=FileResponse)
    def home() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/deal-manager", response_class=FileResponse)
    @app.get("/deal-manager/lookups/{lookup_id}", response_class=FileResponse)
    def lookup_page() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "deal_manager.html", headers={"Cache-Control": "no-store"})

    @app.post("/api/deal-manager/lookups", status_code=202)
    def create_lookup(request: LookupRequest) -> dict:
        return lookups.submit(request)

    @app.get("/api/deal-manager/lookups")
    def list_lookups() -> list[dict]:
        return lookups.list_all()

    @app.get("/api/deal-manager/lookups/{lookup_id}")
    def get_lookup(lookup_id: str) -> dict:
        try:
            return lookups.get(lookup_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Lookup not found") from error

    @app.get("/runs/{run_id}", response_class=FileResponse)
    def run_page(run_id: str) -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/deals/{deal_id}", response_class=FileResponse)
    def deal_page(deal_id: str) -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/deals")
    def list_deals() -> list[dict]:
        return [
            _deal_payload(deal, manager.list_for_deal(deal.deal_id))
            for deal in deals.list_all()
        ]

    @app.post("/api/deals", status_code=201)
    def add_deal(request: DealRequest) -> dict:
        deal = deals.add(request.deal_id)
        return _deal_payload(deal, manager.list_for_deal(deal.deal_id))

    @app.get("/api/deals/{deal_id}")
    def get_deal(deal_id: str) -> dict:
        try:
            deal = deals.get(deal_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Deal not found") from error
        return _deal_payload(deal, manager.list_for_deal(deal_id), include_runs=True)

    @app.get("/api/deals/{deal_id}/notes")
    def get_deal_notes(deal_id: str) -> dict:
        try:
            store = NotesStore(_notes_runs_directory(), deal_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            notes = store.load()
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=500, detail=f"Could not read deal notes: {error}") from error
        return {"deal_id": deal_id, "notes": notes}

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest) -> dict:
        deals.touch(request.deal_id)
        run = manager.submit(
            request.deal_id,
            request.client_message,
            request.file_urls,
            turn_type=request.turn_type,
            follow_up_stage=request.follow_up_stage,
            as_of_date=request.as_of_date,
            days_since_client_reply=request.days_since_client_reply,
        )
        return run.to_dict()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return manager.get(run_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error

    @app.post("/api/runs/{run_id}/messages", status_code=202)
    def send_message(run_id: str, request: AdditionalMessageRequest) -> dict:
        try:
            return manager.submit_message(run_id, request.submission_id, request.text)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/runs/{run_id}/artifacts/{artifact_path:path}")
    def get_artifact(run_id: str, artifact_path: str) -> FileResponse:
        try:
            path = manager.artifact_path(run_id, artifact_path)
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Artifact not found") from error
        return FileResponse(path)

    return app


def _jobs_directory() -> Path:
    environment = ApplicationEnvironment.from_environment().name
    configured = os.environ.get("SASHA_WEB_JOBS_DIRECTORY", "").strip()
    if configured:
        return Path(configured).expanduser() / environment
    return ROOT / "runs" / "webapp" / "jobs" / environment


def _deals_directory() -> Path:
    environment = ApplicationEnvironment.from_environment().name
    configured = os.environ.get("SASHA_WEB_DEALS_DIRECTORY", "").strip()
    if configured:
        return Path(configured).expanduser() / environment
    return ROOT / "runs" / "webapp" / "deals" / environment


def _notes_runs_directory() -> Path:
    environment = ApplicationEnvironment.from_environment().name
    configured = os.environ.get("OPENAI_MANAGED_RUNS_DIRECTORY", "").strip()
    root = Path(configured).expanduser() if configured else ROOT / "runs" / "openai-managed"
    return root / environment


def _backfill_deals(deals: DealStore, runs: list[WebRun]) -> None:
    for run in runs:
        try:
            deals.get(run.deal_id)
        except KeyError:
            deals.add(run.deal_id)


def _deal_payload(
    deal: Deal,
    runs: list[WebRun],
    include_runs: bool = False,
) -> dict:
    value = deal.to_dict()
    value["run_count"] = len(runs)
    value["latest_run"] = runs[0].to_dict() if runs else None
    if include_runs:
        value["runs"] = [run.to_dict() for run in runs]
    return value


app = create_app()
