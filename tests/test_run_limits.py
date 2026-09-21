import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

for name, value in {
    'FP_BASE_URL': 'https://qa.example', 'FP_LOGIN_URL': 'https://qa.example/login',
    'FP_USER': 'test', 'FP_PASSWORD': 'test', 'ANTHROPIC_API_KEY': 'test',
    'MODEL': 'test-model',
}.items():
    os.environ.setdefault(name, value)

import config
from brain import Brain, INCOMPLETE_REPLY
from llm import LLM, Reply, ToolCall, BROWSER_TOOLSET
from run_limits import RunLimits
from tool_policy import DISABLED_MEMBERS, validate_tool_call
from toolset_executor import PageStillProcessing


def call(name, args=None, identifier='call'):
    toolset = None if name in ('reply_to_client', 'attach_file', 'unknown_custom') else 'browser'
    return ToolCall(identifier, name, args or {}, toolset)


def response(*calls):
    return Reply(text='', raw_content=[], tool_calls=list(calls), input_tokens=10, output_tokens=2)


class FakeLLM(LLM):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.timeouts = []

    def send(self, system, messages, timeout_seconds=None):
        self.timeouts.append(timeout_seconds)
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


class FakeBrowser:
    def __init__(self):
        self.captures = 0
        self.last_capture = {'seconds': 0.0, 'reused': False}

    def save_screenshot(self, path):
        self.captures += 1
        path.write_bytes(b'fake screenshot')
        return path


class FakeExecutor:
    def __init__(self):
        self.calls = []
        self.last_timings = {'readiness_seconds': 0.0}
        self.attachments = None

    def run(self, name, args):
        self.calls.append((name, args))
        if name == 'left_click' and args.get('fail'):
            raise RuntimeError('failed click')
        if name == 'left_click' and args.get('pending'):
            raise PageStillProcessing('Save is still processing. Inspect before another action.')
        return 'Observed' if name == 'screenshot' else 'Executed'


class BrainLimitsTests(unittest.TestCase):
    def run_responses(self, responses, **settings):
        settings = {'MAX_BATCHES': 40, 'MAX_ACTIONS': 120, 'MAX_TURN_SECONDS': 600,
                    'MAX_VERIFICATION_ACTIONS': 8, 'VERIFICATION_SECONDS': 45,
                    'MAX_KEY_REPEAT': 20, **settings}
        browser = FakeBrowser()
        executor = FakeExecutor()
        llm = FakeLLM(responses)
        with tempfile.TemporaryDirectory() as directory:
            with patch.multiple(config, **settings), patch('brain.LLM', return_value=llm), \
                 patch('brain.ToolsetExecutor', return_value=executor), \
                 patch('brain.memory.load_transcript', return_value=''), \
                 patch('brain.memory.append_transcript'):
                result = Brain(browser, Path(directory)).run(1, 'Make the mockup')
            log = json.loads((Path(directory) / 'run.json').read_text())
        return result, executor, browser, llm, log

    def test_thirty_keys_stop_at_checkpoint_and_require_next_turn_observation(self):
        keys = [call('key', {'text': 'Right'}, str(i)) for i in range(30)]
        result, executor, _, _, _ = self.run_responses([
            response(*keys, call('reply_to_client', {'message': 'false success'})),
            response(call('screenshot'), call('key', {'text': 'right'})),
            response(call('key', {'text': 'right', 'repeat': 5}), call('screenshot')),
            response(call('reply_to_client', {'message': 'Checked result'})),
        ])
        keys_executed = [args for name, args in executor.calls if name == 'key']
        self.assertEqual(len(keys_executed), 4)
        self.assertEqual(keys_executed[-1]['repeat'], 5)
        self.assertEqual(result.reply, 'Checked result')
        self.assertEqual(sum(s.outcome == 'needs_observation' for s in result.steps), 2)

    def test_observation_before_movement_does_not_reset_later_movement(self):
        result, executor, _, _, _ = self.run_responses([
            response(call('screenshot'), *[call('key', {'text': 'right'}) for _ in range(3)]),
            response(call('key', {'text': 'right'})),
            response(call('reply_to_client', {'message': 'Incomplete'})),
        ])
        self.assertEqual(sum(name == 'key' for name, _ in executor.calls), 3)
        self.assertEqual(result.steps[-1].outcome, 'needs_observation')

    def test_action_limit_allows_verification_but_not_another_write(self):
        result, executor, _, _, _ = self.run_responses([
            response(call('left_click'), call('attach_file', {'file': 'file_1'})),
            response(call('get_page_text'), call('reply_to_client', {'message': 'Verified prior save'})),
        ], MAX_ACTIONS=1)
        self.assertEqual([name for name, _ in executor.calls], ['left_click', 'get_page_text'])
        self.assertEqual(result.stop_reason, 'action_limit')
        self.assertEqual(result.reply, 'Verified prior save')

    def test_failure_blocks_attachment_and_same_batch_success(self):
        result, executor, _, _, _ = self.run_responses([
            response(call('left_click', {'fail': True}), call('attach_file', {'file': 'file_1'}),
                     call('reply_to_client', {'message': 'incorrect success'})),
            response(call('reply_to_client', {'message': 'Unable to finish'})),
        ])
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(result.reply, 'Unable to finish')
        self.assertEqual(result.steps[1].outcome, 'not_executed')

    def test_final_reply_stops_later_actions_and_does_not_capture(self):
        result, executor, browser, _, _ = self.run_responses([
            response(call('reply_to_client', {'message': 'Done'}), call('left_click')),
        ])
        self.assertEqual(result.reply, 'Done')
        self.assertEqual(executor.calls, [])
        self.assertEqual(browser.captures, 0)

    def test_pending_save_stops_another_save_but_allows_later_inspection(self):
        result, executor, _, _, _ = self.run_responses([
            response(call('left_click', {'pending': True}), call('left_click'),
                     call('reply_to_client', {'message': 'incorrect success'})),
            response(call('get_page_text'), call('reply_to_client', {'message': 'Save is unfinished'})),
        ])
        self.assertEqual([name for name, _ in executor.calls], ['left_click', 'get_page_text'])
        self.assertEqual(result.steps[0].outcome, 'pending')
        self.assertEqual(result.reply, 'Save is unfinished')

    def test_disabled_wait_is_rejected_without_touching_browser(self):
        result, executor, browser, _, _ = self.run_responses([
            response(call('wait', {'duration': 1000})),
            response(call('reply_to_client', {'message': 'Stopped'})),
        ])
        self.assertEqual(executor.calls, [])
        self.assertEqual(browser.captures, 0)
        self.assertEqual(result.steps[0].outcome, 'rejected')

    def test_model_error_records_timings_and_honest_fallback(self):
        result, _, browser, llm, log = self.run_responses([TimeoutError('provider timeout')])
        self.assertEqual(result.reply, INCOMPLETE_REPLY)
        self.assertEqual(result.stop_reason, 'model_error')
        self.assertEqual(browser.captures, 0)
        self.assertGreater(llm.timeouts[0], 0)
        self.assertEqual(log['model_calls'][0]['error_type'], 'TimeoutError')
        self.assertIn('system_prompt_sha256', log['metadata'])
        self.assertNotIn('FP_PASSWORD', json.dumps(log['metadata']))

    def test_batch_limit_reserves_verification_turns(self):
        result, executor, _, _, _ = self.run_responses([
            response(call('get_page_text')),
            response(call('left_click')),
            response(call('reply_to_client', {'message': 'Incomplete'})),
        ], MAX_BATCHES=1)
        self.assertEqual([name for name, _ in executor.calls], ['get_page_text'])
        self.assertEqual(result.stop_reason, 'batch_limit')


class PolicyTests(unittest.TestCase):
    def test_schema_and_executor_share_disabled_policy(self):
        self.assertEqual(set(BROWSER_TOOLSET['configs']), set(DISABLED_MEMBERS))
        for name in DISABLED_MEMBERS:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_tool_call(name, {})

    def test_repeat_and_chord_sequence_are_bounded(self):
        for repeat in (0, -1, 100000, True, 1.5, '5'):
            with self.subTest(repeat=repeat), self.assertRaises(ValueError):
                validate_tool_call('key', {'text': 'right', 'repeat': repeat})
        with self.assertRaises(ValueError):
            validate_tool_call('key', {'text': ' '.join(['Right'] * 30)})
        validate_tool_call('key', {'text': 'Control+a Backspace'})

    def test_time_limit_starts_bounded_verification(self):
        with patch('run_limits.time.monotonic', return_value=100), \
             patch.object(config, 'MAX_TURN_SECONDS', 10), patch.object(config, 'VERIFICATION_SECONDS', 5):
            limits = RunLimits(started=80)
            self.assertTrue(limits.reserve_action('attach_file'))
            self.assertEqual(limits.reason, 'time_limit')
            self.assertEqual(limits.reserve_action('get_page_text'), '')
            with patch('run_limits.time.monotonic', return_value=106):
                self.assertTrue(limits.reserve_action('get_page_text'))
                self.assertEqual(limits.seconds_left(), 0)


class ModelTimeoutTests(unittest.TestCase):
    def test_provider_uses_remaining_time_without_automatic_retries(self):
        with patch('llm.Anthropic') as constructor:
            model = LLM()
        self.assertEqual(constructor.call_args.kwargs['max_retries'], 0)
        client = constructor.return_value
        client.with_options.return_value.messages.create.return_value = SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=2),
            stop_reason='end_turn', _request_id='request-test',
        )
        reply = model.send('system', [], timeout_seconds=0.5)
        client.with_options.assert_called_once_with(timeout=0.5)
        self.assertEqual(reply.request_id, 'request-test')

    def test_no_provider_call_after_deadline(self):
        with patch('llm.Anthropic') as constructor:
            model = LLM()
        with self.assertRaises(TimeoutError):
            model.send('system', [], timeout_seconds=0)
        constructor.return_value.with_options.assert_not_called()


if __name__ == '__main__':
    unittest.main()
