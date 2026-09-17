"""All settings in one place. Read from .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent

FP_BASE_URL = os.environ["FP_BASE_URL"].rstrip("/")
FP_LOGIN_URL = os.environ["FP_LOGIN_URL"]
FP_USER = os.environ["FP_USER"]
FP_PASSWORD = os.environ["FP_PASSWORD"]

# Any OpenAI-compatible endpoint: OpenAI itself, Azure, a gateway, vLLM, LiteLLM...
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.environ["LLM_API_KEY"]
MODEL = os.environ["MODEL"]

MAX_STEPS = int(os.environ.get("MAX_STEPS", "40"))
SNAPSHOT_MAX_CHARS = int(os.environ.get("SNAPSHOT_MAX_CHARS", "30000"))
# Screenshots kept verbatim in the saved history. Older ones become a one-line note.
SCREENSHOTS_TO_KEEP = int(os.environ.get("SCREENSHOTS_TO_KEEP", "3"))

AUTH_DIR = ROOT / "auth"          # persistent Chromium profile (logged-in session)
RUNS_DIR = ROOT / os.environ.get("RUNS_DIR", "runs")


def deal_url(deal_id: int) -> str:
    return f"{FP_BASE_URL}/dashboard/sales-pipeline/deal?id={deal_id}"
