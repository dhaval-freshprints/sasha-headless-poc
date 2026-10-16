"""Artwork extraction: only supplied client files, a transparent PNG written next to them."""
import base64
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from account_manager_agent import extract_artwork
from account_manager_agent.extract_artwork import ExtractionError, extract

from PIL import Image


def png(*pixels):
    picture = Image.new("RGB", (len(pixels), 1))
    picture.putdata(list(pixels))
    output = io.BytesIO()
    picture.save(output, "PNG")
    return output.getvalue()


GREEN, WHITE, RED = (0, 255, 0), (255, 255, 255), (200, 20, 20)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ExtractArtworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "client-files"
        self.root.mkdir()
        self.source = self.root / "file_1.jpg"
        self.source.write_bytes(b"jpeg bytes")
        self.requests = []
        environment = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "ARTWORK_IMAGE_MODEL": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def opener(self, request, timeout, image=None):
        self.requests.append(request)
        image = image or png(GREEN, WHITE, RED, GREEN)
        return Response(json.dumps({"data": [{"b64_json": base64.b64encode(image).decode()}]}).encode())

    def test_writes_a_transparent_png_next_to_the_original(self):
        artifacts = self.root.parent / "artifacts"
        with patch.object(extract_artwork, "ARTIFACTS", artifacts):
            result = extract(
                str(self.source),
                '"Pipe down" at the top, a dolphin',
                "wide",
                self.opener,
                self.root,
            )
        self.assertEqual(result["path"], str((self.root / "file_1_extracted.png").resolve()))
        # The green background becomes transparent and is trimmed; white and red stay.
        picture = Image.open(result["path"])
        self.assertEqual((picture.mode, picture.size), ("RGBA", (2, 1)))
        self.assertEqual(list(picture.get_flattened_data()), [(*WHITE, 255), (*RED, 255)])
        self.assertEqual(self.source.read_bytes(), b"jpeg bytes")
        self.assertEqual(result["artifact"], str(artifacts / "file_1_extracted.png"))
        self.assertTrue((artifacts / "file_1_extracted.png").is_file())
        request = self.requests[0]
        self.assertEqual(request.full_url, extract_artwork.API_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        body = request.data.decode("latin-1")
        for expected in ('name="model"\r\n\r\ngpt-image-1', 'name="background"\r\n\r\nopaque',
                         'name="size"\r\n\r\n1536x1024', "solid pure green (#00FF00)",
                         'Recreate only these elements, and leave out everything else: "Pipe down" at the top, a dolphin',
                         'filename="file_1.jpg"\r\nContent-Type: image/jpeg', "jpeg bytes"):
            self.assertIn(expected, body)

    def test_never_overwrites_an_earlier_extraction(self):
        extract(str(self.source), "", opener=self.opener, root=self.root)
        second = extract(str(self.source), "", opener=self.opener, root=self.root)
        self.assertTrue(second["path"].endswith("file_1_extracted_2.png"))

    def test_only_supplied_images_are_accepted(self):
        outside = self.root.parent / "secret.png"
        outside.write_bytes(b"x")
        (self.root / "notes.txt").write_text("x")
        for path, code in ((outside, "invalid_file"), (self.root / "missing.png", "invalid_file"),
                           (self.root / "notes.txt", "unsupported_type")):
            with self.subTest(path=path.name), self.assertRaises(ExtractionError) as raised:
                extract(str(path), "", opener=self.opener, root=self.root)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.requests, [])

    def test_missing_key_and_api_errors_are_reported(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), self.assertRaises(ExtractionError) as raised:
            extract(str(self.source), "", opener=self.opener, root=self.root)
        self.assertEqual(raised.exception.code, "not_configured")

        def failing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, io.BytesIO(b"bad image"))

        with self.assertRaises(ExtractionError) as raised:
            extract(str(self.source), "", opener=failing, root=self.root)
        self.assertEqual(raised.exception.code, "api_error")
        self.assertIn("bad image", str(raised.exception))
        self.assertFalse((self.root / "file_1_extracted.png").exists())

    def test_an_all_green_result_is_an_error(self):
        with self.assertRaises(ExtractionError) as raised:
            extract(str(self.source), "", opener=lambda request, timeout: self.opener(request, timeout, png(GREEN, GREEN)),
                    root=self.root)
        self.assertEqual(raised.exception.code, "empty_result")


if __name__ == "__main__":
    unittest.main()
