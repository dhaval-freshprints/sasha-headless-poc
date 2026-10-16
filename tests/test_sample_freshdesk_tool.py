"""CLI wrapper for agentic sample Freshdesk operations."""
import json
import hashlib
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from account_manager_agent import sample_freshdesk_tool


class SampleFreshdeskToolTests(unittest.TestCase):
    def test_request_broker_uses_existing_idempotent_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requests").mkdir()
            (root / "responses").mkdir()
            payload = {
                "action_id": "same-action",
                "action": "show",
                "sample_id": 1,
            }
            fingerprint = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            (root / "responses" / "same-action.json").write_text(
                json.dumps({"ok": True, "ticket_id": 9, "request_fingerprint": fingerprint}),
                encoding="utf-8",
            )
            with patch.object(sample_freshdesk_tool, "BROKER_ROOT", root):
                result = sample_freshdesk_tool.request_broker(payload)
            self.assertEqual(result["ticket_id"], 9)
            self.assertFalse((root / "requests" / "same-action.json").exists())

    def test_main_prints_json(self):
        with patch.object(sample_freshdesk_tool, "request_broker", return_value={"ok": True}) as request, \
                patch("sys.argv", ["sample_freshdesk.py", "sync", "2"]), \
                patch("sys.stdout", new_callable=StringIO) as stdout:
            sample_freshdesk_tool.main()
            self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})
            payload = request.call_args.args[0]
            self.assertEqual((payload["action"], payload["sample_id"]), ("sync", 2))


if __name__ == "__main__":
    unittest.main()
