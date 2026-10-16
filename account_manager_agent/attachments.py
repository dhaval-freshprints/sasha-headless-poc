"""Fetch client artwork into an OpenAI-managed Sasha run workspace."""

from __future__ import annotations

import mimetypes
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from .artwork.inspection import ArtworkInspection, inspect_artwork
from .artwork.settings import ArtworkSettings

MAX_BYTES = 20 * 1024 * 1024
FETCH_TIMEOUT = 30

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".ai", ".eps"}

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class Attachment:
    handle: str
    path: Path
    name: str
    size: int
    from_previous_turn: bool = False
    inspection: ArtworkInspection | None = None

    def describe(self) -> str:
        base = f"{self.handle} ({self.name}, {self.size // 1024} KB)"
        if self.inspection is None:
            return base
        return f"{base}; {self.inspection.describe()}"


class AttachmentSet:
    def __init__(self, items: list[Attachment] | None = None):
        self.items = items or []
        self.unavailable_names: list[str] = []

    def __bool__(self) -> bool:
        return bool(self.items)

    def path_for(self, handle: str) -> Path:
        for item in self.items:
            if item.handle == handle:
                return item.path
        known = ", ".join(item.handle for item in self.items) or "none"
        raise ValueError(f"No attachment named {handle!r}. This turn has: {known}.")

    def describe(self) -> str:
        return "\n".join(f"- {item.describe()}" for item in self.items)


def fetch_to_directory(urls: list[str], target_dir: Path) -> AttachmentSet:
    if not urls:
        return AttachmentSet()
    target_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for index, url in enumerate(urls, start=1):
        items.append(_fetch_one(url, f"file_{index}", target_dir))
    return AttachmentSet(items)


def restore_saved_attachments(
    urls: list[str], target_dir: Path, files: AttachmentSet
) -> None:
    for url in urls:
        handle = f"file_{len(files.items) + 1}"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            attachment = _fetch_one(url, handle, target_dir)
        except (OSError, ValueError):
            files.unavailable_names.append(file_name(url))
            continue
        attachment.from_previous_turn = True
        files.items.append(attachment)


def _fetch_one(url: str, handle: str, target_dir: Path) -> Attachment:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{handle}: use an HTTP or HTTPS artwork URL.")
    name = file_name(url)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"{handle}: {suffix or 'no extension'} is not a file type we accept.")

    request = urllib.request.Request(url, headers={"User-Agent": "sasha-poc"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            content_type = response.headers.get_content_type()
            payload = response.read(MAX_BYTES + 1)
    except Exception as error:
        raise ValueError(f"{handle}: could not download artwork.") from error
    if len(payload) > MAX_BYTES:
        raise ValueError(f"{handle}: larger than {MAX_BYTES // 1024 // 1024} MB.")
    if not payload:
        raise ValueError(f"{handle}: the link returned nothing.")
    if content_type == "text/html" or payload.lstrip().lower().startswith(
        (b"<!doctype html", b"<html")
    ):
        raise ValueError(f"{handle}: the link is a web page, not a direct artwork file.")

    path = target_dir / f"{handle}{suffix}"
    path.write_bytes(payload)
    return Attachment(
        handle=handle,
        path=path,
        name=name,
        size=len(payload),
        inspection=inspect_artwork(path),
    )


def artwork_quality_guidance(settings: ArtworkSettings | None = None) -> str:
    """Task text that forces a suitability route before Design Tool upload."""
    quality = settings or ArtworkSettings.from_environment()
    return (
        "Before uploading any artwork, choose exactly one route for each used attachment and "
        "record it in /workspace/artifacts/artwork_decisions.json: "
        "`direct`, `extract`, `preserve_photo`, or `request_source`. "
        f"Effective PPI is usable artwork pixel width divided by print width in inches. "
        f"Direct raster uploads must be at least {quality.min_print_ppi:g} PPI at the intended "
        f"Design Tool width; aim for {quality.target_print_ppi:g} PPI when possible. "
        "Use `direct` only for isolated vector files or clean standalone rasters that already "
        "meet the minimum. For reference photos and screenshots, inventory every graphic and "
        "text block in the audit's elements array. Rebuild each text block as editable Design Tool "
        "text using the closest available font; preserve exact wording and never guess unreadable text. "
        "Use `extract` via `python /opt/sasha/extract_artwork.py` separately for each independent "
        "graphic, excluding all text and other graphics in --keep. Use a distinct --action-id per "
        "element/attempt and reuse it only for an identical retry. Never flatten the whole reference "
        "design into one image. Leave clean production-ready artwork intact unless edits are requested. "
        "Extract line art or logos embedded in a shirt/screenshot/UI/background, or when raster "
        "resolution is insufficient. Use `preserve_photo` for intended photographic content: "
        "keep original pixels and Design Tool crop only; never regenerate people, places, or "
        "animals. Use `request_source` when the design is unreadable or cannot be prepared "
        "faithfully. Never create print artwork by cropping a tiny region and enlarging it "
        "with Pillow, ImageMagick, canvas, or similar tools—upscaling cannot invent missing "
        "detail. Upload only files under /workspace/client-files (originals or host-broker "
        "extract outputs). After placement, recheck each image's effective PPI if width changed. "
        "Review garment-scale contrast, small text, spacing and collisions; improve readability "
        "within explicit client constraints, verify the saved result, and record findings and changes "
        "in visual_review. Follow the artwork reference guide for the element audit and font matching."
    )


def file_name(url: str) -> str:
    raw = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    cleaned = SAFE_NAME.sub("_", raw).strip("._") or "upload"
    guessed = mimetypes.guess_extension(mimetypes.guess_type(cleaned)[0] or "") or ""
    return cleaned if Path(cleaned).suffix else cleaned + guessed
