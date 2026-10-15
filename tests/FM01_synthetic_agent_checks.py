"""Real managed-agent routing checks against a synthetic, container-local CRM."""
import json
import re
import sys
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from openai import OpenAI
from openai_managed.runner import OpenAIManagedRunner, ManagedRunnerSettings
from openai_managed.sandbox import DockerSandbox
from openai_managed.environment import ApplicationEnvironment
from openai_managed.progress import ProgressReporter
from tests.database_helpers import MemoryStorage
from sasha_database.config import DatabaseSettings
from unittest.mock import patch
from openai_managed.task import SashaTask
from openai_managed.notes import follow_up_entries

configuration = dotenv_values(ROOT / '.env')
# Only OpenAI credentials and runtime settings are used from configuration.
# No deployment CRM credentials or records enter the runner or sandbox.
DIRECTORY = ROOT / 'runs' / 'FM01_synthetic_followup_checks'
DIRECTORY.mkdir(parents=True, exist_ok=True)
BASE = 'http://127.0.0.1:8765'
application = ApplicationEnvironment('synthetic', BASE, BASE + '/login', BASE + '/catalog', BASE + '/designs', BASE + '/design-tool', BASE + '/help', BASE + '/hq')
settings = ManagedRunnerSettings(
    model=configuration.get('OPENAI_AGENT_MODEL', 'gpt-6-astra'),
    reasoning_effort=configuration.get('OPENAI_AGENT_REASONING_EFFORT', 'medium'),
    executor_api_key=configuration['OPENAI_EXECUTOR_API_KEY'],
    application=application, login_user='synthetic', login_password='synthetic',
    sandbox_image=configuration.get('OPENAI_MANAGED_SANDBOX_IMAGE', 'sasha-openai-managed:local'),
    runs_directory=DIRECTORY, connection_timeout_seconds=90, turn_timeout_seconds=300,
)
FIXTURE = """<!doctype html><html><head><title>Synthetic CRM</title></head><body>
<h1>Deal 900001 — Synthetic test deal</h1><p>Client: Alex</p>
<p>Event: Fall club recruitment in November 2026. No client-confirmed hard deadline.</p>
<p>Product already discussed: Synthetic Cotton Tee, navy. Verified fabric: 100% cotton.</p>
<p>Client concern: unsure about the feel and fit. Several design revisions have already been reviewed.</p>
<p>Proof: none currently linked. No pending art request. No order placed.</p>
<p>Shipping and stock: unknown. No verified alternative or expedited delivery path.</p>
<h2>CRM conversation (different from local conversation)</h2>
<p>Client: Please change the design now.</p>
<h2>Sample evidence</h2><p>SAMPLE_EVIDENCE</p>
</body></html>"""

class FixtureSandbox(DockerSandbox):
    def start_container(self):
        database = DatabaseSettings({
            'NON_PROD_HOST': '127.0.0.1', 'NON_PROD_PORT': '3306',
            'NON_PROD_USER': 'synthetic', 'NON_PROD_PASS': 'synthetic',
            'DEAL_MANAGER_MAIN_SCHEMA': 'synthetic', 'DEAL_MANAGER_OPERATIONS_SCHEMA': 'synthetic',
        })
        with patch('openai_managed.sandbox.DatabaseSettings.from_environment', return_value=database):
            super().start_container()

    def prepare(self, task):
        handle = super().prepare(task)
        (handle.workspace_directory / 'FM01_fixture.html').write_text(FIXTURE.replace("SAMPLE_EVIDENCE", sample_evidence))
        (handle.workspace_directory / 'FM01_fixture_server.js').write_text("const http = require('http'); const fs = require('fs'); http.createServer((req,res) => {res.writeHead(200, {'Content-Type':'text/html'}); res.end(fs.readFileSync('/workspace/FM01_fixture.html'));}).listen(8765, '127.0.0.1');")
        return handle

    def authenticate(self, *arguments):
        subprocess.run(['docker', 'exec', '--detach', self.handle.container_name, 'node', '/workspace/FM01_fixture_server.js'], check=True)

class ReadOnlyRunner(OpenAIManagedRunner):
    @staticmethod
    def _load_instructions():
        return OpenAIManagedRunner._load_instructions() + '\nVerification constraint: Use only the supplied synthetic localhost applications. This is a read-only verification run: do not create or change any deal, proof, or art request. Local notebook updates are allowed.\n'

DEAL = '900001'
question = 'Are samples free, and do they include the printed design?'
client = {'role':'client','content_type':'text','content':question}
history = [
    {'role':'client', 'content_type':'text', 'content':'We are considering tees for fall club recruitment in November. I am unsure about the feel and fit. Still discussing internally, no action needed yet.'},
    {'role':'sasha', 'content_type':'text/html', 'content':'<p>Take your time discussing the Synthetic Cotton Tee with the club.</p>', 'delivery_status':'generated'},
]
prior_notes = "## Current decisions\n- Stage 1 follow-up sent: curated_design_idea\n"
TACTICS = re.findall(r"^\| `([a-z_]+)` \|", (ROOT / 'openai_managed/capabilities/followup/references/tactics.md').read_text(), re.MULTILINE)
exhausted = prior_notes + "".join(f"- Follow-up generated: {tactic}\n" for tactic in TACTICS if tactic != 'curated_design_idea')
# Follow-up checks give (as_of_date, days_since_client_reply); the skill infers the phase from them.
cases = [
    (f'quiet_{days}_days', history, None, False, 'follow_up', ('2026-10-13', days), prior_notes,
     'Sample value and shipping history unknown.')
    for days in (2, 6, 10, 15)
]
cases += [
    ('event_passed', history, None, False, 'follow_up', ('2026-12-15', 40), prior_notes, 'Unknown.'),
    ('empty_followup', [], None, False, 'no_reply', ('2026-10-13', None), prior_notes, 'Unknown.'),
    ('exhausted', history, None, False, 'no_reply', ('2026-10-13', 10), exhausted, 'Unknown.'),
    ('unanswered_client', [client], None, False, 'response', ('2026-10-13', 9), prior_notes, 'Unknown.'),
    ('message_during_followup', history, None, True, 'response', ('2026-10-13', 15), prior_notes, 'Unknown.'),
    ('message_during_empty_followup', [], None, True, 'response', ('2026-10-13', None), prior_notes, 'Unknown.'),
    ('ordinary_empty', [], None, False, 'outreach', None, prior_notes, 'Unknown.'),
    ('ordinary_handled', history, None, False, 'no_reply', None, prior_notes, 'Unknown.'),
    ('acknowledgement', history, 'Thanks, nothing else needed.', False, 'no_reply', None, prior_notes, 'Unknown.'),
]
for name, evidence in [
    ('sample_under_50_at_14_days', 'Verified sample value $49.99. Last sample sent exactly 14 days ago. No other sample sent since. Manager approval absent.'),
    ('sample_50', 'Verified sample value $50. Last sample sent 20 days ago. No other sample sent since. Manager approval absent.'),
    ('sample_recent', 'Verified sample value $20. Last sample sent 13 days ago. Manager approval absent.'),
    ('sample_missing_value', 'Sample value unknown. Verified no samples ever sent. Manager approval absent.'),
    ('sample_missing_history', 'Verified sample value $20. Sample shipment history unavailable. Manager approval absent.'),
]:
    cases.append((name, history, 'Can you offer me a free blank sample of this tee?', False, 'sample', None, prior_notes, evidence))
# Handoff to a person: the label after 'handoff:' names the situation for whoever reads the results.
cases += [
    ('handoff_external_item', history, 'Can you print our logo on shirts we already bought ourselves?', False,
     'handoff:external_item', None, prior_notes, 'Unknown.'),
    ('handoff_group_order', history, 'Can everyone in the chapter order their own size and pay separately?', False,
     'handoff:group_order', None, prior_notes, 'Unknown.'),
    ('handoff_client_upset', history, 'This is the third time you got this wrong. Get me a real person now.', False,
     'handoff:client_upset', None, prior_notes, 'Unknown.'),
    ('no_handoff_price_question', history, 'What is the price for 40 of these tees?', False,
     'response_any', None, prior_notes, 'Unknown.'),
]
selected = set(sys.argv[1:])
if selected:
    cases = [case for case in cases if case[0] in selected]
results_path = DIRECTORY / 'FM01_results.json'
results = json.loads(results_path.read_text()) if selected and results_path.exists() else []
for name, history, message, additional, expected, follow_up, notes_text, sample_evidence in cases:
    as_of_date, days_since_reply = follow_up or (None, None)
    directory = DIRECTORY / name
    storage = MemoryStorage()
    storage.history[DEAL] = [dict(entry, id=index + 1) for index, entry in enumerate(history)]
    storage.notes[DEAL] = notes_text
    first_tool = threading.Event()
    updates = []
    def progress(value):
        print(name + ': ' + value, flush=True)
        if 'tool step 1 started' in value:
            first_tool.set()
    runner = ReadOnlyRunner(OpenAI(api_key=configuration['OPENAI_API_KEY']), replace(settings, runs_directory=directory), sandbox_factory=FixtureSandbox, progress=ProgressReporter(progress), storage_factory=lambda: storage,
                            handoff_service_factory=lambda storage: type('NoOffers', (), {'offer_next': staticmethod(lambda deal_id: {'outcome': 'skipped_in_fm01'})})())
    runner.enable_additional_messages(updates.append)
    outputs = []
    errors = []
    def execute():
        try:
            outputs.append(runner.run(SashaTask(DEAL, 'FM01_' + name, application.deal_url(DEAL), message, turn_type='follow_up' if follow_up else None, as_of_date=as_of_date, days_since_client_reply=days_since_reply)))
        except Exception as error:
            errors.append(type(error).__name__ + ': ' + str(error))
    worker = threading.Thread(target=execute)
    worker.start()
    if additional:
        while worker.is_alive() and not (runner.accepting_messages and first_tool.is_set()):
            time.sleep(0.2)
        if runner.accepting_messages:
            runner.submit_message('FM01_first_inbound', question)
    worker.join()
    result = outputs[0].to_dict() if outputs else {'status':'failed','failure_message':str(errors)}
    reply = result.get('message_html', '')
    if expected.startswith('handoff:'):
        # The reason is the agent's own words; read it in the results for the expected situation.
        checks = {'handed_off': result['status'] == 'handoff',
                  'reason_given': bool(str(result.get('handoff_reason', '')).strip())}
    else:
        checks = {'completed':result['status']=='completed', 'expected_reply_presence':bool(reply.strip())==(expected!='no_reply')}
    if expected == 'response':
        checks['answers_sample_question'] = 'sample' in reply.lower() and ('blank' in reply.lower() or 'unprinted' in reply.lower() or 'not printed' in reply.lower())
    if expected == 'outreach':
        checks['introduces_sasha'] = 'sasha' in reply.lower() and 'account manager' in reply.lower()
    before_entries = follow_up_entries(notes_text)
    after_entries = follow_up_entries(storage.load_notes(DEAL))
    checks['prior_tactics_preserved'] = all(entry in after_entries for entry in before_entries)
    checks['new_tactic_count'] = len(after_entries) - len(before_entries) == (1 if expected == 'follow_up' else 0)
    if expected == 'follow_up':
        new = [entry for entry in after_entries if entry not in before_entries]
        checks['library_tactic_generated'] = len(new) == 1 and new[0].startswith('- Follow-up generated:') \
            and new[0].split(':', 1)[1].split()[0].strip('`') in TACTICS
    if additional:
        checks['additional_message_accepted'] = any(update.get('status')=='accepted' for update in updates)
    results = [result for result in results if result['case'] != name]
    results.append({'case':name,'expected':expected,'checks':checks,'result':result,'run_directory':str(runner.last_run_directory),'messages':updates,'history_after':storage.load_context(DEAL)[1], 'notes_after':storage.load_notes(DEAL), 'sample_evidence':sample_evidence})
    results_path.write_text(json.dumps(results, indent=2))
    print('CASE_RESULT ' + json.dumps({'case':name,'checks':checks,'result':result}), flush=True)

print('EVIDENCE ' + str(results_path), flush=True)
checked = [result for result in results if not selected or result['case'] in selected]
raise SystemExit(0 if checked and all(all(result['checks'].values()) for result in checked) else 1)
