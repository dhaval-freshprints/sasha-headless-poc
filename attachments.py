"""
Client files, fetched once per turn and handed to Sasha as opaque handles.

The upstream workflow puts a client's file on S3 and gives us a link. Sasha never sees the
link and never fetches anything herself: a URL in a client message is text the client
controls, so letting the browser follow it would make her a proxy for whatever is behind it.

Instead the link is fetched here, before the turn starts, into runs/deal_<id>/files/, and
Sasha is told only "file_1 (kotlin_icon.png, 12 KB)". `attach_file` resolves that handle
back to a path. A handle she was not given does not resolve.
"""

import mimetypes
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import config

MAX_BYTES = 20 * 1024 * 1024          # the wizard's stated limit for a reference image
FETCH_TIMEOUT = 30

# What the Design Tool and the wizard accept. Anything else is refused before it reaches disk.
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".ai", ".eps"}

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class Attachment:
    handle: str        # what Sasha calls it: file_1
    path: Path         # where it actually is on disk
    name: str          # the original file name, for the description she writes
    size: int          # bytes

    def describe(self) -> str:
        return f"{self.handle} ({self.name}, {self.size // 1024} KB)"


class AttachmentSet:
    """The files for one turn. Empty unless the caller passed links."""

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


def fetch_all(deal_id: int, urls: list[str]) -> AttachmentSet:
    """Download each link into the deal's folder. Raises if one cannot be used."""
    if not urls:
        return AttachmentSet()
    target_dir = config.RUNS_DIR / f"deal_{deal_id}" / "files"
    target_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for index, url in enumerate(urls, start=1):
        items.append(_fetch_one(url, f"file_{index}", target_dir))
    return AttachmentSet(items)


def _fetch_one(url: str, handle: str, target_dir: Path) -> Attachment:
    name = _file_name(url)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"{handle}: {suffix or 'no extension'} is not a file type we accept.")

    request = urllib.request.Request(url, headers={"User-Agent": "sasha-poc"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError(f"{handle}: larger than {MAX_BYTES // 1024 // 1024} MB.")
    if not payload:
        raise ValueError(f"{handle}: the link returned nothing.")

    path = target_dir / f"{handle}{suffix}"
    path.write_bytes(payload)
    return Attachment(handle=handle, path=path, name=name, size=len(payload))


def _file_name(url: str) -> str:
    """The file name from the URL path, ignoring any query string. Never used as a path itself."""
    raw = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    cleaned = SAFE_NAME.sub("_", raw).strip("._") or "upload"
    guessed = mimetypes.guess_extension(mimetypes.guess_type(cleaned)[0] or "") or ""
    return cleaned if Path(cleaned).suffix else cleaned + guessed
