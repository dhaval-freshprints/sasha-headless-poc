"""
/simulate — drive Sasha the way Riccelo drove Astra: one client message at a time.

POST /simulate  {"deal_id": 123, "client_message": "What's the price for 30?"}
                 client_message omitted or null  →  initial outreach
POST /reset     {"deal_id": 123}                →  forget the conversation
GET  /transcript/{deal_id}                      →  client ↔ Sasha transcript

Conversation is remembered per deal on disk (runs/deal_<id>/), so it survives restarts.

Run:  uvicorn api:app --port 8100
"""

import time
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import config
import memory
from brain import Brain
from browser import Browser

app = FastAPI(title="Sasha Browser POC")

_browser: Browser | None = None   # one browser, one request at a time (POC)


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
    run_dir = config.RUNS_DIR / f"deal_{request.deal_id}" / f"turn_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    result = Brain(_browser, run_dir).run(request.deal_id, request.client_message)

    return {
        "reply": result.reply,
        "seconds": round(time.time() - started, 1),
        "step_count": len(result.steps),
        "input_tokens": result.input_tokens,
        "cached_tokens": result.cached_tokens,
        "output_tokens": result.output_tokens,
        "run_dir": str(run_dir),
        "steps": [step.__dict__ for step in result.steps],
    }


@app.post("/reset")
def reset(request: ResetRequest) -> dict:
    memory.clear(request.deal_id)
    return {"ok": True}


@app.get("/transcript/{deal_id}", response_class=PlainTextResponse)
def transcript(deal_id: int) -> str:
    path = memory.deal_dir(deal_id) / "transcript.md"
    return path.read_text() if path.exists() else ""
