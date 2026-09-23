import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from attachments import fetch_to_directory


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str):
        super().__init__(payload)
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class AttachmentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.target = Path(self.temporary_directory.name) / "client-files"

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("attachments.urllib.request.urlopen")
    def test_downloads_direct_artwork_with_signed_query(self, urlopen):
        urlopen.return_value = FakeResponse(b"<svg></svg>", "image/svg+xml")

        files = fetch_to_directory(
            ["https://example.test/logo.svg?signature=secret"], self.target
        )

        self.assertEqual(files.items[0].handle, "file_1")
        self.assertEqual(files.items[0].path.name, "file_1.svg")
        self.assertEqual(files.items[0].path.read_bytes(), b"<svg></svg>")

    @patch("attachments.urllib.request.urlopen")
    def test_downloads_multiple_files_with_separate_handles(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(b"png", "image/png"),
            FakeResponse(b"pdf", "application/pdf"),
        ]

        files = fetch_to_directory(
            ["https://example.test/logo.png", "https://example.test/brief.pdf"],
            self.target,
        )

        self.assertEqual([item.path.name for item in files.items], ["file_1.png", "file_2.pdf"])

    @patch("attachments.urllib.request.urlopen")
    def test_rejects_empty_file(self, urlopen):
        urlopen.return_value = FakeResponse(b"", "image/png")

        with self.assertRaisesRegex(ValueError, "returned nothing"):
            fetch_to_directory(["https://example.test/logo.png"], self.target)

    @patch("attachments.urllib.request.urlopen")
    def test_rejects_file_over_20_mb(self, urlopen):
        urlopen.return_value = FakeResponse(b"x" * (20 * 1024 * 1024 + 1), "image/png")

        with self.assertRaisesRegex(ValueError, "larger than 20 MB"):
            fetch_to_directory(["https://example.test/logo.png"], self.target)

    @patch("attachments.urllib.request.urlopen")
    def test_rejects_web_page_disguised_as_svg(self, urlopen):
        urlopen.return_value = FakeResponse(b"<!DOCTYPE html><html></html>", "text/html")

        with self.assertRaisesRegex(ValueError, "web page, not a direct artwork file"):
            fetch_to_directory(["https://example.test/wiki/File:logo.svg"], self.target)

        self.assertEqual(list(self.target.iterdir()), [])

    @patch("attachments.urllib.request.urlopen")
    def test_download_error_does_not_repeat_signed_url(self, urlopen):
        url = "https://example.test/logo.png?signature=secret"
        urlopen.side_effect = OSError(url)

        with self.assertRaises(ValueError) as failure:
            fetch_to_directory([url], self.target)

        self.assertNotIn("signature", str(failure.exception))

    def test_rejects_non_http_url(self):
        with self.assertRaisesRegex(ValueError, "HTTP or HTTPS"):
            fetch_to_directory(["file:///tmp/logo.png"], self.target)

    def test_rejects_unsupported_extension(self):
        with self.assertRaisesRegex(ValueError, "not a file type we accept"):
            fetch_to_directory(["https://example.test/page.txt"], self.target)


if __name__ == "__main__":
    unittest.main()
