#!/usr/bin/env python3
"""Generate one read-only outreach draft for a Fresh Prints QA deal."""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai_managed.outreach import OutreachTask
from openai_managed.progress import ProgressReporter
from openai_managed.runner import OpenAIManagedOutreachRunner


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate an outreach draft through OpenAI-managed Sasha."
    )
    parser.add_argument("deal_id", help="Fresh Prints QA deal ID")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print live orchestration and agent activity",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    arguments = parse_arguments()

    base_url = os.environ.get("FP_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("FP_BASE_URL must be set")

    task = OutreachTask(
        deal_id=arguments.deal_id,
        task_id=f"outreach-{arguments.deal_id}-{uuid.uuid4().hex[:8]}",
        deal_url=(
            f"{base_url}/dashboard/sales-pipeline/deal?id={arguments.deal_id}"
        ),
    )
    progress = ProgressReporter(
        (lambda message: print(message, file=sys.stderr))
        if arguments.verbose
        else None
    )
    runner = OpenAIManagedOutreachRunner.from_environment(progress)
    result = runner.run(task)
    print(json.dumps(result.to_dict(), indent=2))
    if runner.last_run_directory:
        print(f"Artifacts: {runner.last_run_directory}", file=sys.stderr)
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
