import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openai_managed.notes import (
    MAX_NOTES_BYTES,
    NOTES_OUTPUT_NAME,
    NotesStore,
    remove_notes_output,
    saved_attachment_urls,
    with_attachment_links,
)


class NotesStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.candidate = self.workspace / NOTES_OUTPUT_NAME
        self.store = NotesStore(self.root, "123")

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_notes_do_not_create_a_notebook(self):
        self.assertEqual(self.store.load(), "")
        self.assertFalse(self.store.path.exists())

    def test_updates_one_file_and_keeps_deals_separate(self):
        other = NotesStore(self.root, "456")
        self.candidate.write_text("Navy", encoding="utf-8")
        self.store.publish(self.workspace)
        self.candidate.write_text("Black", encoding="utf-8")
        self.store.publish(self.workspace)
        other.publish(self.workspace)
        self.assertEqual(self.store.load(), "Black")
        self.assertEqual(other.load(), "Black")
        self.candidate.write_text("Green", encoding="utf-8")
        other.publish(self.workspace)
        self.assertEqual(self.store.load(), "Black")
        self.assertEqual(other.load(), "Green")
        self.assertEqual(set(self.store.path.parent.iterdir()), {self.store.path, other.path})

    def test_rejects_invalid_content_without_replacing_saved_notes(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text("Original", encoding="utf-8")
        for invalid in (b"", b" \n", b"\xff", b"text\x00", b"x" * (MAX_NOTES_BYTES + 1)):
            with self.subTest(content=invalid[:10]):
                self.candidate.write_bytes(invalid)
                with self.assertRaises(ValueError):
                    self.store.publish(self.workspace)
                self.assertEqual(self.store.load(), "Original")

    def test_limit_counts_utf8_bytes(self):
        self.candidate.write_text("é" * (MAX_NOTES_BYTES // 2), encoding="utf-8")
        self.store.publish(self.workspace)
        self.assertEqual(self.store.path.stat().st_size, MAX_NOTES_BYTES)
        self.candidate.write_text("é" * (MAX_NOTES_BYTES // 2 + 1), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.publish(self.workspace)

    def test_rejects_symlink_and_cleanup_keeps_its_target(self):
        outside = self.root / "outside.md"
        outside.write_text("External notes", encoding="utf-8")
        self.candidate.symlink_to(outside)
        with self.assertRaises(OSError):
            self.store.publish(self.workspace)
        remove_notes_output(self.workspace)
        self.assertEqual(outside.read_text(), "External notes")
        self.assertFalse(self.candidate.is_symlink())

    def test_rejects_directory_and_removes_invalid_output(self):
        self.candidate.mkdir()
        (self.candidate / "accidental.txt").write_text("Not a notebook")
        with self.assertRaises(ValueError):
            self.store.publish(self.workspace)
        remove_notes_output(self.workspace)
        self.assertFalse(self.candidate.exists())

    def test_rejects_fifo_without_blocking(self):
        os.mkfifo(self.candidate)
        with self.assertRaises(ValueError):
            self.store.publish(self.workspace)
        remove_notes_output(self.workspace)
        self.assertFalse(self.candidate.exists())

    def test_failed_atomic_replace_retains_notes_and_removes_staging_file(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text("Original", encoding="utf-8")
        self.candidate.write_text("Replacement", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=OSError("Disk error")):
            with self.assertRaises(OSError):
                self.store.publish(self.workspace)
        self.assertEqual(self.store.load(), "Original")
        self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])

    def test_unreadable_saved_notes_are_not_treated_as_missing(self):
        self.store.path.parent.mkdir()
        self.store.path.write_bytes(b"\xff")
        with self.assertRaises(ValueError):
            self.store.load()

    def test_rejects_unsafe_deal_ids(self):
        with self.assertRaises(ValueError):
            NotesStore(self.root, "../123")

    def test_preserves_exact_links_when_agent_omits_or_invents_attachments(self):
        url = "https://example.test/art(1).svg?signature=abc%2Fdef&expires=123"
        self.candidate.write_text(
            "## Current decisions\n- White shirt.\n\n## Attachments\n"
            "- wrong.svg: <https://example.test/wrong.svg>\n",
            encoding="utf-8",
        )
        self.store.publish(self.workspace, attachment_urls=[url, url])
        self.assertEqual(saved_attachment_urls(self.store.load()), [url])
        self.assertNotIn("wrong.svg", self.store.load())
        self.candidate.write_text("## Current decisions\n- Black shirt.", encoding="utf-8")
        self.store.publish(self.workspace, attachment_urls=[url])
        self.assertEqual(saved_attachment_urls(self.store.load()), [url])
        self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])

    def test_ignores_links_outside_attachment_section(self):
        notes = (
            "## Current decisions\n- proof.svg: <https://example.test/not-artwork.svg>\n"
            "## Attachments\n- logo.svg: <https://example.test/logo.svg>\n"
            "## Needs verification\n- page.svg: <https://example.test/another.svg>\n"
        )
        self.assertEqual(saved_attachment_urls(notes), ["https://example.test/logo.svg"])

    def test_combined_notes_and_attachment_links_stay_within_size_limit(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text("Original", encoding="utf-8")
        self.candidate.write_text("x" * MAX_NOTES_BYTES, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.publish(self.workspace, attachment_urls=["https://example.test/logo.svg"])
        self.assertEqual(self.store.load(), "Original")

    def test_attachment_section_does_not_delete_other_sections(self):
        notes = (
            "## Attachments\n- old.svg: <https://example.test/old.svg>\n"
            "## Unfinished work\n- Await quantity.\n"
        )
        updated = with_attachment_links(notes, ["https://example.test/new.svg"])
        self.assertIn("- Await quantity.", updated)
        self.assertNotIn("old.svg", updated)
        self.assertEqual(updated.count("## Attachments"), 1)
