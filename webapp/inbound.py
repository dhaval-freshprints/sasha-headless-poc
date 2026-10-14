"""Client response without a deal ID: find the deal, validate it, then run Account Manager.

1. Code looks up the sender's active deal by email or phone.
2. Only when that is not one deal does the Deal Manager agent find it from the message.
3. The deal is validated and the client's active deals consolidated, as Sasha v3 does
   (`deal_manager_agent.deal_resolution`).
4. Account Manager runs on the resulting deal. Any other outcome is saved for a person.

Internal team mail (an email from INTERNAL_SENDER_DOMAINS, default freshprints.com) skips
step 1, because a staff address never maps to a client: the Deal Manager agent finds the
deal from the message. Validation never merges deals for it, it never joins an open
conversation, and Account Manager handles it with its internal-email skill.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, field_validator, model_validator

from deal_manager_agent.deal_lookup import ActiveDealFinder
from deal_manager_agent.deal_resolution import DealResolver
from deal_manager_agent.models import LookupRequest, normalize_phone
from deal_manager_agent.settings import env_value

IN_PROGRESS = {"queued", "resolving", "agent_running", "validating"}
FINAL_STATUSES = {"run_started", "needs_review", "no_match", "failed"}
STATUSES = IN_PROGRESS | FINAL_STATUSES
RUN_FINAL_STATUSES = {"completed", "failed"}
MAX_BODY = 20_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def internal_sender_domains() -> set[str]:
    configured = env_value("INTERNAL_SENDER_DOMAINS") or "freshprints.com"
    return {domain.strip().lower().lstrip("@") for domain in configured.split(",") if domain.strip()}


class InboundRequest(BaseModel):
    """Webhook payload. With a phone the body is an array of SMS messages, else one email body.

    A phone without a + is a US/CA number, as in v3-crons (last ten digits).
    """

    email: str | None = None
    phone: str | None = None
    subject: str = Field(default="", max_length=1000)
    body: str | list[str]
    thread_id: str | None = None
    message_id: str | None = Field(default=None, max_length=500)

    @field_validator("email", "phone", "thread_id", "message_id", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def check_channel(self) -> "InboundRequest":
        if not self.email and not self.phone:
            raise ValueError("Supply an email or a phone number")
        if self.phone and not isinstance(self.body, list):
            raise ValueError("body must be an array of messages when phone is provided")
        if not self.phone and isinstance(self.body, list):
            raise ValueError("body must be a string for an email")
        self.to_lookup()  # Validate every derived field now so the webhook answers 422.
        return self

    @property
    def messages(self) -> list[str]:
        values = self.body if isinstance(self.body, list) else [self.body]
        return [value.strip() for value in values if value.strip()]

    @property
    def text(self) -> str:
        return "\n".join(self.messages)

    @property
    def is_internal(self) -> bool:
        """An email from a Fresh Prints team or system address. SMS is always from a client."""
        if self.phone or not self.email:
            return False
        return self.email.rsplit("@", 1)[-1].strip().lower() in internal_sender_domains()

    @property
    def source(self) -> str:
        return "internal" if self.is_internal else "client"

    def to_lookup(self) -> LookupRequest:
        if not self.text:
            raise ValueError("body has no message text")
        if self.phone:
            return LookupRequest(
                sender_phone=normalize_phone(self.phone, "US"),
                thread_id=self.thread_id, subject=self.subject, body=self.text,
            )
        return LookupRequest(
            sender_email=self.email, thread_id=self.thread_id,
            subject=self.subject, body=self.text,
        )

    def client_message(self, later_messages=()) -> str:
        """The client message for Account Manager, in the Deals form's envelope format.

        later_messages arrived while the deal was being found; they follow in order.
        """
        if self.phone:
            envelope = [("Channel", "SMS"), ("From", self.to_lookup().sender_phone)]
            body = "\n".join([self.text, *later_messages])
        else:
            envelope = [("Channel", "Email"), ("From", self.email), ("Subject", self.subject.strip())]
            body = "".join([self.text, *(f"\n\nFollow-up message:\n{text}" for text in later_messages)])
        header = "\n".join(f"{label}: {value}" for label, value in envelope if value)
        return f"{header}\n\n{body}"

    def continues(self, other: "InboundRequest") -> bool:
        """True when this message belongs to other's conversation: the same phone for SMS,
        or the same email address and thread for email."""
        if self.phone or other.phone:
            return bool(self.phone and other.phone) and \
                self.to_lookup().sender_phone == other.to_lookup().sender_phone
        return (self.email or "").lower() == (other.email or "").lower() \
            and bool(self.thread_id) and self.thread_id == other.thread_id


StartRun = Callable[[str, str, dict], Any]  # (deal_id, client_message, deal_check) -> run
SendMessage = Callable[[str, str, str], dict]  # (run_id, submission_id, text) -> run message
RunState = Callable[[str], dict]  # run_id -> the run as a dict (status, accepting_messages, messages)


class InboundJobs:
    """Persisted inbound records. Resolution runs off the request thread."""

    def __init__(self, directory: Path, finder: ActiveDealFinder, lookups, start_run: StartRun,
                 resolver: DealResolver | None = None, send_message: SendMessage | None = None,
                 run_state: RunState | None = None, retry_seconds: float = 2):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.finder = finder
        self.resolver = resolver or DealResolver()
        self.lookups = lookups
        self.start_run = start_run
        self.send_message = send_message
        self.run_state = run_state
        self.retry_seconds = retry_seconds
        self.delivering: set[str] = set()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="inbound")
        self.records: dict[str, dict] = {}
        self.by_message_id: dict[str, str] = {}
        self._load_saved()

    def submit(self, request: InboundRequest) -> dict:
        """Queue a message. A repeated message_id returns the record already created.

        A client message that continues an open conversation (same phone, or same email
        thread) joins it as an additional message instead of starting a second flow.
        Internal team mail always starts its own flow.
        """
        with self.lock:
            existing = self.by_message_id.get(request.message_id or "")
            if existing:
                return self.get(existing)
            open_id = None if request.is_internal else self._open_conversation(request)
            if open_id:
                self._add_message(open_id, request.text)
                if request.message_id:
                    self.by_message_id[request.message_id] = open_id
                return {**self.get(open_id), "attached": True}
            inbound_id = uuid.uuid4().hex
            record = {
                "inbound_id": inbound_id, "status": "queued", "created_at": utc_now(),
                "source": request.source,
                "finished_at": None, "input": request.model_dump(), "deal_id": None,
                "found_deal_id": None, "resolved_by": None, "deal_search": None,
                "lookup_id": None, "resolution": None, "run_id": None, "reason": None,
                "error": None, "messages": [],
            }
            self.records[inbound_id] = record
            if request.message_id:
                self.by_message_id[request.message_id] = inbound_id
            self._save(record)
        self.executor.submit(self._resolve, inbound_id)
        return self.get(inbound_id)

    def get(self, inbound_id: str) -> dict:
        with self.lock:
            if inbound_id not in self.records:
                raise KeyError(inbound_id)
            record = json.loads(json.dumps(self.records[inbound_id]))
        run = self._run(record)
        if run:
            record["run_status"] = run["status"]
            delivered = {item["submission_id"]: item for item in run.get("messages", [])}
            for message in record["messages"]:
                if message["status"] == "forwarded" and message["submission_id"] in delivered:
                    message["delivery"] = delivered[message["submission_id"]]["status"]
                    message["error"] = delivered[message["submission_id"]].get("error", "")
        record["accepting_messages"] = self._is_open(record, run)
        return record

    def add_message(self, inbound_id: str, text: str) -> dict:
        """Another client message for this conversation, like an additional message on a run."""
        if not text.strip() or len(text) > 20_000:
            raise ValueError("Client message must contain between 1 and 20,000 characters.")
        with self.lock:
            if inbound_id not in self.records:
                raise KeyError(inbound_id)
            return self._add_message(inbound_id, text.strip())

    def _add_message(self, inbound_id: str, text: str) -> dict:
        record = self.records[inbound_id]
        if not self._is_open(record, self._run(record)):
            raise ValueError("This client response is finished. Send the message as a new client response.")
        # Before Account Manager starts, hold it and send it with the first message.
        status = "held" if record["status"] in IN_PROGRESS else "pending"
        message = {"submission_id": uuid.uuid4().hex, "text": text, "status": status,
                   "error": "", "created_at": utc_now()}
        record["messages"].append(message)
        self._save(record)
        if status == "pending":
            self._start_delivery(inbound_id)
        return dict(message)

    def _run(self, record: dict) -> dict | None:
        if not (record.get("run_id") and self.run_state):
            return None
        try:
            return self.run_state(record["run_id"])
        except KeyError:
            return None

    def _is_open(self, record: dict, run: dict | None) -> bool:
        if record["status"] in IN_PROGRESS:
            return True
        return record["status"] == "run_started" and run is not None \
            and run["status"] not in RUN_FINAL_STATUSES

    def _open_conversation(self, request: InboundRequest) -> str | None:
        for record in sorted(self.records.values(), key=lambda item: item["created_at"], reverse=True):
            earlier = InboundRequest.model_validate(record["input"])
            if not earlier.is_internal and request.continues(earlier) and self._is_open(record, self._run(record)):
                return record["inbound_id"]
        return None

    def _start_delivery(self, inbound_id: str) -> None:
        if inbound_id in self.delivering:
            return
        self.delivering.add(inbound_id)
        threading.Thread(target=self._deliver, args=(inbound_id,), daemon=True,
                         name=f"inbound-deliver-{inbound_id[:8]}").start()

    def _deliver(self, inbound_id: str) -> None:
        """Forward pending messages to the running Account Manager, waiting while it is not
        yet accepting messages. Give up only when the run has finished."""
        while True:
            with self.lock:
                record = self.records[inbound_id]
                pending = [item for item in record["messages"] if item["status"] == "pending"]
                if not pending:
                    self.delivering.discard(inbound_id)
                    return
                message, run_id = dict(pending[0]), record["run_id"]
            try:
                self.send_message(run_id, message["submission_id"], message["text"])
                self._set_message(inbound_id, message["submission_id"], status="forwarded")
            except ValueError as error:
                run = self._run(record)
                if run is None or run["status"] in RUN_FINAL_STATUSES:
                    self._set_message(inbound_id, message["submission_id"], status="not_delivered",
                                      error=f"Account Manager finished first: {error}")
                else:
                    time.sleep(self.retry_seconds)
            except Exception as error:
                self._set_message(inbound_id, message["submission_id"], status="not_delivered",
                                  error=str(error))

    def _set_message(self, inbound_id: str, submission_id: str, **values: Any) -> None:
        with self.lock:
            record = self.records[inbound_id]
            for message in record["messages"]:
                if message["submission_id"] == submission_id:
                    message.update(values)
            self._save(record)

    def list_all(self) -> list[dict]:
        with self.lock:
            records = sorted(self.records.values(), key=lambda item: item["created_at"], reverse=True)
            return [json.loads(json.dumps(item)) for item in records]

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _resolve(self, inbound_id: str) -> None:
        try:
            request = InboundRequest.model_validate(self.get(inbound_id)["input"])
            self._update(inbound_id, status="resolving")
            if not request.is_internal:  # A staff address never maps to a client's deal.
                search = self.finder.find(request.to_lookup())
                self._update(inbound_id, deal_search=search.to_dict())
                if search.outcome == "found":
                    self._validate_and_run(inbound_id, request, search.deal_id, "code")
                    return
            self._update(inbound_id, status="agent_running")
            self.lookups.submit(
                request.to_lookup(),
                on_finished=lambda job: self._on_agent_finished(inbound_id, request, job),
            )
        except Exception as error:
            self._fail(inbound_id, str(error))

    def _on_agent_finished(self, inbound_id: str, request: InboundRequest, job: dict) -> None:
        try:
            self._update(inbound_id, lookup_id=job["lookup_id"])
            if job["status"] != "completed":
                self._fail(inbound_id, job.get("error") or "The deal lookup failed")
                return
            result = job["result"]
            if result["status"] == "matched":
                # Validation can write to the CRM; keep it off the agent's single worker.
                self.executor.submit(self._validate_and_run, inbound_id, request,
                                     result["deal_id"], "agent")
            elif result["status"] == "error":
                self._fail(inbound_id, result["reason"])
            else:  # needs_review or no_match: nothing is sent or run without a verified deal.
                self._update(inbound_id, status=result["status"], reason=result["reason"],
                             finished_at=utc_now())
                self._drop_held(inbound_id)
        except Exception as error:
            self._fail(inbound_id, str(error))

    def _validate_and_run(self, inbound_id, request, deal_id, resolved_by) -> None:
        try:
            self._update(inbound_id, status="validating", found_deal_id=deal_id,
                         resolved_by=resolved_by)
            resolution = self.resolver.resolve(
                deal_id, "internal_email" if request.is_internal else "client_response")
            self._update(inbound_id, resolution=resolution.to_dict())
            if resolution.status != "proceed":
                self._update(inbound_id, status="needs_review", finished_at=utc_now(),
                             reason=f"Deal {deal_id} did not pass validation: {resolution.reason}")
                self._drop_held(inbound_id)
                return
            with self.lock:  # Messages arriving now are either included here or forwarded.
                record = self.records[inbound_id]
                held = [item for item in record["messages"] if item["status"] == "held"]
                run = self.start_run(resolution.deal_id,
                                     request.client_message([item["text"] for item in held]),
                                     resolution.to_dict())
                for item in held:
                    item["status"] = "included"
                self._update(inbound_id, status="run_started", deal_id=resolution.deal_id,
                             run_id=run["run_id"], reason=resolution.reason, finished_at=utc_now())
        except Exception as error:
            self._fail(inbound_id, str(error))

    def _fail(self, inbound_id: str, message: str) -> None:
        self._update(inbound_id, status="failed", error=message, finished_at=utc_now())
        self._drop_held(inbound_id)

    def _drop_held(self, inbound_id: str) -> None:
        with self.lock:
            for message in self.records[inbound_id]["messages"]:
                if message["status"] in ("held", "pending"):
                    message.update(status="not_delivered", error="No Account Manager run was started.")
            self._save(self.records[inbound_id])

    def _update(self, inbound_id: str, **values: Any) -> None:
        with self.lock:
            self.records[inbound_id].update(values)
            self._save(self.records[inbound_id])

    def _save(self, record: dict) -> None:
        target = self.directory / f"{record['inbound_id']}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temporary.replace(target)

    def _load_saved(self) -> None:
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text())
                if uuid.UUID(record["inbound_id"]).hex != path.stem:
                    continue
                InboundRequest.model_validate(record["input"])
                if record["status"] not in STATUSES:
                    continue
            except (ValueError, KeyError, TypeError, OSError):
                continue
            record.setdefault("messages", [])
            record.setdefault("source", InboundRequest.model_validate(record["input"]).source)
            if record["status"] not in FINAL_STATUSES:
                record.update(status="failed", finished_at=utc_now(),
                              error="The server stopped before this message was routed.")
            for message in record["messages"]:
                if message["status"] in ("held", "pending"):  # Active runs do not survive a restart.
                    message.update(status="not_delivered", error="The server restarted before delivery.")
            self._save(record)
            self.records[record["inbound_id"]] = record
            if record["input"].get("message_id"):
                self.by_message_id[record["input"]["message_id"]] = record["inbound_id"]
