"""Storage tests run against an explicitly enabled, disposable local MySQL instance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import pymysql
from sasha_database.config import DatabaseSettings, container_database_options
from sasha_database.connection import DatabaseError
from sasha_database.messages import IncomingMessage, incoming_message
from sasha_database.storage import SashaStorage


class MessageTests(unittest.TestCase):
    def test_plain_text_and_envelopes_preserve_bodies(self):
        self.assertEqual(incoming_message('Keep From: inside my body').body, 'Keep From: inside my body')
        message = incoming_message('Channel: Email\nFrom: art@freshprints.com\nSubject: Ready\n\nProof ready')
        self.assertEqual((message.body, message.sender_name, message.convo_type),
                         ('Proof ready', 'art@freshprints.com', 'internal_mail'))

    def test_sms_requires_sender_and_metadata_has_db_limits(self):
        for changes in ({'message_type': 'SMS'}, {'subject': 'x'*501},
                        {'external_message_id': 'x'*256}, {'external_thread_id': 'x'*256}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                IncomingMessage('Hello', **changes)

    def test_timestamp_converts_to_utc(self):
        message = IncomingMessage('Hello', message_timestamp='2026-10-14T10:00:00+05:30')
        self.assertEqual(message.timestamp().isoformat(), '2026-10-14T04:30:00')

    def test_message_body_limit_counts_utf8_bytes_before_any_write(self):
        settings = DatabaseSettings({})
        with patch('sasha_database.messages.MAX_MESSAGE_BYTES', 8):
            self.assertEqual(IncomingMessage('😀😀').body, '😀😀')
            with self.assertRaisesRegex(ValueError, 'MEDIUMTEXT'):
                IncomingMessage('😀😀a')
            with self.assertRaisesRegex(ValueError, 'MEDIUMTEXT'):
                SashaStorage(settings).complete_run('42', 'run', '', '', '😀😀a', 'outreach')

    def test_container_configuration_is_allowlisted_and_secrets_are_not_arguments(self):
        settings = DatabaseSettings({
            'NON_PROD_PASS': 'secret', 'NON_PROD_HOST': 'test',
            'UNRELATED_SECRET': 'private',
        })
        options, environment = container_database_options(settings)
        self.assertNotIn('secret', str(options))
        self.assertNotIn('UNRELATED_SECRET', options)
        self.assertIn('NON_PROD_PASS', options)
        self.assertEqual(environment['NON_PROD_PASS'], 'secret')
        self.assertNotIn('database.json', str(options))

    def test_installed_skill_retains_its_shared_database_dependency(self):
        import subprocess
        import sys
        import tempfile
        import json
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, str(root / 'deal_manager_agent/install_skills.py'),
                            '--destination', directory], check=True, capture_output=True)
            helper = Path(directory) / 'deal-manager-database/scripts/database.py'
            result = subprocess.run([sys.executable, str(helper), 'schemas'], check=True,
                                    capture_output=True, text=True, env={
                'DEAL_MANAGER_MAIN_SCHEMA': 'test_main', 'DEAL_MANAGER_OPERATIONS_SCHEMA': 'test_operations',
            })
            self.assertEqual(json.loads(result.stdout), {'main': 'test_main', 'operations': 'test_operations'})

    def test_neither_container_receives_the_crm_schema(self):
        import tempfile
        from deal_manager_agent.settings import DealManagerSettings
        from deal_manager_agent.sandbox import DealManagerSandbox
        from deal_manager_agent.models import LookupRequest
        from openai_managed.sandbox import DockerSandbox, SandboxConfig
        from openai_managed.task import SashaTask
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = DatabaseSettings({
                'NON_PROD_HOST': 'test', 'NON_PROD_PORT': '3306', 'NON_PROD_USER': 'test',
                'NON_PROD_PASS': 'synthetic-database-secret',
                'DEAL_MANAGER_MAIN_SCHEMA': 'main', 'DEAL_MANAGER_OPERATIONS_SCHEMA': 'operations',
                'DEAL_MANAGER_CRM_SCHEMA': 'crm',
            })
            dm = DealManagerSandbox(DealManagerSettings('model', 'medium', 'key', 'image',
                                                        root / 'dm', database.values))
            am = DockerSandbox(SandboxConfig('image', root / 'am'), 'key')
            dm.prepare(LookupRequest(sender_email='client@example.com', body='Hello'))
            am.prepare(SashaTask('42', 'task', 'https://example.com/deal?id=42'))
            with patch('subprocess.run') as run, patch('openai_managed.sandbox.DatabaseSettings.from_environment', return_value=database):
                dm.start_container()
                am.start_container()
            names = []
            for call in run.call_args_list:
                args = call.args[0]
                names.append({args[i+1] for i, value in enumerate(args) if value == '--env'})
                self.assertNotIn('synthetic-database-secret', str(args))
                self.assertNotIn('/run/secrets', str(args))
                self.assertEqual(call.kwargs['env']['NON_PROD_PASS'], 'synthetic-database-secret')
            self.assertEqual(len(names), 2)
            self.assertNotIn('DEAL_MANAGER_CRM_SCHEMA', names[1])
            self.assertNotIn('NON_PROD_SSL_CA', names[1])
            for call in run.call_args_list:
                self.assertNotIn('rds-ca.pem', str(call.args))
            self.assertEqual(names[0], names[1])

    def test_default_database_configuration_does_not_require_or_load_crm(self):
        from sasha_database.connection import connect_readonly
        environment = {
            'NON_PROD_HOST': 'test', 'NON_PROD_USER': 'test', 'NON_PROD_PASS': 'secret',
            'DEAL_MANAGER_MAIN_SCHEMA': 'main', 'DEAL_MANAGER_OPERATIONS_SCHEMA': 'operations',
        }
        with patch.dict(os.environ, environment, clear=True), \
                patch('sasha_database.config.bootstrap_deal_manager_env'):
            settings = DatabaseSettings.from_environment()
            self.assertNotIn('DEAL_MANAGER_CRM_SCHEMA', settings.values)
            with patch.dict(os.environ, {'DEAL_MANAGER_CRM_SCHEMA': 'unused_crm'}):
                self.assertEqual(DatabaseSettings.from_environment(), settings)
                with self.assertRaisesRegex(ValueError, 'outside the configured scope'):
                    connect_readonly('unused_crm')


TABLES = [
    '''CREATE TABLE IF NOT EXISTS sasha_deal_status (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, deal_id INT NOT NULL UNIQUE,
        status ENUM('active','paused','handoff_needed','opt_out','completed','rejected') NOT NULL DEFAULT 'active',
        status_reason TEXT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        deleted_at DATETIME NULL) ENGINE=InnoDB''',
    '''CREATE TABLE IF NOT EXISTS sasha_deal_notes (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, deal_id INT NOT NULL UNIQUE, notes LONGTEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        deleted_at DATETIME NULL) ENGINE=InnoDB''',
    '''CREATE TABLE IF NOT EXISTS sasha_conversation_history (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, deal_id INT NOT NULL, run_id VARCHAR(64),
        convo_type ENUM('client_response','followup','internal_mail','outreach') NOT NULL,
        direction ENUM('INBOUND','OUTBOUND') NOT NULL, sender_name VARCHAR(255) NOT NULL,
        message_type ENUM('EMAIL','SMS','CHAT') NOT NULL, subject VARCHAR(500), body MEDIUMTEXT NOT NULL,
        content_type ENUM('text','text/html') NOT NULL DEFAULT 'text',
        delivery_status ENUM('generated','sent','failed'), attachments JSON,
        external_message_id VARCHAR(255), external_thread_id VARCHAR(255), message_timestamp DATETIME,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, deleted_at DATETIME,
        KEY (deal_id,message_timestamp), KEY (deal_id,convo_type), KEY (run_id),
        KEY (external_message_id), KEY (external_thread_id)) ENGINE=InnoDB''',
    # v3's handoff tables (migrations 20260710_0001 and _0003), used unchanged.
    '''CREATE TABLE IF NOT EXISTS sasha_handoff_isas (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL UNIQUE, email VARCHAR(255) NOT NULL UNIQUE,
        first_name VARCHAR(255), last_name VARCHAR(255), rank_order INT NOT NULL, is_active BOOLEAN NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB''',
    '''CREATE TABLE IF NOT EXISTS sasha_handoff_offers (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, deal_id INT NOT NULL, sasha_deal_context_id INT NULL,
        isa_id INT NOT NULL, thread_id VARCHAR(255) NULL,
        status ENUM('OFFERED','ACCEPTED','REJECTED','TIMED_OUT') NOT NULL,
        offered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, responded_at DATETIME NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        open_deal_id INT AS (CASE WHEN status IN ('OFFERED','ACCEPTED') THEN deal_id ELSE NULL END) STORED,
        UNIQUE KEY uq_sasha_handoff_offers_one_open_per_deal (open_deal_id),
        CONSTRAINT fk_sasha_handoff_offers_isa_id FOREIGN KEY (isa_id) REFERENCES sasha_handoff_isas (id)) ENGINE=InnoDB''',
    # v3's settings table, and the samples table from sql/20261015_0002_sasha_sample_requests.sql.
    '''CREATE TABLE IF NOT EXISTS sasha_settings (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, `key` VARCHAR(100) NOT NULL UNIQUE, `value` TEXT NOT NULL,
        description VARCHAR(500) NOT NULL DEFAULT '', status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB''',
    '''CREATE TABLE IF NOT EXISTS sasha_sample_requests (
    id                   INT           NOT NULL AUTO_INCREMENT,
    deal_id              INT           NOT NULL,
    run_id               VARCHAR(64)   NULL     COMMENT 'Account Manager run that placed the sample',
    status               ENUM('placed', 'shipped', 'delivered', 'cancelled') NOT NULL DEFAULT 'placed',
    style_code           VARCHAR(64)   NOT NULL,
    color                VARCHAR(128)  NOT NULL,
    size                 VARCHAR(32)   NOT NULL,
    freshdesk_ticket_url VARCHAR(500)  NULL     COMMENT 'NULL when Freshdesk is not configured',
    shipped_at           DATETIME      NULL,
    delivered_at         DATETIME      NULL,
    cancelled_at         DATETIME      NULL,
    cancel_reason        VARCHAR(255)  NULL,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'When the sample was placed',
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at           DATETIME      NULL,
    PRIMARY KEY (id),
    KEY ix_sasha_sample_requests_deal_id (deal_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Free blank samples Sasha placed for a deal' ''',
    # v3's follow-up schedule (migration 20260414_0001 and 20260715_0001), used unchanged.
    '''CREATE TABLE IF NOT EXISTS sasha_followup_schedule (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY, deal_id INT NOT NULL UNIQUE,
        status ENUM('active','paused','stopped','completed') NOT NULL, stop_reason VARCHAR(50) NULL,
        first_message_timestamp DATETIME NOT NULL, last_followup_timestamp DATETIME NULL,
        last_channel_used ENUM('email','sms') NULL, followup_count INT NOT NULL DEFAULT 0,
        next_followup_date DATETIME NULL, scheduled_followup_reason TEXT NULL, followup_metadata JSON NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB''',
    'CREATE TABLE IF NOT EXISTS user (id INT PRIMARY KEY,email VARCHAR(255),deleted_at DATETIME)',
    'CREATE TABLE IF NOT EXISTS deals (id INT PRIMARY KEY,client_id INT,deleted_at DATETIME)',
]


@unittest.skipUnless(os.environ.get('SASHA_TEST_MYSQL_PORT'), 'Requires disposable local MySQL')
class MySQLStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Host/schema are fixed to prevent accidentally running fixture writes on QA.
        cls.admin = pymysql.connect(host='127.0.0.1', port=int(os.environ['SASHA_TEST_MYSQL_PORT']),
                                    user='root', password='', database='sasha_test', autocommit=True,
                                    cursorclass=pymysql.cursors.DictCursor)
        with cls.admin.cursor() as cursor:
            for statement in TABLES:
                cursor.execute(statement)
            cursor.execute('DROP TRIGGER IF EXISTS reject_test_draft')
            cursor.execute("CREATE TRIGGER reject_test_draft BEFORE INSERT ON sasha_conversation_history "
                           "FOR EACH ROW BEGIN IF NEW.body='reject this draft' THEN "
                           "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'; END IF; END")
        cls.settings = DatabaseSettings({
            'NON_PROD_HOST': '127.0.0.1', 'NON_PROD_PORT': os.environ['SASHA_TEST_MYSQL_PORT'],
            'NON_PROD_USER': 'root', 'NON_PROD_PASS': '',
            'DEAL_MANAGER_MAIN_SCHEMA': 'sasha_test', 'DEAL_MANAGER_OPERATIONS_SCHEMA': 'sasha_test',
        })

    @classmethod
    def tearDownClass(cls):
        cls.admin.close()

    def setUp(self):
        with self.admin.cursor() as cursor:
            for table in ('sasha_followup_schedule','sasha_sample_requests','sasha_settings','sasha_handoff_offers','sasha_handoff_isas',
                          'sasha_conversation_history',
                          'sasha_deal_notes','sasha_deal_status','deals','user'):
                cursor.execute(f'DELETE FROM {table}')
            cursor.execute("INSERT INTO user (id,email) VALUES (7,'client@example.com')")
            cursor.execute('INSERT INTO deals (id,client_id) VALUES (42,7)')
        sender = patch.dict(os.environ, {'SASHA_SENDER_EMAIL': 'sasha@example.com'})
        sender.start()
        self.addCleanup(sender.stop)
        self.storage = SashaStorage(self.settings)

    def scalar(self, sql):
        with self.admin.cursor() as cursor:
            cursor.execute(sql)
            return next(iter(cursor.fetchone().values()))

    def test_active_status_is_unique_and_existing_status_is_preserved(self):
        self.storage.prepare_run('42', 'one')
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_deal_status SET status='paused' WHERE deal_id=42")
        self.storage.prepare_run('42', 'two')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sasha_deal_status'), 1)
        self.assertEqual(self.scalar('SELECT status FROM sasha_deal_status'), 'paused')

    def test_shared_connection_uses_no_tls(self):
        from sasha_database.connection import transaction
        with transaction(self.settings, readonly=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                self.assertEqual(cursor.fetchone()['Value'], '')

    def test_missing_email_is_looked_up_and_inbound_survives_without_completion(self):
        saved = self.storage.prepare_run('42', 'run', (IncomingMessage('Hello'),))
        self.assertEqual(saved[0].sender_name, 'client@example.com')
        notes, history = self.storage.load_context('42')
        self.assertEqual(notes, '')
        self.assertEqual(history[0]['content'], 'Hello')
        self.assertIsNone(history[0]['delivery_status'])

    def test_missing_client_email_rolls_back_status_and_input(self):
        with self.assertRaises(ValueError):
            self.storage.prepare_run('99', 'run', (IncomingMessage('Hello'),))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sasha_deal_status'), 0)

    def test_provider_retries_and_row_ids_do_not_duplicate(self):
        message = IncomingMessage('Hello', external_message_id='provider-1')
        first = self.storage.prepare_run('42', 'one', (message,))[0]
        second = self.storage.prepare_run('42', 'two', (message,))[0]
        self.storage.prepare_run('42', 'one', (first,))
        self.assertEqual(first.row_id, second.row_id)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sasha_conversation_history'), 1)
        with self.assertRaises(ValueError):
            self.storage.prepare_run('42', 'three', (replace(message, body='Different'),))

    def test_concurrent_provider_retries_are_serialized(self):
        message = IncomingMessage('Hello', 'client@example.com', external_message_id='same')
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda i: self.storage.prepare_run('42', str(i), (message,)), range(2)))
        self.assertEqual(results[0][0].row_id, results[1][0].row_id)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sasha_conversation_history'), 1)

    def test_email_sms_internal_metadata_and_order(self):
        messages = (
            IncomingMessage('SMS', '+14155550100', 'SMS', message_timestamp='2026-10-14T10:00:00'),
            IncomingMessage('Internal', 'art@freshprints.com', convo_type='internal_mail',
                            subject='Proof', external_thread_id='thread', attachments=('https://example.com/art.svg',),
                            message_timestamp='2026-10-13T10:00:00'),
        )
        self.storage.prepare_run('42', 'run', messages)
        history = self.storage.load_context('42')[1]
        self.assertEqual([row['content'] for row in history], ['Internal', 'SMS'])
        self.assertEqual(history[0]['attachments'], ['https://example.com/art.svg'])
        self.assertEqual(history[0]['convo_type'], 'internal_mail')
        self.assertEqual(history[1]['message_type'], 'SMS')

    def test_handoff_completion_sets_status_reason_notes_and_holding_draft_together(self):
        self.storage.prepare_run('42', 'one')
        self.storage.complete_run('42', 'one', '', 'Handed off.', '<p>Checking with my team.</p>', 'client_response',
                                  handoff_reason='  Asked for a manager. ')
        self.assertEqual(self.storage.deal_status('42'), ('handoff_needed', 'Asked for a manager.'))
        self.assertEqual(self.storage.load_notes('42'), 'Handed off.')
        self.assertEqual(self.storage.deal_status('99'), ('active', ''))
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.storage.complete_run('42', 'two', 'Handed off.', 'x', '', None, handoff_reason='  ')

    def test_handoff_offers_keep_one_open_offer_per_deal(self):
        from openai_managed.handoff.selection import select_next_isa
        from sasha_database.handoff import HandoffOffers
        with self.admin.cursor() as cursor:
            cursor.execute("INSERT INTO sasha_handoff_isas (id,user_id,email,first_name,rank_order) VALUES "
                           "(1,501,'a@freshprints.com','A',1),(2,502,'b@freshprints.com','B',2)")
        self.storage.prepare_run('42', 'one')
        self.storage.complete_run('42', 'one', '', 'Handed off.', '', None, handoff_reason='Chapter store.')
        offers = HandoffOffers(self.settings)
        outcome, offer, isa = offers.create_offer('42', select_next_isa)
        self.assertEqual((outcome, isa.email), ('created', 'a@freshprints.com'))
        self.assertEqual(offers.create_offer('42', select_next_isa)[0], 'skipped_existing')
        self.assertTrue(offers.mark_timed_out(offer.id))
        self.assertEqual(offers.deals_needing_offer(), [42])
        outcome, second, isa = offers.create_offer('42', select_next_isa)
        self.assertEqual((outcome, isa.email), ('created', 'b@freshprints.com'))
        self.assertTrue(offers.accept(second.id))
        self.assertEqual([item.status for item in offers.offers_for_deal('42')], ['TIMED_OUT', 'ACCEPTED'])
        self.assertEqual(offers.create_offer('42', select_next_isa)[0], 'skipped_existing')
        self.assertEqual(self.storage.client_contact('42'), {'client_id': 7, 'email': 'client@example.com'})

    def test_samples_are_stored_updated_and_settings_read(self):
        from openai_managed.samples.service import SampleService
        with self.admin.cursor() as cursor:
            cursor.execute("INSERT INTO sasha_settings (`key`,`value`) VALUES ('followup_sample_max_unit_value','50'),"
                           "('sample_max_per_deal','4'),('followup_sample_cooldown_days','14'),('sasha_user_id','42')")
        service = SampleService(storage=self.storage, freshdesk_factory=lambda: None)
        request = {'trigger': 'client_request', 'style_code': 'C1717', 'product_link': '', 'color': 'White',
                   'size': 'M', 'ship_to_name': 'Rita', 'ship_to_address': '1 Main St',
                   'contact_email': '', 'contact_phone': '', 'organization': ''}
        self.assertTrue(service.place('42', 'one', request)['placed'])
        context = service.context('42')
        self.assertEqual(context['limits']['sample_max_per_deal'], '4')
        sample_id = context['samples'][0]['id']
        self.assertTrue(service.record_update('42', {'sample_id': sample_id, 'status': 'shipped'})['updated'])
        sample = service.context('42')['samples'][0]
        self.assertEqual(sample['status'], 'shipped')
        self.assertIsNotNone(sample['shipped_at'])
        from deal_manager_agent.deal_queries import sasha_user_id
        with patch('sasha_database.settings.DatabaseSettings.from_environment', return_value=self.settings):
            self.assertEqual(sasha_user_id(), 42)

    def test_deal_state_is_saved_with_the_turn_and_never_overrides_a_handoff(self):
        self.storage.prepare_run('42', 'one')
        state = {'status': 'paused', 'reason': 'Asked to reconnect after spring break.',
                 'next_check_on': '2027-03-22', 'crm_lost_reason': ''}
        self.storage.complete_run('42', 'one', '', 'Paused.', '', None, deal_state=state)
        self.assertEqual(self.storage.apply_followup_schedule('42', {**state, 'status': 'paused'}),
                         'scheduled for 2027-03-22')
        saved = self.storage.deal_state('42')
        self.assertEqual((saved['status'], saved['status_reason']), ('paused', state['reason']))
        self.assertEqual((saved['followup']['status'], saved['followup']['next_followup_date'][:10]),
                         ('active', '2027-03-22'))
        self.assertEqual(self.storage.apply_followup_schedule('42', {**state, 'status': 'opt_out'}),
                         'stopped (opted_out)')
        self.storage.apply_followup_schedule('42', {**state, 'status': 'paused', 'next_check_on': None})
        self.assertEqual(self.storage.deal_state('42')['followup']['status'], 'stopped')
        self.storage.complete_run('42', 'two', 'Paused.', 'Handed off.', '', None, handoff_reason='Wants a store.')
        self.storage.complete_run('42', 'three', 'Handed off.', 'x', '', None,
                                  deal_state={**state, 'status': 'active'})
        self.assertEqual(self.storage.deal_state('42')['status'], 'handoff_needed')
        with self.assertRaisesRegex(ValueError, 'reason'):
            self.storage.complete_run('42', 'four', 'x', 'y', '', None, deal_state={**state, 'reason': ' '})

    def test_due_followups_are_claimed_once_counted_paused_and_stopped_on_handoff(self):
        from datetime import datetime
        self.storage.prepare_run('42', 'one')
        state = {'status': 'active', 'reason': 'Sent mockup; check in.', 'next_check_on': '2026-10-14',
                 'crm_lost_reason': ''}
        self.storage.apply_followup_schedule('42', state)
        self.assertEqual(self.storage.claim_due_followups(datetime(2026, 10, 13, 15)), [])
        self.assertEqual(self.storage.claim_due_followups(datetime(2026, 10, 14, 15)), [42])
        self.assertEqual(self.storage.claim_due_followups(datetime(2026, 10, 14, 15)), [])
        self.storage.record_followup_sent('42')
        self.storage.pause_followups('42', 'No next follow-up chosen after this check')
        followup = self.storage.deal_state('42')['followup']
        self.assertEqual((followup['status'], followup['followup_count']), ('paused', 1))
        self.storage.apply_followup_schedule('42', {**state, 'next_check_on': '2026-10-20'})
        self.storage.complete_run('42', 'two', '', 'Handed off.', '', None, handoff_reason='Wants a store.')
        followup = self.storage.deal_state('42')['followup']
        self.assertEqual((followup['status'], followup['stop_reason'], followup['next_followup_date']),
                         ('stopped', 'handoff', None))

    def test_completion_commits_notes_and_generated_draft(self):
        self.storage.prepare_run('42', 'run')
        self.storage.complete_run('42', 'run', '', 'Notes', '<p>Hello</p>', 'outreach')
        notes, history = self.storage.load_context('42')
        self.assertEqual(notes, 'Notes')
        self.assertEqual(history[0]['delivery_status'], 'generated')
        self.assertEqual(history[0]['sender_name'], 'sasha@example.com')
        self.storage.complete_run('42', 'run', '', 'Notes', '<p>Hello</p>', 'outreach')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM sasha_conversation_history'), 1)

    def test_outgoing_insert_failure_rolls_back_notes_but_keeps_inbound(self):
        self.storage.prepare_run('42', 'run', (IncomingMessage('Hello'),))
        with self.assertRaises(DatabaseError):
            self.storage.complete_run('42', 'run', '', 'Must roll back', 'reject this draft', 'client_response')
        notes, history = self.storage.load_context('42')
        self.assertEqual(notes, '')
        self.assertEqual([row['content'] for row in history], ['Hello'])

    def test_no_reply_and_stale_notes(self):
        self.storage.prepare_run('42', 'run')
        self.storage.complete_run('42', 'run', '', 'Notes', '', None)
        self.assertEqual(self.storage.load_context('42'), ('Notes', []))
        with self.assertRaises(ValueError):
            self.storage.complete_run('42', 'older', '', 'Stale', '<p>Draft</p>', 'followup')
        self.assertEqual(self.storage.load_context('42'), ('Notes', []))

    def test_deleted_rows_are_excluded_and_notes_can_be_replaced(self):
        self.storage.prepare_run('42', 'run', (IncomingMessage('Old'),))
        self.storage.complete_run('42', 'run', '', 'Old notes', '', None)
        with self.admin.cursor() as cursor:
            cursor.execute('UPDATE sasha_conversation_history SET deleted_at=NOW()')
            cursor.execute('UPDATE sasha_deal_notes SET deleted_at=NOW()')
        self.assertEqual(self.storage.load_context('42'), ('', []))
        self.storage.complete_run('42', 'new', '', 'New', '', None)
        self.assertEqual(self.storage.load_notes('42'), 'New')

    def test_deal_manager_reads_provider_thread_and_sender_from_conversation_table(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'deal_manager_agent/skills/deal-manager-resolve-deal/scripts/collect_evidence.py'
        spec = importlib.util.spec_from_file_location('database_test_collector', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original = IncomingMessage('Email', 'client@example.com', external_message_id='parent',
                                   external_thread_id='thread', message_timestamp='2026-10-12T10:00:00')
        self.storage.prepare_run('42', 'email', (original,))
        self.storage.prepare_run('43', 'sms', (replace(original, body='SMS', message_type='SMS', sender_name='+12025550123'),))
        self.storage.prepare_run('44', 'future', (replace(original, message_timestamp='2026-10-14T10:00:00'),))
        self.storage.prepare_run('45', 'deleted', (original,))
        with self.admin.cursor() as cursor:
            cursor.execute('UPDATE sasha_conversation_history SET deleted_at=NOW() WHERE deal_id=45')
        with patch.object(module, 'configured_schemas', return_value={'main': 'sasha_test', 'operations': 'sasha_test'}):
            email = module.EvidenceCollector(self.admin, {
                'channel': 'EMAIL', 'received_at': '2026-10-13T10:00:00Z',
                'sender_email': 'client@example.com', 'reply_to_message_id': 'parent', 'thread_id': 'thread',
            })
            email.collect_message_links()
            email.collect_sender_history()
            sms = module.EvidenceCollector(self.admin, {
                'channel': 'SMS', 'received_at': '2026-10-13T10:00:00Z', 'sender_phone': '+12025550123',
            })
            sms.collect_sender_history()
            provider = module.EvidenceCollector(self.admin, {
                'channel': 'EMAIL', 'received_at': '2026-10-13T10:00:00Z', 'message_id': 'parent',
            })
            provider.collect_message_links()
        self.assertEqual(email.candidate_ids, {42})
        self.assertEqual(sms.candidate_ids, {43})
        self.assertEqual(provider.candidate_ids, {42, 44})
        for collector in (email, sms, provider):
            self.assertFalse(any('error_type' in row for row in collector.observations))

    def test_latest_outbound_uses_conversation_history_without_legacy_context(self):
        from deal_manager_agent.deal_queries import DatabaseSession, latest_outbound_by_deal
        from sasha_database.read import read_query
        self.storage.prepare_run('42', 'run', (IncomingMessage('Incoming'),))
        self.storage.complete_run('42', 'run', '', 'Notes', 'Draft', 'outreach')
        database = DatabaseSession(lambda sql, parameters, limit=100: read_query(self.admin, sql, parameters, limit),
                                   '`sasha_test`', '`sasha_test`')
        self.assertEqual(set(latest_outbound_by_deal(database, [42, 43])), {42})
        with self.admin.cursor() as cursor:
            cursor.execute("UPDATE sasha_conversation_history SET deleted_at=NOW() WHERE direction='OUTBOUND'")
        self.assertEqual(latest_outbound_by_deal(database, [42]), {})
