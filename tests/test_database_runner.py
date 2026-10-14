"""Persistence boundaries around real runner orchestration, using a fake agent."""
from dataclasses import replace
import json
from unittest.mock import patch
import unittest

from tests import test_openai_managed_runner as fixtures
from openai_managed.task import SashaTask
from openai_managed.notes import follow_up_entries, saved_attachment_urls
from sasha_database.connection import DatabaseError
from sasha_database.messages import IncomingMessage


class RunnerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.runner, self.sessions, self.sandboxes = self.fixture._make_runner([])
        self.database = self.fixture.database
        self.task = SashaTask('303839', 'task', fixtures.TASK_URL, 'A client question')

    def output(self, **changes):
        item = self.sessions.items._final_item()
        value = json.loads(item['content'][0]['text'])
        value.update(changes)
        item['content'][0]['text'] = json.dumps(value)
        return patch.object(self.sessions.items, '_final_item', return_value=item)

    def test_inbound_commits_before_agent_and_is_supplied_only_once(self):
        original = self.runner._run_turn
        def run(task):
            self.assertEqual(self.database.history[task.deal_id][0]['content'], 'A client question')
            self.assertEqual(task.conversation_history, ())
            return original(task)
        with patch.object(self.runner, '_run_turn', side_effect=run):
            result = self.runner.run(self.task)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(self.sandboxes[0].task_message.count('A client question'), 1)
        self.assertEqual(len(self.database.history['303839']), 2)
        self.assertFalse(list(self.fixture.root.rglob('*notes*.md')))
        self.assertFalse(list(self.fixture.root.rglob('*conversation*.json')))

    def test_prepared_web_messages_are_not_reinserted(self):
        messages = self.database.prepare_run('303839', self.task.run_id, (IncomingMessage('Already stored'),))
        result = self.runner.run(replace(self.task, incoming_messages=messages))
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(self.database.history['303839']), 2)

    def test_read_and_prepare_failures_do_not_start_agent(self):
        for method in ('prepare_run', 'load_context'):
            with self.subTest(method=method), patch.object(self.database, method, side_effect=DatabaseError('Unavailable')):
                result = self.runner.run(self.task)
                self.assertEqual(result.failure_code, 'persistence_error')
                self.assertEqual(self.sandboxes, [])

    def test_commit_failure_marks_artifact_failed_and_keeps_input(self):
        self.database.notes['303839'] = 'Previous'
        with patch.object(self.database, 'complete_run', side_effect=DatabaseError('Write failed')):
            result = self.runner.run(self.task)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(self.database.notes['303839'], 'Previous')
        self.assertEqual([row['role'] for row in self.database.history['303839']], ['client'])
        artifact = self.runner.last_run_directory / 'workspace' / 'result.json'
        self.assertEqual(json.loads(artifact.read_text())['status'], 'failed')

    def test_failed_agent_keeps_input_without_replacing_notes(self):
        self.database.notes['303839'] = 'Previous'
        with self.output(status='failed', failure_code='blocked', message_html='', notes='Incomplete'):
            result = self.runner.run(self.task)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(self.database.notes['303839'], 'Previous')
        self.assertEqual(len(self.database.history['303839']), 1)

    def test_invalid_notes_and_conversation_type_fail_without_draft(self):
        for change in ({'notes': ''}, {'notes': '\x00'}, {'notes': 'é' * 8193}, {'convo_type': 'invented'}):
            with self.subTest(change=str(change)[:40]), self.output(**change):
                result = self.runner.run(replace(self.task, client_message=None))
                self.assertEqual(result.status, 'failed')
                self.assertEqual(self.database.completed, [])

    def test_no_reply_updates_notes_without_consuming_followup_tactic(self):
        self.database.notes['303839'] = '## Current decisions\n- Follow-up generated: offer_art_team\n'
        with self.output(message_html='', convo_type=None,
                         notes='## Current decisions\n- Follow-up generated: close_the_loop'):
            result = self.runner.run(self.task)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(follow_up_entries(self.database.notes['303839']), ['- Follow-up generated: offer_art_team'])
        self.assertEqual(len(self.database.history['303839']), 1)

    def test_followup_draft_preserves_and_adds_one_tactic(self):
        self.database.notes['303839'] = '## Current decisions\n- Follow-up generated: offer_art_team\n'
        with self.output(convo_type='followup', notes='## Current decisions\n- Follow-up generated: close_the_loop'):
            result = self.runner.run(replace(self.task, client_message=None, turn_type='follow_up',
                                           follow_up_stage=1, as_of_date='2026-10-14'))
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(follow_up_entries(result.notes)), 2)

    def test_other_deal_notes_and_legacy_files_are_not_used(self):
        self.database.notes['999'] = 'Other deal secret'
        legacy = self.fixture.root / 'runs' / 'notes'
        legacy.mkdir(parents=True)
        (legacy / 'deal_303839_notes.md').write_text('Dead legacy notes')
        result = self.runner.run(self.task)
        self.assertEqual(result.status, 'completed')
        self.assertNotIn('Other deal secret', self.sandboxes[0].task_message)
        self.assertNotIn('Dead legacy notes', self.sandboxes[0].task_message)

    def test_saved_attachments_are_restored_and_urls_preserved(self):
        from openai_managed.attachments import AttachmentSet
        url = 'https://example.test/logo.svg?signature=abc%2Fdef'
        self.database.notes['303839'] = f'## Attachments\n- logo.svg: <{url}>\n'
        with patch('openai_managed.runner.restore_saved_attachments') as restore:
            result = self.runner.run(self.task)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(restore.call_args.args[0], [url])
        self.assertEqual(saved_attachment_urls(result.notes), [url])

    def test_result_for_another_deal_is_rejected(self):
        with self.output(deal_id='999'):
            result = self.runner.run(self.task)
        self.assertEqual(result.failure_code, 'invalid_result')
        self.assertEqual(self.database.completed, [])
