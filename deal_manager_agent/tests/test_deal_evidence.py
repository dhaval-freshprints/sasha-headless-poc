"""Behavioral tests using synthetic rows; no network or real credentials."""
import sys
import json
import subprocess
import tempfile
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills' / 'deal-manager-resolve-deal' / 'scripts'))
from collect_evidence import EvidenceCollector, normalize_email, normalize_phone, utc_timestamp, validate_input
from database import connect, read_query, configured_schemas


SCHEMAS = {
    'DEAL_MANAGER_MAIN_SCHEMA': 'test-main',
    'DEAL_MANAGER_OPERATIONS_SCHEMA': 'test_operations',
}


class ConfiguredDatabaseTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, SCHEMAS)
        environment.start()
        self.addCleanup(environment.stop)


class DatabaseTests(ConfiguredDatabaseTests):
    def test_schema_configuration_has_no_default_and_rejects_sql_identifiers(self):
        with patch.dict(os.environ, {"DEAL_MANAGER_MAIN_SCHEMA": ""}):
            with self.assertRaisesRegex(ValueError, "DEAL_MANAGER_MAIN_SCHEMA"):
                configured_schemas()
        for name in ("bad`; SELECT 1", "schema.name", "a" * 65):
            with patch.dict(os.environ, {"DEAL_MANAGER_MAIN_SCHEMA": name}):
                with self.assertRaises(ValueError):
                    configured_schemas()

    def test_unconfigured_schema_is_rejected_before_connecting(self):
        with patch('sasha_database.connection.pymysql.connect') as database:
            with self.assertRaisesRegex(ValueError, "outside"):
                connect('unconfigured_database')
        database.assert_not_called()

    def test_cli_reads_schema_names_from_env_file_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            configuration = Path(directory) / 'database.env'
            configuration.write_text('\n'.join(f'{key}={value}' for key, value in {
                **SCHEMAS, 'NON_PROD_PASS': 'synthetic-private-password',
            }.items()))
            environment = {key: value for key, value in os.environ.items()
                           if key not in SCHEMAS and key != 'DEAL_MANAGER_DATABASE_CONFIG'}
            result = subprocess.run([
                sys.executable, str(ROOT / 'skills/deal-manager-database/scripts/database.py'),
                '--env-file', str(configuration), 'schemas',
            ], env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {
            'main': 'test-main', 'operations': 'test_operations',
        })
        self.assertNotIn('synthetic-private-password', result.stdout + result.stderr)

    def test_environment_configuration_drives_shared_connection(self):
        from sasha_database.config import DatabaseSettings
        settings = DatabaseSettings({
            "NON_PROD_HOST": "db.example", "NON_PROD_PORT": "3306", "NON_PROD_USER": "reader",
            "NON_PROD_PASS": "synthetic-secret", **SCHEMAS,
        })
        with patch('sasha_database.connection.DatabaseSettings.from_environment', return_value=settings), \
                patch('sasha_database.connection.pymysql.connect') as database:
            connect('test-main')
            self.assertEqual(database.call_args.kwargs['host'], 'db.example')
            self.assertEqual(database.call_args.kwargs['password'], 'synthetic-secret')
            self.assertTrue(database.call_args.kwargs['ssl_disabled'])
            self.assertNotIn('ssl', database.call_args.kwargs)
            statements = [call.args[0] for call in database.return_value.cursor.return_value.__enter__.return_value.execute.call_args_list]
            self.assertIn('START TRANSACTION READ ONLY', statements)

    def test_write_and_file_access_rejected_before_execution(self):
        connection = Mock()
        for sql in ['DELETE FROM deals', 'SELECT 1; DELETE FROM deals', "SELECT LOAD_FILE(%s)", 'SELECT 1 INTO OUTFILE %s']:
            with self.assertRaises(ValueError):
                read_query(connection, sql)
        connection.cursor.assert_not_called()

    def test_bound_values_and_row_truncation(self):
        cursor = Mock()
        cursor.fetchmany.return_value = [{'id':1},{'id':2}]
        connection = Mock()
        connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        connection.cursor.return_value.__exit__ = Mock(return_value=False)
        hostile_value = "a'; DELETE FROM deals"
        result = read_query(connection, 'SELECT id FROM deals WHERE title=%s', [hostile_value], limit=1)
        sql, parameters = cursor.execute.call_args.args
        self.assertNotIn(hostile_value, sql)
        self.assertEqual(parameters, (hostile_value,2))
        self.assertTrue(result['truncated'])
        self.assertEqual(result['rows'], [{'id':1}])


class NormalizationTests(unittest.TestCase):
    def test_email_preserves_plus_alias(self):
        self.assertEqual(normalize_email('Person <USER+event@Example.COM>'), 'user+event@example.com')

    def test_national_phone_requires_country(self):
        with self.assertRaises(ValueError):
            normalize_phone('202-555-0123')
        self.assertEqual(normalize_phone('(202) 555-0123','US'), '+12025550123')

    def test_international_phone_not_reduced_to_ten_digits(self):
        self.assertEqual(normalize_phone('+91 98765 43210'), '+919876543210')
        with self.assertRaises(ValueError):
            normalize_phone('2025550123 ext 2', 'US')

    def test_timestamp_requires_zone_and_converts_to_utc(self):
        self.assertEqual(str(utc_timestamp('2026-10-12T15:00:00+05:30')), '2026-10-12 09:30:00')
        with self.assertRaises(ValueError):
            utc_timestamp('2026-10-12T15:00:00')

    def test_invalid_entity_id_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_input({'channel':'EMAIL','received_at':'2026-10-12T09:30:00Z','deal_ids':['1 OR 1=1']})


class EvidenceTests(ConfiguredDatabaseTests):
    def test_queries_use_only_main_and_conversation_history(self):
        with patch.dict(os.environ, {
            'DEAL_MANAGER_MAIN_SCHEMA': 'alternate_main',
            'DEAL_MANAGER_OPERATIONS_SCHEMA': 'alternate_operations',
        }):
            collector = self.collector(thread_id='synthetic-thread', proof_ids=[123])
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        collector.collect_sender_history()
        collector.collect_explicit_references()
        queries = '\n'.join(call.args[1] for call in collector.query.call_args_list)
        self.assertIn('`alternate_main`.proof_request', queries)
        self.assertIn('`alternate_operations`.sasha_conversation_history', queries)
        self.assertNotIn('sasha_deal_context', queries)
        self.assertNotIn('.message ', queries)

    def test_proof_item_fallback_retains_multiple_deals(self):
        collector = self.collector(proof_ids=[123])
        outputs = [
            {'rows': [], 'truncated': False},
            {'rows': [], 'truncated': False},
            {'rows': [{'proof_item_id':123,'proof_id':456,'deal_id':10},
                      {'proof_item_id':123,'proof_id':456,'deal_id':20}], 'truncated': False},
        ]
        with patch('collect_evidence.read_query', side_effect=outputs):
            collector.collect_explicit_references()
        self.assertEqual(collector.candidate_ids, {10,20})
        self.assertEqual(collector.observations[-1]['source'], 'proof_item_fallback')

    def test_actual_proof_does_not_become_an_unrelated_proof_item(self):
        collector = self.collector()
        collector.query = Mock(side_effect=[[{'id':123}], []])
        collector.collect_proof_links(123)
        self.assertEqual(collector.query.call_count, 2)

    def test_proof_name_is_bound_and_preserves_competing_deals(self):
        collector = self.collector(proof_names=["Design for Example"])
        with patch('collect_evidence.read_query', return_value={
            'rows':[{'proof_id':123,'deal_id':10},{'proof_id':124,'deal_id':20}],
            'truncated':False,
        }) as read:
            collector.collect_explicit_references()
        self.assertEqual(read.call_args.args[2], ["Design for Example"])
        self.assertEqual(collector.candidate_ids, {10,20})

    def collector(self, **fields):
        message = {'channel':'EMAIL','received_at':'2026-10-12T09:30:00Z',**fields}
        return EvidenceCollector(Mock(),message)

    def test_thread_keeps_conflicting_deal_candidates(self):
        collector = self.collector(thread_id='synthetic-thread')
        with patch('collect_evidence.read_query', return_value={'rows':[{'id':1,'deal_id':101},{'id':2,'deal_id':202}],'truncated':False}):
            collector.collect_message_links()
        self.assertEqual(collector.candidate_ids,{101,202})
        self.assertEqual(collector.observations[0]['source'],'prior_thread_history')

    def test_replay_never_uses_current_message_association(self):
        collector = self.collector(sasha_external_message_id='synthetic-target')
        collector.replay = True
        collector.query = Mock()
        collector.collect_message_links()
        collector.query.assert_not_called()

    def test_provider_id_reads_stored_conversation_association(self):
        collector = self.collector(message_id='synthetic-provider-id')
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        _, sql, parameters = collector.query.call_args.args
        self.assertIn('sasha_conversation_history', sql)
        self.assertIn('external_message_id=%s', sql)
        self.assertIn('deleted_at IS NULL', sql)
        self.assertEqual(parameters, ['synthetic-provider-id', 'EMAIL'])

    def test_prior_history_excludes_future_rows_and_target(self):
        collector = self.collector(thread_id='synthetic-thread',sasha_external_message_id='synthetic-target')
        collector.replay = True
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        _,sql,parameters = collector.query.call_args.args
        self.assertIn('COALESCE(message_timestamp,created_at) < %s',sql)
        self.assertIn('external_message_id<>%s',sql)
        self.assertIn('synthetic-target',parameters)

    def test_failures_and_truncation_are_not_silent_no_matches(self):
        collector = self.collector()
        with patch('collect_evidence.read_query', side_effect=TimeoutError):
            self.assertEqual(collector.query('source','SELECT 1'),[])
        self.assertIn('incomplete',collector.warnings[0])
        with patch('collect_evidence.read_query', return_value={'rows':[],'truncated':True}):
            collector.query('source','SELECT 1')
        self.assertIn('truncated',collector.warnings[1])

    def test_missing_deal_is_not_presented_as_verified(self):
        collector = self.collector()
        collector.candidate_ids.add(123)
        collector.query = Mock(return_value=[])
        self.assertEqual(collector.hydrate_candidates(),[])
        self.assertIn('not verified',collector.warnings[0])

    def test_sms_sender_history_uses_inbound_phone_and_flags_missing_recipient(self):
        collector = self.collector(channel='SMS', sender_phone='+12025550123', recipient_phone='+12025550456')
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        collector.collect_sender_history()
        _, sql, parameters = collector.query.call_args.args
        self.assertIn('sender_name IN', sql)
        self.assertIn("direction='INBOUND'", sql)
        self.assertIn('deleted_at IS NULL', sql)
        self.assertIn('2025550123', parameters)
        self.assertIn('SMS', parameters)
        self.assertNotIn('recipient_number', sql)
        self.assertTrue(any('no recipient line' in warning for warning in collector.warnings))

    def test_replay_uses_parent_but_excludes_current_provider_message(self):
        collector = self.collector(message_id='synthetic-current', reply_to_message_id='synthetic-parent')
        collector.replay = True
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        self.assertEqual(collector.query.call_count, 1)
        parameters = collector.query.call_args.args[2]
        self.assertEqual(parameters[0], 'synthetic-parent')
        self.assertIn('synthetic-current', parameters)

    def test_sender_history_preserves_multiple_candidate_deals(self):
        collector = self.collector(sender_email='client@example.com')
        with patch('collect_evidence.read_query', return_value={
            'rows': [{'id': 1, 'deal_id': 101}, {'id': 2, 'deal_id': 202}], 'truncated': False,
        }) as read:
            collector.collect_sender_history()
        self.assertEqual(collector.candidate_ids, {101, 202})
        sql, parameters = read.call_args.args[1:3]
        self.assertIn('sasha_conversation_history', sql)
        self.assertIn('client@example.com', parameters)

    def test_collector_never_reads_legacy_message_or_context_tables(self):
        collector = self.collector(sender_email='client@example.com', message_id='current',
                                   thread_id='thread', client_ids=[7], deal_ids=[42])
        with patch('collect_evidence.read_query', return_value={'rows': [], 'truncated': False}) as read:
            collector.collect()
        queries = '\n'.join(call.args[1] for call in read.call_args_list)
        self.assertIn('sasha_conversation_history', queries)
        for removed in ('sasha_communication_history', 'sasha_deal_context', '.message ', '.activity '):
            self.assertNotIn(removed, queries)


if __name__ == '__main__':
    unittest.main()
