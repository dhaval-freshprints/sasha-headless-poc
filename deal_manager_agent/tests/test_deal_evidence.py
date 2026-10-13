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
    'DEAL_MANAGER_CRM_SCHEMA': 'test_crm',
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
        with patch('database.pymysql.connect') as database:
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
            'main': 'test-main', 'operations': 'test_operations', 'crm': 'test_crm',
        })
        self.assertNotIn('synthetic-private-password', result.stdout + result.stderr)

    def test_managed_secrets_work_when_executor_omits_environment(self):
        settings = json.dumps({"NON_PROD_HOST":"db.example","NON_PROD_USER":"reader","NON_PROD_PASS":"synthetic-secret", **SCHEMAS})
        with patch.dict(os.environ, {}, clear=True), \
             patch('database.Path.is_file', return_value=True), \
             patch('database.Path.read_text', return_value=settings), \
             patch('database.ssl.create_default_context') as tls, \
             patch('database.pymysql.connect') as database, \
             patch('database.getpass.getpass') as prompt:
            connect('test-main')
            self.assertEqual(database.call_args.kwargs['host'], 'db.example')
            self.assertEqual(database.call_args.kwargs['password'], 'synthetic-secret')
            tls.assert_called_once_with(cafile='/opt/rds-ca.pem')
            prompt.assert_not_called()
            self.assertEqual(database.call_args.kwargs['database'], 'test-main')

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
    def test_queries_follow_all_three_configured_schema_roles(self):
        with patch.dict(os.environ, {
            'DEAL_MANAGER_MAIN_SCHEMA': 'alternate_main',
            'DEAL_MANAGER_OPERATIONS_SCHEMA': 'alternate_operations',
            'DEAL_MANAGER_CRM_SCHEMA': 'alternate-crm',
        }):
            collector = self.collector(thread_id='synthetic-thread', proof_ids=[123])
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        collector.collect_crm_messages()
        collector.collect_explicit_references()
        queries = '\n'.join(call.args[1] for call in collector.query.call_args_list)
        self.assertIn('`alternate_main`.proof_request', queries)
        self.assertIn('`alternate_operations`.sasha_communication_history', queries)
        self.assertIn('`alternate-crm`.message', queries)

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

    def test_provider_id_is_not_treated_as_sasha_external_id(self):
        collector = self.collector(message_id='synthetic-provider-id')
        collector.query = Mock()
        collector.collect_message_links()
        collector.query.assert_not_called()

    def test_prior_history_excludes_future_rows_and_target(self):
        collector = self.collector(thread_id='synthetic-thread',sasha_external_message_id='synthetic-target')
        collector.replay = True
        collector.query = Mock(return_value=[])
        collector.collect_message_links()
        _,sql,parameters = collector.query.call_args.args
        self.assertIn('message_timestamp < %s',sql)
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

    def test_crm_phone_pair_includes_both_directions(self):
        collector = self.collector(sender_phone='+12025550123',recipient_phone='+12025550456')
        collector.read_crm_messages = Mock()
        collector.collect_crm_sms_pair()
        self.assertEqual(collector.read_crm_messages.call_count,2)
        calls = collector.read_crm_messages.call_args_list
        self.assertEqual(calls[0].kwargs['index_name'],'sender')
        self.assertEqual(calls[1].kwargs['index_name'],'recipient')
        for call in calls:
            self.assertIn('2025550123',call.args[2])
            self.assertIn('2025550456',call.args[2])
            self.assertTrue(call.kwargs['prior'])

    def test_crm_replay_skips_current_provider_message(self):
        collector = self.collector(message_id='synthetic-current',reply_to_message_id='synthetic-parent')
        collector.replay = True
        collector.read_crm_messages = Mock()
        collector.collect_crm_messages()
        self.assertEqual(collector.read_crm_messages.call_count,1)
        self.assertEqual(collector.read_crm_messages.call_args.args[2],['synthetic-parent'])

    def test_crm_multiple_clients_are_preserved_and_flagged(self):
        collector = self.collector()
        collector.crm_message_ids.add(10)
        collector.query = Mock(side_effect=[[
            {'id':1,'crm_message_id':10,'client_id':101},
            {'id':2,'crm_message_id':10,'client_id':202},
        ],[]])
        collector.link_crm_activities()
        self.assertEqual(collector.client_ids,{101,202})
        self.assertTrue(any('multiple clients' in warning for warning in collector.warnings))


if __name__ == '__main__':
    unittest.main()
