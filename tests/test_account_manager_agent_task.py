import json
import unittest

from account_manager_agent.task import SASHA_RESULT_JSON_SCHEMA, SashaResult, SashaTask


class SashaTaskTests(unittest.TestCase):
    def test_followup_validation_also_applies_outside_the_web_api(self):
        valid = {"turn_type": "follow_up", "as_of_date": "2026-10-13"}
        task = SashaTask("303839", "followup", "https://qa.example/deal", **valid)
        self.assertEqual(task.as_of_date, "2026-10-13")
        for changed in ({"turn_type": "response"}, {"turn_type": None}, {"as_of_date": None},
                        {"as_of_date": "not-a-date"},
                        {"days_since_client_reply": -1}, {"client_message": "A request"},
                        {"file_urls": ("https://example.com/art.png",)}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                SashaTask("303839", "followup", "https://qa.example/deal", **{**valid, **changed})

    def test_rejects_removed_follow_up_stage_argument(self):
        with self.assertRaises(TypeError):
            SashaTask("303839", "followup", "https://qa.example/deal", turn_type="follow_up",
                      follow_up_stage=6, as_of_date="2026-10-13")

    def test_rejects_removed_workflow_argument(self):
        with self.assertRaises(TypeError):
            SashaTask("303839", "task-1", "https://qa.example/deal", workflow="outreach")

    def test_blank_messages_are_normalized(self):
        for message in (None, "", "  ", "\n\t"):
            with self.subTest(message=message):
                task = SashaTask("303839", "task-1", "https://qa.example/deal", message)
                self.assertIsNone(task.client_message)

    def test_preserves_nonblank_message_content(self):
        task = SashaTask("303839", "task-1", "https://qa.example/deal", "  Hello\n")
        self.assertEqual(task.client_message, "  Hello\n")

    def test_outreach_has_no_client_message(self):
        task = SashaTask(
            "303839",
            "outreach-303839",
            "https://qa.example/deal?id=303839",
        )

        self.assertEqual(task.deal_id, "303839")
        self.assertIsNone(task.client_message)

    def test_client_response_keeps_supplied_message(self):
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "What's the price for 40?",
        )

        self.assertEqual(task.client_message, "What's the price for 40?")


class SashaResultTests(unittest.TestCase):
    def test_result_round_trips_json_without_message_validation(self):
        value = {
            "deal_id": "303839",
            "status": "completed",
            "message_html": "Any model-generated HTML",
            "sms_messages": [],
            "communication_channel": "email",
            "failure_code": "",
            "failure_message": "",
            "notes": "Saved notes",
            "convo_type": "outreach",
            "handoff_reason": "",
            "isa_decision": "",
            "sample_request": None,
            "sample_update": None,
            "deal_state": None,
        }

        result = SashaResult.from_json(json.dumps(value))

        self.assertEqual(result.to_dict(), value)

    def test_outreach_and_client_response_use_the_same_result_shape(self):
        outreach_result = SashaResult(
            deal_id="303839",
            status="completed",
            message_html="<p>Initial outreach</p>",
        )
        client_response_result = SashaResult(
            deal_id="303839",
            status="completed",
            message_html="<p>The price is shown here.</p>",
        )

        expected_fields = set(SASHA_RESULT_JSON_SCHEMA["required"])
        self.assertEqual(set(outreach_result.to_dict()), expected_fields)
        self.assertEqual(set(client_response_result.to_dict()), expected_fields)


if __name__ == "__main__":
    unittest.main()
