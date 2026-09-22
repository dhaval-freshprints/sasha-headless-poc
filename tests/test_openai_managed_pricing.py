import unittest

from openai_managed.pricing import CostReporter, estimate_cost


class CostEstimateTests(unittest.TestCase):
    def test_estimates_astra_standard_token_cost(self):
        estimate = estimate_cost(
            "gpt-6-astra",
            {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 400},
                "output_tokens": 200,
                "output_tokens_details": {"reasoning_tokens": 50},
            },
        )

        self.assertEqual(estimate.status, "estimated")
        self.assertEqual(estimate.cached_input_tokens, 400)
        self.assertEqual(estimate.reasoning_tokens, 50)
        self.assertEqual(estimate.estimated_cost_usd, 0.0164)

    def test_reports_unavailable_when_usage_is_missing(self):
        messages = []
        estimate = estimate_cost("gpt-6-astra", None)

        CostReporter(messages.append).report(estimate)

        self.assertEqual(estimate.status, "unavailable")
        self.assertIn("Estimated OpenAI cost: unavailable", "\n".join(messages))

    def test_reports_unavailable_for_unconfigured_model(self):
        estimate = estimate_cost(
            "another-model",
            {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        )

        self.assertEqual(estimate.status, "unavailable")
        self.assertIn("No pricing is configured", estimate.note)


if __name__ == "__main__":
    unittest.main()
