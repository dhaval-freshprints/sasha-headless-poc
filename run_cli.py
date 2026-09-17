"""
Run Sasha on any deal from the terminal. No server needed.

    python run_cli.py <deal_id>                      # outreach if new, then chat as the client
    python run_cli.py <deal_id> --once               # outreach only, then exit
    python run_cli.py <deal_id> -m "price for 60?"   # one client reply, then exit
    python run_cli.py <deal_id> --reset              # forget this deal's conversation
    python run_cli.py <deal_id> --headed             # watch the browser work

Memory per deal is the transcript only (runs/deal_<id>/transcript.md). Each turn re-reads the CRM.
In chat mode: type the client's next message and press Enter. Empty line or Ctrl-C to quit.
"""

import argparse
from datetime import datetime

import config
import memory
from brain import Brain, RunResult
from browser import Browser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Sasha on a deal.")
    parser.add_argument("deal_id", type=int)
    parser.add_argument("-m", "--message", help="Client reply. Runs one turn and exits.")
    parser.add_argument("--once", action="store_true", help="Run the outreach turn only, then exit.")
    parser.add_argument("--reset", action="store_true", help="Forget this deal's conversation first.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.reset:
        memory.clear(args.deal_id)
        print(f"Cleared conversation for deal {args.deal_id}.")
        if not (args.message or args.once):
            return

    browser = Browser(headless=not args.headed)
    try:
        if args.message:
            run_turn(browser, args.deal_id, args.message)
        elif args.once:
            run_turn(browser, args.deal_id, None)
        else:
            chat(browser, args.deal_id)
    except KeyboardInterrupt:
        pass
    finally:
        browser.close()


def chat(browser: Browser, deal_id: int) -> None:
    if not memory.load_transcript(deal_id):
        run_turn(browser, deal_id, None)
    else:
        print(f"[deal {deal_id}] resuming conversation (see runs/deal_{deal_id}/transcript.md)")
    while True:
        client_message = input("Client > ").strip()
        if not client_message:
            break
        run_turn(browser, deal_id, client_message)


def run_turn(browser: Browser, deal_id: int, client_message: str | None) -> RunResult:
    run_dir = config.RUNS_DIR / f"deal_{deal_id}" / f"turn_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    label = "client reply" if client_message else "outreach"
    print(f"\n[deal {deal_id} · {label}] working...")
    result = Brain(browser, run_dir).run(deal_id, client_message)

    print_steps(result)
    print(f"\n--- Sasha ({len(result.steps)} steps, {result.input_tokens} in "
          f"({result.cached_tokens} cached) / {result.output_tokens} out tokens) ---")
    print(result.reply)
    print(f"[screenshots + run.json in {run_dir}]\n")
    return result


def print_steps(result: RunResult) -> None:
    for step in result.steps:
        short_args = {k: (v[:50] if isinstance(v, str) else v) for k, v in step.args.items()}
        short_result = step.result[:70].replace("\n", " ")
        print(f"  [{step.index:02d}] {step.tool} {short_args} -> {short_result}  [tree {step.tree_chars // 1000}K]")


if __name__ == "__main__":
    main()
