"""Shared names and defaults for host-brokered artwork extraction."""

from __future__ import annotations

import re

BROKER_DIR_NAME = ".artwork-extract"
CLIENT_FILES_NAME = "client-files"
ARTIFACTS_NAME = "artifacts"
REQUESTS_DIR_NAME = "requests"
RESPONSES_DIR_NAME = "responses"

# Container mount root; override with SASHA_WORKSPACE when testing outside Docker.
DEFAULT_WORKSPACE = "/workspace"

ACTION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SHAPES = {
    "square": "1024x1024",
    "wide": "1536x1024",
    "tall": "1024x1536",
}

DEFAULT_MODEL = "gpt-image-1"
DEFAULT_API_URL = "https://api.openai.com/v1/images/edits"
DEFAULT_EXTRACT_TIMEOUT_SECONDS = 300.0
DEFAULT_CLIENT_WAIT_SECONDS = 320.0
DEFAULT_POLL_SECONDS = 0.05
DEFAULT_MIN_PRINT_PPI = 150.0
DEFAULT_TARGET_PRINT_PPI = 300.0

ARTWORK_DECISIONS_FILE = "artwork_decisions.json"
ARTWORK_ROUTES = ("direct", "extract", "preserve_photo", "request_source")
VECTOR_SUFFIXES = {".svg", ".pdf", ".ai", ".eps"}
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

ENV_API_KEY = "OPENAI_API_KEY"
ENV_MODEL = "ARTWORK_IMAGE_MODEL"
ENV_API_URL = "ARTWORK_IMAGE_API_URL"
ENV_EXTRACT_TIMEOUT = "ARTWORK_EXTRACT_TIMEOUT_SECONDS"
ENV_MIN_PRINT_PPI = "ARTWORK_MIN_PRINT_PPI"
ENV_TARGET_PRINT_PPI = "ARTWORK_TARGET_PRINT_PPI"
ENV_WORKSPACE = "SASHA_WORKSPACE"

INSTRUCTIONS = (
    "This is a photo or screenshot of a garment or mockup with a printed design. "
    "Recreate only the printed design as flat, print-ready artwork on a perfectly flat, solid pure "
    "green (#00FF00) background with no gradient, shadow or texture. "
    "Keep every word spelled exactly as shown, with the same line breaks, fonts, layout and "
    "proportions, and each element's exact colors. Fit the whole design inside the image with a "
    "clear margin, so nothing touches or crosses an edge. "
    "Leave out every real photograph (of people, places or animals) completely and leave its area "
    "empty; never draw or recreate a photo. "
    "Remove the garment, fabric texture, folds, shadows, phone or app interface, and anything "
    "else that is not part of the print. "
    "If the print is blurry, low resolution, at an angle, folded, stretched or partly hidden, "
    "redraw it cleanly and flat so it looks almost the same as the original: the same wording, "
    "line breaks, fonts or the closest matching style, colors, graphics, proportions and layout. "
    "Fill in only what is clearly implied, such as a letter hidden by a fold. "
    "Do not add, invent or restyle anything."
)
