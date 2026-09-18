"""
Run the eval asks and score them automatically.

    python evals/run.py            # every ask in evals/asks.json, in order
    python evals/run.py 2 4        # only asks 2 and 4

For each ask: reset the deal's transcript, snapshot the CRM state that matters (read-only),
run one turn, snapshot again, apply the checks. Writes evals/results/<timestamp>.json and
prints one row per ask. No human grading; what the checks cannot see is not scored.
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import memory
from brain import Brain
from browser import Browser
from toolset_executor import ToolsetExecutor

ASKS_FILE = Path(__file__).parent / "asks.json"
RESULTS_DIR = Path(__file__).parent / "results"

# Opus 5 list prices per million tokens. Cache read and write multipliers are the standard
# 0.1x and 1.25x; the run log does not split cache writes from plain input, so fresh tokens
# are priced at the plain rate and the estimate is a floor.
PRICE_INPUT = 5.0
PRICE_OUTPUT = 25.0
PRICE_CACHE_READ = 0.5


def main() -> None:
    wanted = {int(a) for a in sys.argv[1:]}
    asks = [a for a in json.loads(ASKS_FILE.read_text()) if not wanted or a["id"] in wanted]
    browser = Browser(headless=True)
    inspector = ToolsetExecutor(browser)
    results = []
    try:
        for ask in asks:
            results.append(run_ask(ask, browser, inspector))
            print_row(results[-1])
    finally:
        browser.close()
    save_results(results)
    print_totals(results)


# ---- one ask ------------------------------------------------------------------

def run_ask(ask: dict, browser: Browser, inspector: ToolsetExecutor) -> dict:
    deal_id = ask["deal"]
    memory.clear(deal_id)
    before = snapshot(inspector, deal_id, ask.get("proof"))

    run_dir = config.RUNS_DIR / f"deal_{deal_id}" / f"turn_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[ask {ask['id']}] deal {deal_id}: {ask['message'] or '(outreach)'}")
    started = time.monotonic()
    result = Brain(browser, run_dir, on_event=show_progress).run(deal_id, ask["message"])
    log = json.loads((run_dir / "run.json").read_text())

    after = snapshot(inspector, deal_id, ask.get("proof"))
    failures = apply_checks(ask.get("checks", {}), log, before, after)
    rounds = len({step["batch"] for step in log["steps"]})
    if rounds > ask.get("max_rounds", 15):
        failures.append(f"rounds {rounds} > {ask.get('max_rounds', 15)}")
    return {
        "id": ask["id"], "deal": deal_id, "message": ask["message"], "passed": not failures,
        "failures": failures, "rounds": rounds, "calls": len(log["steps"]),
        "images": log["screenshots_sent"], "stale_refs": log["stale_refs"], "halts": log["batch_halts"],
        "seconds": round(time.monotonic() - started), "fresh_tokens": log["uncached_tokens"],
        "cached_tokens": log["cached_tokens"], "output_tokens": log["output_tokens"],
        "cost_usd": estimate_cost(log), "before": before, "after": after, "reply": result.reply,
        "run_dir": str(run_dir),
    }


def show_progress(kind: str, payload) -> None:
    if kind == "step":
        flag = "ERR " if payload.is_error else ""
        print(f"    [{payload.index:02d}] {flag}{payload.tool}")


# ---- CRM state, read-only -------------------------------------------------------

def snapshot(inspector: ToolsetExecutor, deal_id: int, proof_id: int | None) -> dict:
    """What the checks compare: the deal's proof list, and one proof's revisions and quantity."""
    inspector.run("navigate", {"url": config.deal_url(deal_id)})
    deal_text = inspector.run("get_page_text", {}).replace("\n", " | ")
    proofs_part = re.search(r"Proofs \|(.*?)\| Orders", deal_text)
    state = {"proofs": re.findall(r"#(\d{6})", proofs_part.group(1)) if proofs_part else []}
    if proof_id:
        inspector.run("navigate", {"url": f"{config.FP_BASE_URL}/dashboard/proof/{proof_id}"})
        proof_text = inspector.run("get_page_text", {}).replace("\n", " | ")
        tree = inspector.run("read_page", {"filter": "interactive"})
        qty = re.search(r'spinbutton[^\n]*value="(\d+)"', tree)
        state["revisions"] = sorted(set(re.findall(r"Revision \d+", proof_text)))
        state["revision_pending"] = "Or Submit a Revision Request" not in proof_text
        state["qty"] = qty.group(1) if qty else None
    return state


# ---- checks -------------------------------------------------------------------

def apply_checks(checks: dict, log: dict, before: dict, after: dict) -> list[str]:
    """Return the names of the checks that failed. Each check kind is its own function."""
    failures = []
    for kind, expected in checks.items():
        check = CHECKS[kind]
        if not check(expected, log, before, after):
            failures.append(f"{kind}={expected!r}")
    return failures


def visited(urls: list[str], log, before, after) -> bool:
    seen = " ".join(step["args"].get("url", "") for step in log["steps"] if step["tool"] == "navigate")
    return all(url in seen for url in urls)


def not_visited(urls: list[str], log, before, after) -> bool:
    seen = " ".join(step["args"].get("url", "") for step in log["steps"] if step["tool"] == "navigate")
    return not any(url in seen for url in urls)


def reply_regex(patterns: list[str], log, before, after) -> bool:
    return all(re.search(p, log["reply"], re.IGNORECASE | re.DOTALL) for p in patterns)


def reply_not_regex(patterns: list[str], log, before, after) -> bool:
    return not any(re.search(p, log["reply"], re.IGNORECASE | re.DOTALL) for p in patterns)


def signoff(expected: bool, log, before, after) -> bool:
    ends_right = bool(re.search(r"(Best|Thanks),\s*\nSasha\s*$", log["reply"]))
    return ends_right == expected


def proofs(expected: str, log, before, after) -> bool:
    """'same' or '+1'."""
    delta = len(after["proofs"]) - len(before["proofs"])
    return delta == (0 if expected == "same" else 1)


def revisions(expected: str, log, before, after) -> bool:
    """'same' or '+1' on the ask's proof."""
    delta = len(after.get("revisions", [])) - len(before.get("revisions", []))
    return delta == (0 if expected == "same" else 1)


def revision_pending(expected: bool, log, before, after) -> bool:
    return after.get("revision_pending") == expected


def qty(expected: str, log, before, after) -> bool:
    """'same': the proof's saved quantity did not change."""
    return after.get("qty") == before.get("qty")


def no_errors(expected: bool, log, before, after) -> bool:
    return (log["batch_halts"] == 0) == expected


CHECKS = {
    "visited": visited, "not_visited": not_visited,
    "reply_regex": reply_regex, "reply_not_regex": reply_not_regex, "signoff": signoff,
    "proofs": proofs, "revisions": revisions, "revision_pending": revision_pending, "qty": qty,
    "no_errors": no_errors,
}


# ---- reporting ----------------------------------------------------------------

def estimate_cost(log: dict) -> float:
    return round(
        log["uncached_tokens"] / 1e6 * PRICE_INPUT
        + log["cached_tokens"] / 1e6 * PRICE_CACHE_READ
        + log["output_tokens"] / 1e6 * PRICE_OUTPUT, 3)


def print_row(r: dict) -> None:
    status = "PASS" if r["passed"] else "FAIL " + "; ".join(r["failures"])
    print(f"  -> {status}")
    print(f"     rounds {r['rounds']} · calls {r['calls']} · images {r['images']} · {r['seconds']}s · "
          f"fresh {r['fresh_tokens']:,} · cached {r['cached_tokens']:,} · ${r['cost_usd']:.2f}")


def print_totals(results: list[dict]) -> None:
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed · rounds {sum(r['rounds'] for r in results)} · "
          f"${sum(r['cost_usd'] for r in results):.2f} · {sum(r['seconds'] for r in results)}s")
    for r in results:
        mark = "ok " if r["passed"] else "FAIL"
        print(f"  {mark} ask {r['id']:>2}  rounds {r['rounds']:>2}  ${r['cost_usd']:.2f}  {r['message'] or '(outreach)'}")


def save_results(results: list[dict]) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"\nresults: {path}")


if __name__ == "__main__":
    main()
