"""All settings in one place. Read from .env."""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent

FP_BASE_URL = os.environ["FP_BASE_URL"].rstrip("/")
DT_BASE_URL = os.environ.get("DT_BASE_URL", "https://dt-qa.internal-fp.com").rstrip("/")   # the Design Tool
FP_LOGIN_URL = os.environ["FP_LOGIN_URL"]
FP_USER = os.environ["FP_USER"]
FP_PASSWORD = os.environ["FP_PASSWORD"]

# Claude via the Anthropic API. The model must support browser_toolset_20260801.
# LLM_API_KEY is accepted too, so an older .env keeps working.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or os.environ["LLM_API_KEY"]
MODEL = os.environ["MODEL"]

MAX_STEPS = int(os.environ.get("MAX_STEPS", "40"))
PAGE_TEXT_MAX_CHARS = int(os.environ.get("PAGE_TEXT_MAX_CHARS", "30000"))
READ_PAGE_MAX_CHARS = 50000   # the toolset contract caps read_page output here

# Hosts the browser may navigate to. Anything else is refused.
_default_hosts = {
    urlparse(FP_BASE_URL).hostname,
    urlparse(FP_LOGIN_URL).hostname,
    urlparse(DT_BASE_URL).hostname,
    "www.freshprints.com",
}
ALLOWED_HOSTS = {
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host.strip()
} or _default_hosts

AUTH_DIR = ROOT / "auth"          # persistent Chromium profile (logged-in session)
RUNS_DIR = ROOT / os.environ.get("RUNS_DIR", "runs")


def deal_url(deal_id: int) -> str:
    return f"{FP_BASE_URL}/dashboard/sales-pipeline/deal?id={deal_id}"
