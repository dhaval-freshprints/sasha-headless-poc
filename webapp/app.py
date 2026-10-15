"""FastAPI entry point for the Sasha web app."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from account_manager_agent.environment import ApplicationEnvironment
from account_manager_agent.task import normalize_client_message, validate_follow_up
from sasha_database.storage import SashaStorage, validate_deal_id
from sasha_database.connection import DatabaseError
from sasha_database.messages import IncomingMessage, incoming_message

from .deals import Deal, DealStore
from .jobs import JobManager, WebRun
from .deal_manager_jobs import DealManagerJobs
from .inbound import InboundJobs, InboundRequest
from deal_manager_agent.deal_lookup import ActiveDealFinder
from deal_manager_agent.deal_resolution import DealResolver
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


class MessageInput(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)
    sender_name: str = Field(default="", max_length=255)
    message_type: Literal["EMAIL", "SMS"] = "EMAIL"
    subject: str | None = Field(default=None, max_length=500)
    external_message_id: str | None = Field(default=None, max_length=255)
    external_thread_id: str | None = Field(default=None, max_length=255)
    content_type: Literal["text", "text/html"] = "text"
    message_timestamp: str | None = None
    attachments: list[str] = Field(default_factory=list, max_length=10)

    def to_message(self, attachments=()):
        envelope = f"Channel: {self.message_type}\nFrom: {self.sender_name}\n\n{self.body}"
        kind = incoming_message(envelope).convo_type
        values = self.model_dump(exclude_none=True)
        urls = list(dict.fromkeys([*values.pop("attachments"), *attachments]))
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(url) > 2048:
                raise ValueError("Attachment URLs must be HTTP(S) URLs of at most 2048 characters")
        return IncomingMessage(**values, attachments=tuple(urls), convo_type=kind)

    @model_validator(mode="after")
    def validate_message(self):
        self.to_message()
        return self


class RunRequest(BaseModel):
    deal_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)
    client_message: str | None = Field(default=None, max_length=20_000)
    file_urls: list[str] = Field(default_factory=list, max_length=10)
    incoming_message: MessageInput | None = None

    turn_type: Literal["follow_up"] | None = None
    as_of_date: str | None = None
    days_since_client_reply: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def validate_turn(self):
        validate_follow_up(self.turn_type, self.as_of_date,
                           self.days_since_client_reply,
                           self.incoming_message.body if self.incoming_message else self.client_message, self.file_urls)
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_workflow(cls, value):
        if isinstance(value, dict) and "workflow" in value:
            raise ValueError("Remove workflow; Account Manager chooses its work from the conversation")
        if isinstance(value, dict) and "follow_up_stage" in value:
            raise ValueError("Remove follow_up_stage; Account Manager works out the follow-up phase itself")
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


class InboundMessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class DealRequest(BaseModel):
    deal_id: str = Field(pattern=r"^[0-9]+$", min_length=1, max_length=20)


class AdditionalMessageRequest(BaseModel):
    submission_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    text: str = Field(min_length=1, max_length=20_000)
    incoming_message: MessageInput | None = None

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
    inbound_manager: InboundJobs | None = None,
    deal_resolver: DealResolver | None = None,
    handoff_service=None,
    followup_settings=None,
) -> FastAPI:
    load_dotenv(ROOT / ".env")
    manager = job_manager or JobManager(_jobs_directory())
    deals = deal_store or DealStore(_deals_directory())
    lookups = lookup_manager or DealManagerJobs(storage_directory() / "jobs")
    _backfill_deals(deals, manager.list_all())

    resolver = deal_resolver or DealResolver()
    handoffs = handoff_service or LazyHandoffService()

    def start_account_manager(deal_id: str, client_message: str, deal_check: dict | None = None, incoming_messages=()) -> dict:
        deals.touch(deal_id)
        return manager.submit(deal_id, client_message, [], deal_check=deal_check, incoming_messages=incoming_messages).to_dict()

    inbound = inbound_manager or InboundJobs(
        storage_directory() / "inbound", ActiveDealFinder(), lookups, start_account_manager, resolver,
        send_message=manager.submit_message,
        run_state=lambda run_id: manager.get(run_id).to_dict(),
    )
    stop_worker = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if handoff_service is None:
            _start_timeout_worker(handoffs, stop_worker)
        if followup_settings is None:
            _start_followup_worker(lambda: run_due_followups(manager, deals, None), stop_worker)
        yield
        stop_worker.set()
        manager.shutdown()
        lookups.shutdown()
        inbound.shutdown()

    app = FastAPI(title="Account Manager", lifespan=lifespan)
    @app.exception_handler(DatabaseError)
    async def database_error(_, error):
        return JSONResponse(status_code=503, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def input_error(_, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    app.state.job_manager = manager
    app.state.deal_store = deals
    app.state.lookup_manager = lookups
    app.state.inbound_manager = inbound
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

    @app.get("/client-response", response_class=FileResponse)
    @app.get("/client-response/{inbound_id}", response_class=FileResponse)
    def client_response_page() -> Path:
        return STATIC_DIRECTORY / "client_response.html"

    @app.post("/api/inbound", status_code=202)
    def receive_inbound(request: InboundRequest) -> dict:
        """Webhook: a client email or SMS without a deal ID."""
        return inbound.submit(request)

    @app.get("/api/inbound")
    def list_inbound() -> list[dict]:
        return inbound.list_all()

    @app.get("/api/inbound/{inbound_id}")
    def get_inbound(inbound_id: str) -> dict:
        try:
            return inbound.get(inbound_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Inbound message not found") from error

    @app.post("/api/inbound/{inbound_id}/messages", status_code=202)
    def add_inbound_message(inbound_id: str, request: InboundMessageRequest) -> dict:
        """Another client message while the flow runs: held until Account Manager starts,
        then sent to the running Account Manager as an additional message."""
        try:
            return inbound.add_message(inbound_id, request.text)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Inbound message not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

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
            validate_deal_id(deal_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        try:
            notes = manager.storage_factory().load_notes(deal_id)
        except (DatabaseError, ValueError, OSError) as error:
            raise HTTPException(status_code=503, detail=f"Could not read deal notes: {error}") from error
        return {"deal_id": deal_id, "notes": notes}

    @app.post("/api/runs", status_code=202)
    def create_run(request: RunRequest) -> dict:
        # Every run is checked first, as in Sasha v3: with a client message it is a client
        # response, without one an outreach. Account Manager may run on a merged deal.
        message = request.incoming_message.to_message(request.file_urls) if request.incoming_message else None
        if message is None and request.client_message:
            message = incoming_message(request.client_message, request.file_urls)
        client_message = message.prompt() if request.incoming_message else request.client_message
        workflow = "client_response" if client_message else "outreach"
        if message and message.convo_type == "internal_mail":
            workflow = "internal_email"
        check = resolver.resolve(request.deal_id, workflow)
        if check.status != "proceed":
            verb = "skipped" if check.status == "skipped" else "not started"
            raise HTTPException(status_code=409, detail=f"Account Manager {verb}: {check.summary}")
        deals.touch(check.deal_id)
        run = manager.submit(
            check.deal_id,
            client_message,
            request.file_urls,
            incoming_messages=(message,) if message else (),
            deal_check=check.to_dict(),
            turn_type=request.turn_type,
            as_of_date=request.as_of_date,
            days_since_client_reply=request.days_since_client_reply,
        )
        return run.to_dict()

    @app.get("/api/handoffs/{deal_id}")
    def get_handoff(deal_id: str) -> dict:
        try:
            validate_deal_id(deal_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        status, reason = manager.storage_factory().deal_status(deal_id)
        offers, offers_error = [], ""
        try:
            for offer in handoffs.offers.offers_for_deal(deal_id):
                isa = handoffs.offers.isa(offer.isa_id)
                offers.append({**vars(offer), "isa": vars(isa) if isa else {},
                               "email": handoffs.offer_email(offer, isa, reason)})
        except Exception as error:  # Handoff tables missing or not reachable.
            offers, offers_error = [], f"Offers unavailable ({type(error).__name__})"
        return {"deal_id": deal_id, "status": status, "status_reason": reason, "offers": offers,
                "offers_error": offers_error}

    @app.post("/api/handoffs/check-timeouts")
    def check_handoff_timeouts(force: bool = False) -> dict:
        return {"results": handoffs.check_timeouts(force=force)}

    @app.post("/api/followups/run-due")
    def run_followups_now(force: bool = False) -> dict:
        return run_due_followups(manager, deals, followup_settings, force=force)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return manager.get(run_id).to_dict()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Run not found") from error

    @app.post("/api/runs/{run_id}/messages", status_code=202)
    def send_message(run_id: str, request: AdditionalMessageRequest) -> dict:
        try:
            incoming = request.incoming_message.to_message() if request.incoming_message else None
            return manager.submit_message(run_id, request.submission_id, request.text, incoming)
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


def _backfill_deals(deals: DealStore, runs: list[WebRun]) -> None:
    for run in runs:
        try:
            deals.get(run.deal_id)
        except KeyError:
            deals.add(run.deal_id)


class LazyHandoffService:
    """Creates the HandoffService (which needs database settings) on first use."""

    def __init__(self):
        self._service = None
        self._lock = threading.Lock()

    def __getattr__(self, name):
        with self._lock:
            if self._service is None:
                from account_manager_agent.handoff.service import HandoffService

                self._service = HandoffService()
        return getattr(self._service, name)


def _start_timeout_worker(handoffs, stop: threading.Event) -> None:
    """Every HANDOFF_TIMEOUT_CHECK_SECONDS (default 300), time out stale ISA offers during working
    hours and offer the next ISA, as v3's cron does. HANDOFF_TIMEOUT_CHECK_SECONDS=0 disables it."""
    interval = float(os.environ.get("HANDOFF_TIMEOUT_CHECK_SECONDS") or 300)
    if interval <= 0:
        return

    def loop():
        while not stop.wait(interval):
            try:
                handoffs.check_timeouts()
            except Exception:  # Missing configuration or a database outage: try again later.
                pass

    threading.Thread(target=loop, daemon=True, name="handoff-timeouts").start()


def run_due_followups(manager: JobManager, deals: DealStore, settings=None, force: bool = False,
                      now: datetime | None = None) -> dict:
    """Start a follow-up run for every deal whose follow-up day has arrived (as v3's hourly cron).

    Runs only when sasha_settings enable Sasha and automatic follow-ups, and, unless forced,
    during client hours (Mon–Fri 8:00–17:00 New York). Account Manager decides what to send.
    """
    from account_manager_agent.handoff.working_hours import ISA_TZ, is_working_time

    now = now or datetime.now(timezone.utc)
    if settings is None:
        from sasha_database.settings import SashaSettings

        settings = SashaSettings()
    values = settings.values("sasha_master_key", "auto_followup_enabled")
    if values.get("sasha_master_key", "").lower() != "enabled" or \
            values.get("auto_followup_enabled", "").lower() != "true":
        return {"started": [], "skipped": "Sasha or automatic follow-ups are disabled in sasha_settings"}
    if not force and not is_working_time(now):
        return {"started": [], "skipped": "outside client hours (Mon–Fri 8:00–17:00 New York)"}
    today = now.astimezone(ISA_TZ).date().isoformat()
    started = []
    for deal_id in manager.storage_factory().claim_due_followups(now.astimezone(timezone.utc).replace(tzinfo=None)):
        deals.touch(str(deal_id))
        run = manager.submit(str(deal_id), None, [], turn_type="follow_up", as_of_date=today)
        started.append({"deal_id": str(deal_id), "run_id": run.run_id})
    return {"started": started}


def _start_followup_worker(run_due, stop: threading.Event) -> None:
    """Every FOLLOWUP_CHECK_SECONDS (default 3600), run due follow-ups. 0 disables it."""
    interval = float(os.environ.get("FOLLOWUP_CHECK_SECONDS") or 3600)
    if interval <= 0:
        return

    def loop():
        while not stop.wait(interval):
            try:
                run_due()
            except Exception:  # Missing configuration or a database outage: try again later.
                pass

    threading.Thread(target=loop, daemon=True, name="followups").start()


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
