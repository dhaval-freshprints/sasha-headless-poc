#!/usr/bin/env python3
"""Run one Fresh Prints QA sales turn through OpenAI-managed Sasha."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai_managed.pricing import CostReporter
from openai_managed.progress import ProgressReporter
from openai_managed.runner import OpenAIManagedRunner
from openai_managed.task import SashaTask


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one Fresh Prints QA sales turn through OpenAI-managed Sasha."
    )
    parser.add_argument("deal_id", help="Fresh Prints QA deal ID")
    parser.add_argument(
        "--message",
        help="Inbound client message; omit it to generate initial outreach",
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
    return parser.parse_args(arguments)


def main() -> int:
    load_dotenv(ROOT / ".env")
    arguments = parse_arguments()

    base_url = os.environ.get("FP_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("FP_BASE_URL must be set")

    task = SashaTask(
        deal_id=arguments.deal_id,
        task_id=f"sasha-{arguments.deal_id}-{uuid.uuid4().hex[:8]}",
        deal_url=(
            f"{base_url}/dashboard/sales-pipeline/deal?id={arguments.deal_id}"
        ),
        client_message=arguments.message,
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
    result = runner.run(task)
    print(json.dumps(result.to_dict(), indent=2))
    if runner.last_run_directory:
        print(f"Artifacts: {runner.last_run_directory}", file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
