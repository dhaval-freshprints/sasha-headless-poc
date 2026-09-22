import json
import unittest

from openai_managed.task import SASHA_RESULT_JSON_SCHEMA, SashaResult, SashaTask


class SashaTaskTests(unittest.TestCase):
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
            "failure_code": "",
            "failure_message": "",
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
