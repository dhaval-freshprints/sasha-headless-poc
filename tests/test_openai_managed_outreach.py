import json
import unittest

from openai_managed.outreach import OutreachResult, OutreachTask


class OutreachValueTests(unittest.TestCase):
    def test_task_keeps_supplied_values(self):
        task = OutreachTask("303839", "outreach-303839", "https://qa.example/deal?id=303839")
        self.assertEqual(task.deal_id, "303839")

    def test_result_round_trips_json_without_message_validation(self):
        value = {
            "deal_id": "303839",
            "status": "completed",
            "message_html": "Any model-generated HTML",
            "failure_code": "",
            "failure_message": "",
        }
        result = OutreachResult.from_json(json.dumps(value))
        self.assertEqual(result.to_dict(), value)


if __name__ == "__main__":
    unittest.main()
