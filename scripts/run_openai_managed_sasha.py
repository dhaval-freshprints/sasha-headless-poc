#!/usr/bin/env python3
"""Run one Fresh Prints sales turn through OpenAI-managed Sasha."""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import OpenAIManagedRunner
from openai_managed.environment import ApplicationEnvironment
from openai_managed.task import WORKFLOWS, SashaTask, validate_workflow


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Fresh Prints sales turn through OpenAI-managed Sasha."
    )
    parser.add_argument("deal_id", help="Fresh Prints deal ID")
    parser.add_argument("--workflow", required=True, choices=WORKFLOWS)
    parser.add_argument(
        "--message",
        help="Inbound client message; required for client-response-orchestrator",
    )
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=[],
        metavar="URL",
        help="Direct URL to a client artwork file; repeat for multiple files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print live orchestration and agent activity",
    )
    parser.add_argument(
        "--pricing",
        action="store_true",
        help="Print estimated OpenAI token pricing for the complete run",
    )
    parsed = parser.parse_args(arguments)
    try:
        validate_workflow(parsed.workflow, parsed.message)
    except ValueError as error:
        parser.error(str(error))
    return parsed


def main() -> int:
    load_dotenv(ROOT / ".env")
    arguments = parse_arguments()

    application = ApplicationEnvironment.from_environment()

    task = SashaTask(
        deal_id=arguments.deal_id,
        task_id=f"sasha-{arguments.deal_id}-{uuid.uuid4().hex[:8]}",
        deal_url=application.deal_url(arguments.deal_id),
        client_message=arguments.message,
        workflow=arguments.workflow,
        file_urls=tuple(arguments.file),
    )
    progress = ProgressReporter(
        (lambda message: print(message, file=sys.stderr))
        if arguments.verbose
        else None
    )
    cost_reporter = (
        CostReporter(lambda message: print(message, file=sys.stderr))
        if arguments.pricing
        else None
    )
    runner = OpenAIManagedRunner.from_environment(progress, cost_reporter)
    started_at = time.perf_counter()
    try:
        result = runner.run(task)
    finally:
        elapsed_seconds = time.perf_counter() - started_at
        minutes, seconds = divmod(int(elapsed_seconds + 0.5), 60)
        print(f"[timing] Total run: {minutes} min {seconds:02d} sec", file=sys.stderr)
    print(json.dumps(result.to_dict(), indent=2))
    if runner.last_run_directory:
        print(f"Artifacts: {runner.last_run_directory}", file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
