"""
/simulate — drive Sasha the way Riccelo drove Astra: one client message at a time.

POST /simulate  {"deal_id": 123, "client_message": "What's the price for 30?"}
                 client_message omitted or null  →  initial outreach
POST /reset     {"deal_id": 123}                →  forget conversation history

Run:  uvicorn api:app --port 8100
"""

import time
from datetime import datetime

from fastapi import FastAPI
from pydantic import BaseModel

import config
from brain import Brain
from browser import Browser

app = FastAPI(title="Sasha Browser POC")

# One browser, one conversation history per deal. POC-grade: in memory, single process.
_browser: Browser | None = None
_history_by_deal: dict[int, list[dict]] = {}


class SimulateRequest(BaseModel):
    deal_id: int
    client_message: str | None = None


class ResetRequest(BaseModel):
    deal_id: int


@app.on_event("startup")
def start_browser() -> None:
    global _browser
    _browser = Browser(headless=True)


@app.on_event("shutdown")
def stop_browser() -> None:
    if _browser:
        _browser.close()


@app.post("/simulate")
def simulate(request: SimulateRequest) -> dict:
    run_dir = config.RUNS_DIR / f"deal_{request.deal_id}" / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    history = _history_by_deal.setdefault(request.deal_id, [])
    brain = Brain(_browser, run_dir)

    started = time.time()
    result = brain.run(request.deal_id, request.client_message, history)
    elapsed = round(time.time() - started, 1)

    steps = [step.__dict__ for step in result.steps]

    return {
        "reply": result.reply,
        "seconds": elapsed,
        "step_count": len(steps),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "run_dir": str(run_dir),
        "steps": steps,
    }


@app.post("/reset")
def reset(request: ResetRequest) -> dict:
    _history_by_deal.pop(request.deal_id, None)
    return {"ok": True}
