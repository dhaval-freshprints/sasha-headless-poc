"""One persistent Markdown notebook per deal."""

import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .conversation import DEAL_ID
from .attachments import file_name


MAX_NOTES_BYTES = 16 * 1024
NOTES_OUTPUT_NAME = "SASHANOTES01_notes_update.md"
ATTACHMENTS_HEADING = "## Attachments"
ATTACHMENT_LINE = re.compile(r"^- [A-Za-z0-9._-]+: <(https?://\S+)>$")


class NotesStore:
    def __init__(self, runs_directory: Path, deal_id: str) -> None:
        _validate_deal_id(deal_id)
        self.path = runs_directory / "notes" / f"SASHANOTES01_deal_{deal_id}_notes.md"

    def load(self) -> str:
        try:
            return read_notes(self.path)
        except FileNotFoundError:
            return ""

    def publish(self, workspace: Path, attachment_urls: list[str] | None = None) -> None:
        notes = read_notes(workspace / NOTES_OUTPUT_NAME)
        if attachment_urls is not None:
            notes = with_attachment_links(notes, attachment_urls)
            _validate_notes(notes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(notes, encoding="utf-8")
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


def read_notes(path: Path) -> str:
    # Reject links and avoid blocking if an agent accidentally creates a FIFO.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Notes must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as notes_file:
            content = notes_file.read(MAX_NOTES_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(content) > MAX_NOTES_BYTES:
        raise ValueError("Notes exceed the 16 KiB limit")
    notes = content.decode("utf-8")
    _validate_notes(notes)
    return notes


def saved_attachment_urls(notes: str) -> list[str]:
    urls = []
    in_attachments = False
    for line in notes.splitlines():
        if line.startswith("## "):
            in_attachments = line.strip() == ATTACHMENTS_HEADING
        if in_attachments:
            match = ATTACHMENT_LINE.fullmatch(line)
            if match and match.group(1) not in urls:
                urls.append(match.group(1))
    return urls


def with_attachment_links(notes: str, urls: list[str]) -> str:
    lines = []
    in_attachments = False
    for line in notes.splitlines():
        if line.startswith("## "):
            in_attachments = line.strip() == ATTACHMENTS_HEADING
        if not in_attachments:
            lines.append(line)
    if not urls and ATTACHMENTS_HEADING not in notes:
        return notes
    content = "\n".join(lines).rstrip()
    if not urls:
        return content + "\n"
    links = []
    for url in urls:
        line = f"- {file_name(url)}: <{url}>"
        if line not in links:
            links.append(line)
    return content + "\n\n" + ATTACHMENTS_HEADING + "\n" + "\n".join(links) + "\n"


def _validate_notes(notes: str) -> None:
    if len(notes.encode("utf-8")) > MAX_NOTES_BYTES:
        raise ValueError("Notes exceed the 16 KiB limit")
    if not notes.strip() or "\x00" in notes:
        raise ValueError("Notes must contain nonempty text without null bytes")


def remove_notes_output(workspace: Path) -> None:
    path = workspace / NOTES_OUTPUT_NAME
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _validate_deal_id(deal_id: str) -> None:
    if not DEAL_ID.fullmatch(deal_id):
        raise ValueError("deal_id must contain only digits")
