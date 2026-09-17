"""
Roll up every run.json into one table: time and tokens per turn, plus totals and averages.

    python report.py                # all deals
    python report.py 303821         # one deal
    python report.py --csv > runs.csv
"""

import csv
import glob
import json
import statistics
import sys

import config


def load_runs(deal_id: str | None) -> list[dict]:
    pattern = f"deal_{deal_id}" if deal_id else "deal_*"
    runs = []
    for path in sorted(glob.glob(str(config.RUNS_DIR / pattern / "turn_*" / "run.json"))):
        run = json.load(open(path))
        run["turn"] = path.split("/")[-2].replace("turn_", "")
        run["deal_id"] = str(run["deal_id"])
        run["message"] = (run.get("client_message") or "(outreach)")[:44]
        run["step_count"] = len(run["steps"])
        run.setdefault("seconds", 0)
        run.setdefault("cached_tokens", 0)
        run.setdefault("uncached_tokens", run["input_tokens"] - run["cached_tokens"])
        runs.append(run)
    return runs


COLUMNS = ["deal_id", "turn", "step_count", "seconds", "input_tokens", "cached_tokens", "uncached_tokens", "output_tokens", "message"]


def print_table(runs: list[dict]) -> None:
    print(f"{'deal':7} {'turn':15} {'steps':>5} {'secs':>6} {'in':>10} {'cached':>10} {'fresh':>9} {'out':>6}  message")
    for r in runs:
        print(f"{r['deal_id']:7} {r['turn']:15} {r['step_count']:5} {r['seconds']:6.0f} "
              f"{r['input_tokens']:10,} {r['cached_tokens']:10,} {r['uncached_tokens']:9,} {r['output_tokens']:6,}  {r['message']}")


def print_totals(runs: list[dict]) -> None:
    n = len(runs)
    if n == 0:
        return
    total = lambda key: sum(r[key] for key in [key] for r in runs)
    secs = [r["seconds"] for r in runs if r["seconds"]]
    steps = [r["step_count"] for r in runs]
    print()
    print(f"turns: {n}   steps: {sum(steps)} (avg {statistics.mean(steps):.1f})")
    if secs:
        print(f"time:  total {sum(secs):.0f}s   avg {statistics.mean(secs):.0f}s   median {statistics.median(secs):.0f}s   "
              f"per step {sum(secs)/sum(steps):.1f}s")
    print(f"tokens in:  {total('input_tokens'):,}   cached {total('cached_tokens'):,}   fresh {total('uncached_tokens'):,}   "
          f"(avg fresh/turn {total('uncached_tokens')//n:,})")
    print(f"tokens out: {total('output_tokens'):,}   (avg {total('output_tokens')//n:,})")


def print_csv(runs: list[dict]) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(runs)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    runs = load_runs(args[0] if args else None)
    if "--csv" in sys.argv:
        print_csv(runs)
        return
    print_table(runs)
    print_totals(runs)


if __name__ == "__main__":
    main()
