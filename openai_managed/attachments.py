"""Fetch client artwork into an OpenAI-managed Sasha run workspace."""

import mimetypes
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

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

    def describe(self) -> str:
        return f"{self.handle} ({self.name}, {self.size // 1024} KB)"


class AttachmentSet:
    def __init__(self, items: list[Attachment] | None = None):
        self.items = items or []

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


def _fetch_one(url: str, handle: str, target_dir: Path) -> Attachment:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{handle}: use an HTTP or HTTPS artwork URL.")
    name = _file_name(url)
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
    return Attachment(handle=handle, path=path, name=name, size=len(payload))


def _file_name(url: str) -> str:
    raw = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    cleaned = SAFE_NAME.sub("_", raw).strip("._") or "upload"
    guessed = mimetypes.guess_extension(mimetypes.guess_type(cleaned)[0] or "") or ""
    return cleaned if Path(cleaned).suffix else cleaned + guessed
