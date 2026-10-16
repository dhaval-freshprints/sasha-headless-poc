"""Sandbox artwork client IPC against a fake host response."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from account_manager_agent.artwork import client as artwork_client
from account_manager_agent.artwork.constants import BROKER_DIR_NAME
from account_manager_agent import extract_artwork


class ExtractArtworkToolTests(unittest.TestCase):
    def test_request_broker_uses_existing_idempotent_response(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            requests = workspace / BROKER_DIR_NAME / "requests"
            responses = workspace / BROKER_DIR_NAME / "responses"
            requests.mkdir(parents=True)
            responses.mkdir()
            payload = {
                "action_id": "same",
                "file": "/workspace/client-files/file_1.png",
                "keep": "text",
                "shape": "wide",
            }
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            (responses / "same.json").write_text(
                json.dumps({"ok": True, "path": "/out.png", "request_fingerprint": fingerprint}),
                encoding="utf-8",
            )
            result = artwork_client.request_broker(payload, workspace=workspace)
            self.assertEqual(result["path"], "/out.png")
            self.assertFalse(any(requests.iterdir()))

    def test_broker_unavailable_when_dirs_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(artwork_client.ArtworkClientError) as raised:
                artwork_client.request_broker(
                    {"action_id": "x", "file": "a", "keep": "", "shape": "auto"},
                    workspace=Path(directory),
                    timeout_seconds=0.1,
                )
            self.assertEqual(raised.exception.code, "broker_unavailable")

    def test_main_prints_json(self):
        with patch.object(
            extract_artwork,
            "request_broker",
            return_value={"ok": True, "path": "/workspace/client-files/file_1_extracted.png"},
        ) as request, patch(
            "sys.argv",
            [
                "extract_artwork.py",
                "/workspace/client-files/file_1.png",
                "--keep",
                "logo",
                "--shape",
                "wide",
                "--action-id",
                "act-1",
            ],
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            extract_artwork.main()
        self.assertIn("file_1_extracted.png", stdout.getvalue())
        request.assert_called_once()
        payload = request.call_args.args[0]
        self.assertEqual(payload["action_id"], "act-1")
        self.assertEqual(payload["shape"], "wide")
        self.assertEqual(payload["keep"], "logo")


if __name__ == "__main__":
    unittest.main()
