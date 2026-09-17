"""
Run Sasha on any deal from the terminal. No server needed.

    python run_cli.py <deal_id>                          # initial outreach, then chat as the client
    python run_cli.py <deal_id> --once                   # initial outreach only, then exit
    python run_cli.py <deal_id> -m "price for 60?"       # one turn with a client message, then exit
    python run_cli.py <deal_id> --headed                 # watch the browser work

In chat mode: type the client's next message and press Enter. Empty line or Ctrl-C to quit.
"""

import argparse
from datetime import datetime

import config
from brain import Brain, RunResult
from browser import Browser


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Sasha on a deal.")
    parser.add_argument("deal_id", type=int)
    parser.add_argument("-m", "--message", help="Client message. If given, runs one turn and exits.")
    parser.add_argument("--once", action="store_true", help="Run the outreach turn only, then exit.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    browser = Browser(headless=not args.headed)
    history: list[dict] = []

    try:
        if args.message or args.once:
            run_turn(browser, args.deal_id, args.message or None, history, turn=1)
            return
        chat(browser, args.deal_id, history)
    except KeyboardInterrupt:
        pass
    finally:
        browser.close()


def chat(browser: Browser, deal_id: int, history: list[dict]) -> None:
    client_message = None
    turn = 0
    while True:
        turn += 1
        run_turn(browser, deal_id, client_message, history, turn)
        client_message = input("Client > ").strip()
        if not client_message:
            break


def run_turn(browser: Browser, deal_id: int, client_message: str | None, history: list[dict], turn: int) -> RunResult:
    run_dir = config.RUNS_DIR / f"deal_{deal_id}" / f"cli_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[deal {deal_id} · turn {turn}] working...")
    result = Brain(browser, run_dir).run(deal_id, client_message, history)

    print_steps(result)
    print(f"\n--- Sasha ({len(result.steps)} steps, {result.input_tokens} in / {result.output_tokens} out tokens) ---")
    print(result.reply)
    print(f"[screenshots + run.json in {run_dir}]\n")
    return result


def print_steps(result: RunResult) -> None:
    for step in result.steps:
        short_args = {k: (v[:50] if isinstance(v, str) else v) for k, v in step.args.items()}
        short_result = step.result[:70].replace("\n", " ")
        print(f"  [{step.index:02d}] {step.tool} {short_args} -> {short_result}")


if __name__ == "__main__":
    main()
