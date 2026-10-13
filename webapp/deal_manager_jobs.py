"""Independent persisted lookup jobs; never create sales runs or deal records."""

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from deal_manager_agent.models import LookupRequest
from deal_manager_agent.runner import DealManagerAgent


def now():
    return datetime.now(timezone.utc).isoformat()


class DealManagerJobs:
    def __init__(self, directory, runner_factory=DealManagerAgent.from_environment):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runner_factory = runner_factory
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deal-manager-agent")
        self.jobs = {}
        self.load_saved()

    def submit(self, request: LookupRequest):
        lookup_id = uuid.uuid4().hex
        job = {"lookup_id": lookup_id, "input": request.model_dump(), "status": "queued",
               "created_at": now(), "finished_at": None, "progress": [],
               "result": None, "error": None, "usage": None}
        with self.lock:
            self.jobs[lookup_id] = job
            self.save(job)
        self.executor.submit(self.execute, lookup_id)
        return self.get(lookup_id)

    def get(self, lookup_id):
        with self.lock:
            if lookup_id not in self.jobs:
                raise KeyError(lookup_id)
            return json.loads(json.dumps(self.jobs[lookup_id]))

    def list_all(self):
        with self.lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job["created_at"], reverse=True)
            return [{key: job[key] for key in (
                "lookup_id", "created_at", "status", "result",
            )} for job in jobs]

    def execute(self, lookup_id):
        self.update(lookup_id, status="running")
        try:
            runner = self.runner_factory(lambda message: self.add_progress(lookup_id, message))
            request = LookupRequest.model_validate(self.get(lookup_id)["input"])
            result = runner.run(request, lookup_id)
            self.update(lookup_id, status="completed", result=result.model_dump(),
                        finished_at=now(), usage=getattr(runner, "usage", None))
        except Exception as error:
            self.update(lookup_id, status="failed", error=str(error), finished_at=now())

    def add_progress(self, lookup_id, message):
        with self.lock:
            self.jobs[lookup_id]["progress"].append(message)
            self.save(self.jobs[lookup_id])

    def update(self, lookup_id, **values):
        with self.lock:
            self.jobs[lookup_id].update(values)
            self.save(self.jobs[lookup_id])

    def save(self, job):
        target = self.directory / f"{job['lookup_id']}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(job, indent=2), encoding="utf-8")
        temporary.replace(target)

    def load_saved(self):
        for path in self.directory.glob("*.json"):
            try:
                job = json.loads(path.read_text())
                if uuid.UUID(job["lookup_id"]).hex != path.stem:
                    continue
                LookupRequest.model_validate(job["input"])
                if job["status"] not in {"queued", "running", "completed", "failed"}:
                    continue
            except (ValueError, KeyError, TypeError, OSError):
                continue
            if job["status"] in {"running", "queued"}:
                job.update(status="failed", error="The server stopped before this lookup finished.",
                           finished_at=now())
                self.save(job)
            self.jobs[job["lookup_id"]] = job

    def shutdown(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
