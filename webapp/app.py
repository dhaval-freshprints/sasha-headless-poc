"""FastAPI entry point for the Sasha web app."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .deals import Deal, DealStore
from .jobs import JobManager, WebRun


ROOT = Path(__file__).resolve().parent.parent
WEB_DIRECTORY = Path(__file__).resolve().parent
STATIC_DIRECTORY = WEB_DIRECTORY / "static"


class RunRequest(BaseModel):
    deal_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)
    client_message: str | None = Field(default=None, max_length=20_000)
    file_urls: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("client_message")
    @classmethod
    def empty_message_is_none(cls, value: str | None) -> str | None:
        if value is None or value.strip():
            return value
        return None

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


def create_app(
    job_manager: JobManager | None = None,
    deal_store: DealStore | None = None,
) -> FastAPI:
    load_dotenv(ROOT / ".env")
    manager = job_manager or JobManager(_jobs_directory())
    deals = deal_store or DealStore(_deals_directory())
    _backfill_deals(deals, manager.list_all())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(title="Sasha QA Test App", lifespan=lifespan)
    app.state.job_manager = manager
    app.state.deal_store = deals
    app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

    @app.get("/", response_class=FileResponse)
    def home() -> Path:
        return STATIC_DIRECTORY / "index.html"

    @app.get("/runs/{run_id}", response_class=FileResponse)
    def run_page(run_id: str) -> Path:
        return STATIC_DIRECTORY / "index.html"

    @app.get("/deals/{deal_id}", response_class=FileResponse)
    def deal_page(deal_id: str) -> Path:
        return STATIC_DIRECTORY / "index.html"

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

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest) -> dict:
        deals.touch(request.deal_id)
        run = manager.submit(
            request.deal_id,
            request.client_message,
            request.file_urls,
        )
        return run.to_dict()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return manager.get(run_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error

    @app.get("/api/runs/{run_id}/artifacts/{artifact_path:path}")
    def get_artifact(run_id: str, artifact_path: str) -> FileResponse:
        try:
            path = manager.artifact_path(run_id, artifact_path)
        except (KeyError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail="Artifact not found") from error
        return FileResponse(path)

    return app


def _jobs_directory() -> Path:
    configured = os.environ.get("SASHA_WEB_JOBS_DIRECTORY", "").strip()
    if configured:
        return Path(configured).expanduser()
    return ROOT / "runs" / "webapp" / "jobs"


def _deals_directory() -> Path:
    configured = os.environ.get("SASHA_WEB_DEALS_DIRECTORY", "").strip()
    if configured:
        return Path(configured).expanduser()
    return ROOT / "runs" / "webapp" / "deals"


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
