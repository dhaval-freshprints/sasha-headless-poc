"""Persistent background jobs for the Sasha web app."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import OpenAIManagedRunner
from openai_managed.task import SashaResult, SashaTask


FINAL_STATUSES = {"completed", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WebRun:
    run_id: str
    deal_id: str
    client_message: str | None
    file_urls: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    elapsed_seconds: float = 0.0
    progress: list[str] = field(default_factory=list)
    result: dict[str, str] | None = None
    error_message: str = ""
    artifact_directory: str = ""
    artifacts: list[str] = field(default_factory=list)
    pricing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WebRun":
        return cls(**value)


RunnerFactory = Callable[[Callable[[str], None]], Any]


class JobManager:
    def __init__(
        self,
        jobs_directory: Path,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.jobs_directory = jobs_directory
        self.jobs_directory.mkdir(parents=True, exist_ok=True)
        self.runner_factory = runner_factory or self._create_runner
        self.lock = threading.RLock()
        self.jobs: dict[str, WebRun] = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sasha-web")
        self._load_saved_jobs()

    def submit(
        self,
        deal_id: str,
        client_message: str | None,
        file_urls: list[str],
    ) -> WebRun:
        if not deal_id.isdigit():
            raise ValueError("deal_id must contain only digits")

        run = WebRun(
            run_id=uuid.uuid4().hex,
            deal_id=deal_id,
            client_message=client_message,
            file_urls=list(file_urls),
        )
        with self.lock:
            self.jobs[run.run_id] = run
            self._save(run)
        self.executor.submit(self._execute, run.run_id)
        return self.get(run.run_id)

    def get(self, run_id: str) -> WebRun:
        with self.lock:
            run = self.jobs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return WebRun.from_dict(run.to_dict())

    def list_all(self) -> list[WebRun]:
        with self.lock:
            values = [WebRun.from_dict(run.to_dict()) for run in self.jobs.values()]
        return sorted(values, key=lambda run: run.created_at, reverse=True)

    def list_for_deal(self, deal_id: str) -> list[WebRun]:
        return [run for run in self.list_all() if run.deal_id == deal_id]

    def artifact_path(self, run_id: str, relative_path: str) -> Path:
        run = self.get(run_id)
        if not run.artifact_directory:
            raise FileNotFoundError(relative_path)

        root = (Path(run.artifact_directory) / "workspace" / "artifacts").resolve()
        candidate = (root / relative_path).resolve()
        if candidate == root or root not in candidate.parents or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _execute(self, run_id: str) -> None:
        started = time.perf_counter()
        self._start(run_id)

        try:
            run = self.get(run_id)
            runner = self.runner_factory(lambda message: self._add_progress(run_id, message))
            task = self._make_task(run)
            result = runner.run(task)
            self._finish(run_id, runner, result, started)
        except Exception as error:
            self._fail(run_id, str(error), started)

    def _make_task(self, run: WebRun) -> SashaTask:
        base_url = os.environ.get("FP_BASE_URL", "").rstrip("/")
        if not base_url:
            raise ValueError("FP_BASE_URL must be set")
        return SashaTask(
            deal_id=run.deal_id,
            task_id=f"web-{run.deal_id}-{run.run_id[:12]}",
            deal_url=f"{base_url}/dashboard/sales-pipeline/deal?id={run.deal_id}",
            client_message=run.client_message,
            file_urls=tuple(run.file_urls),
        )

    def _start(self, run_id: str) -> None:
        self._change(
            run_id,
            status="running",
            started_at=utc_now(),
            progress=["Sasha run started."],
        )

    def _finish(
        self,
        run_id: str,
        runner: Any,
        result: SashaResult,
        started: float,
    ) -> None:
        run_directory = getattr(runner, "last_run_directory", None)
        pricing = getattr(runner, "last_cost_estimate", None)
        self._change(
            run_id,
            status=result.status,
            finished_at=utc_now(),
            elapsed_seconds=round(time.perf_counter() - started, 1),
            result=result.to_dict(),
            error_message=result.failure_message,
            artifact_directory=str(run_directory or ""),
            artifacts=self._list_artifacts(run_directory),
            pricing=pricing.to_dict() if pricing is not None else None,
        )

    def _fail(self, run_id: str, message: str, started: float) -> None:
        self._change(
            run_id,
            status="failed",
            finished_at=utc_now(),
            elapsed_seconds=round(time.perf_counter() - started, 1),
            error_message=message,
        )

    def _add_progress(self, run_id: str, message: str) -> None:
        with self.lock:
            run = self.jobs[run_id]
            run.progress.append(message)
            self._save(run)

    def _change(self, run_id: str, **values: Any) -> None:
        with self.lock:
            run = self.jobs[run_id]
            for name, value in values.items():
                setattr(run, name, value)
            self._save(run)

    def _load_saved_jobs(self) -> None:
        for path in self.jobs_directory.glob("*.json"):
            try:
                run = WebRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if run.status not in FINAL_STATUSES:
                run.status = "failed"
                run.finished_at = utc_now()
                run.error_message = "The web server stopped before this run completed."
                self._save(run)
            self.jobs[run.run_id] = run

    def _save(self, run: WebRun) -> None:
        path = self.jobs_directory / f"{run.run_id}.json"
        temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(run.to_dict(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _create_runner(write: Callable[[str], None]) -> OpenAIManagedRunner:
        return OpenAIManagedRunner.from_environment(
            ProgressReporter(write),
            CostReporter(write),
        )

    @staticmethod
    def _list_artifacts(run_directory: Path | None) -> list[str]:
        if run_directory is None:
            return []
        root = run_directory / "workspace" / "artifacts"
        if not root.is_dir():
            return []
        return sorted(
            str(path.relative_to(root))
            for path in root.rglob("*")
            if path.is_file()
        )
