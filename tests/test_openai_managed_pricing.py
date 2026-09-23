import unittest
from unittest.mock import patch

from openai_managed.pricing import CostReporter, estimate_cost


SOL_PRICES = {
    "OPENAI_AGENT_INPUT_USD_PER_MILLION": "2.00",
    "OPENAI_AGENT_CACHED_INPUT_USD_PER_MILLION": "0.20",
    "OPENAI_AGENT_OUTPUT_USD_PER_MILLION": "10.00",
}
ASTRA_PRICES = {
    "OPENAI_AGENT_INPUT_USD_PER_MILLION": "10.00",
    "OPENAI_AGENT_CACHED_INPUT_USD_PER_MILLION": "1.00",
    "OPENAI_AGENT_OUTPUT_USD_PER_MILLION": "50.00",
}


class CostEstimateTests(unittest.TestCase):
    @patch.dict("os.environ", ASTRA_PRICES)
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

    @patch.dict("os.environ", ASTRA_PRICES)
    def test_reports_unavailable_when_usage_is_missing(self):
        messages = []
        estimate = estimate_cost("gpt-6-astra", None)

        CostReporter(messages.append).report(estimate)

        self.assertEqual(estimate.status, "unavailable")
        self.assertIn("Estimated OpenAI cost: unavailable", "\n".join(messages))

    @patch.dict("os.environ", SOL_PRICES)
    def test_estimates_sol_standard_token_cost(self):
        estimate = estimate_cost(
            "gpt-6-sol",
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
        self.assertEqual(estimate.estimated_cost_usd, 0.00328)
        self.assertEqual(
            estimate.rates_usd_per_million,
            {"input": "2.00", "cached_input": "0.20", "output": "10.00"},
        )

    @patch.dict("os.environ", {}, clear=True)
    def test_reports_unavailable_when_prices_are_not_configured(self):
        estimate = estimate_cost(
            "gpt-6-sol",
            {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 100,
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        )

        self.assertEqual(estimate.status, "unavailable")
        self.assertIn("OPENAI_AGENT_INPUT_USD_PER_MILLION", estimate.note)


if __name__ == "__main__":
    unittest.main()
