"""Host-side artwork extract broker authorization and idempotency."""

import json
import tempfile
import unittest
from pathlib import Path

from account_manager_agent.artwork.broker import ArtworkExtractBroker
from account_manager_agent.artwork.settings import ArtworkSettings


class ArtworkExtractBrokerTests(unittest.TestCase):
    def test_broker_reuses_response_for_same_action_id(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            calls = []

            def fake_extract(file_path, keep, shape, **kwargs):
                calls.append((file_path, keep, shape, kwargs["api_key"]))
                return {
                    "ok": True,
                    "source": str(workspace / "client-files" / "file_1.png"),
                    "path": str(workspace / "client-files" / "file_1_extracted.png"),
                    "artifact": str(workspace / "artifacts" / "file_1_extracted.png"),
                    "bytes": 12,
                }

            broker = ArtworkExtractBroker(
                workspace,
                ArtworkSettings(api_key="host-secret", model="gpt-image-1"),
                extract_fn=fake_extract,
            )
            broker.requests.mkdir(parents=True)
            broker.responses.mkdir()
            payload = {
                "action_id": "stable-extract",
                "file": "/workspace/client-files/file_1.png",
                "keep": "logo",
                "shape": "wide",
            }
            request_path = broker.requests / "stable-extract.json"
            request_path.write_text(json.dumps(payload), encoding="utf-8")
            broker._process(request_path)
            self.assertEqual(
                calls,
                [
                    (
                        str(workspace / "client-files" / "file_1.png"),
                        "logo",
                        "wide",
                        "host-secret",
                    )
                ],
            )

            request_path.write_text(json.dumps(payload), encoding="utf-8")
            broker._process(request_path)
            self.assertEqual(len(calls), 1)
            response = json.loads((broker.responses / "stable-extract.json").read_text())
            self.assertTrue(response["ok"])
            self.assertEqual(
                response["source"], "/workspace/client-files/file_1.png"
            )
            self.assertEqual(
                response["path"], "/workspace/client-files/file_1_extracted.png"
            )
            self.assertEqual(
                response["artifact"], "/workspace/artifacts/file_1_extracted.png"
            )
            self.assertIn("request_fingerprint", response)

    def test_missing_key_returns_structured_error(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "client-files").mkdir()
            source = workspace / "client-files" / "file_1.png"
            source.write_bytes(b"png")
            broker = ArtworkExtractBroker(
                workspace,
                ArtworkSettings(api_key=""),
            )
            result = broker._execute(
                {
                    "action_id": "a1",
                    "file": "/workspace/client-files/file_1.png",
                    "keep": "",
                    "shape": "auto",
                }
            )
            self.assertEqual(result["ok"], False)
            self.assertEqual(result["error"], "not_configured")

    def test_rejects_invalid_shape_before_calling_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            broker = ArtworkExtractBroker(
                Path(directory),
                ArtworkSettings(api_key="key"),
                extract_fn=lambda *args, **kwargs: calls.append(1) or {"ok": True},
            )
            result = broker._execute(
                {
                    "action_id": "a1",
                    "file": "/workspace/client-files/x.png",
                    "keep": "",
                    "shape": "huge",
                }
            )
            self.assertEqual(result["error"], "invalid_shape")
            self.assertEqual(calls, [])

    def test_rejects_paths_outside_container_client_files(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            broker = ArtworkExtractBroker(
                Path(directory),
                ArtworkSettings(api_key="key"),
                extract_fn=lambda *args, **kwargs: calls.append(1) or {"ok": True},
            )
            for file_path in (
                "/workspace/secret.png",
                "/workspace/client-files/../secret.png",
                "/tmp/file_1.png",
            ):
                with self.subTest(file_path=file_path):
                    result = broker._execute(
                        {
                            "action_id": "a1",
                            "file": file_path,
                            "keep": "",
                            "shape": "auto",
                        }
                    )
                    self.assertEqual(result["error"], "invalid_file")
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
