import json
import unittest

from openai_managed.task import SASHA_RESULT_JSON_SCHEMA, SashaResult, SashaTask


class SashaTaskTests(unittest.TestCase):
    def test_workflow_is_required_even_with_a_message(self):
        with self.assertRaises(TypeError):
            SashaTask("303839", "task-1", "https://qa.example/deal", "Hello")

    def test_rejects_unknown_workflows(self):
        with self.assertRaisesRegex(ValueError, "workflow must be"):
            SashaTask("303839", "task-1", "https://qa.example/deal", workflow="unknown")

    def test_client_response_requires_nonblank_message(self):
        for message in (None, "", "  "):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, "requires a client message"):
                SashaTask("303839", "task-1", "https://qa.example/deal", message, workflow="client-response-orchestrator")

    def test_supplied_message_does_not_change_outreach_selection(self):
        task = SashaTask("303839", "task-1", "https://qa.example/deal", "Context", workflow="outreach")
        self.assertEqual(task.workflow, "outreach")

    def test_outreach_has_no_client_message(self):
        task = SashaTask(
            "303839",
            "outreach-303839",
            "https://qa.example/deal?id=303839",
            workflow="outreach",
        )

        self.assertEqual(task.deal_id, "303839")
        self.assertIsNone(task.client_message)

    def test_client_response_keeps_supplied_message(self):
        task = SashaTask(
            "303839",
            "response-303839",
            "https://qa.example/deal?id=303839",
            "What's the price for 40?",
            workflow="client-response-orchestrator",
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
