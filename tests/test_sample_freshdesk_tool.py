"""CLI wrapper for agentic sample Freshdesk operations."""
import json
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from account_manager_agent import sample_freshdesk_tool


class SampleFreshdeskToolTests(unittest.TestCase):
    def test_show_uses_deal_from_context(self):
        with patch.object(sample_freshdesk_tool, "CONTEXT_PATH", Path("/tmp/context.json")), \
                patch("account_manager_agent.sample_freshdesk_tool.SampleService") as service_type:
            sample_freshdesk_tool.CONTEXT_PATH.write_text('{"deal_id": "397003"}', encoding="utf-8")
            service_type.return_value.freshdesk_show.return_value = {"ok": True, "ticket_id": 9}
            result = sample_freshdesk_tool.run_command("show", 1, "397003", None, False, None, None, None)
            service_type.return_value.freshdesk_show.assert_called_once_with("397003", 1)
            self.assertEqual(result["ticket_id"], 9)
            sample_freshdesk_tool.CONTEXT_PATH.unlink(missing_ok=True)

    def test_main_prints_json(self):
        with patch.object(sample_freshdesk_tool, "deal_id_from_context", return_value="42"), \
                patch.object(sample_freshdesk_tool, "run_command", return_value={"ok": True}), \
                patch("sys.argv", ["sample_freshdesk.py", "sync", "2"]), \
                patch("sys.stdout", new_callable=StringIO) as stdout:
            sample_freshdesk_tool.main()
            self.assertEqual(json.loads(stdout.getvalue()), {"ok": True})


if __name__ == "__main__":
    unittest.main()
