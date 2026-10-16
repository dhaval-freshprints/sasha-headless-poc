"""In-memory DB boundary for orchestration tests; never contacts configured databases."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from datetime import datetime
from sasha_database import followup_schedule as schedule_rules
from sasha_database.storage import describe_schedule, validate_deal_id
from sasha_database.messages import sms_body, utc_now


class MemoryStorage:
    def __init__(self):
        self.notes = {}
        self.history = {}
        self.statuses = {}
        self.status_reasons = {}
        self.followups = {}
        self.prepared = []
        self.completed = []

    def sender_email(self):
        return 'sasha@example.com'

    def prepare_run(self, deal_id, run_id, messages=()):
        validate_deal_id(deal_id)
        self.statuses.setdefault(deal_id, 'active')
        self.prepared.append((deal_id, run_id, messages))
        history = self.history.setdefault(deal_id, [])
        stored = []
        for message in messages:
            if message.row_id is None:
                message = replace(message, sender_name=message.sender_name or 'client@example.com', row_id=len(history)+1)
                history.append({'id': message.row_id, 'role': 'client', 'content': message.body,
                                'content_type': message.content_type, 'sender_name': message.sender_name,
                                'message_type': message.message_type, 'convo_type': message.convo_type,
                                'date': message.timestamp().date().isoformat()})
            stored.append(message)
        return tuple(stored)

    def load_context(self, deal_id):
        return self.load_notes(deal_id), deepcopy(self.history.get(deal_id, []))

    def load_notes(self, deal_id):
        return self.notes.get(deal_id, '')

    def deal_state(self, deal_id):
        return {'status': self.statuses.get(deal_id, 'active'),
                'status_reason': self.status_reasons.get(deal_id, ''),
                'followup': self.followups.get(deal_id)}

    def claim_due_followups(self, now=None, limit=50):
        now = now or datetime(9999, 1, 1)
        due = []
        for deal_id, row in self.followups.items():
            when = row.get('next_followup_date')
            when = datetime.fromisoformat(when) if isinstance(when, str) else when
            if (row.get('status') == 'active'
                    and self.statuses.get(deal_id, 'active') not in ('opt_out', 'handoff_needed')
                    and schedule_rules.is_due(when, now, row.get('last_followup_timestamp'), row.get('followup_metadata'))):
                meta = dict(row.get('followup_metadata') or {})
                meta[schedule_rules.CLAIM_KEY] = schedule_rules.business_date(now).isoformat()
                row['followup_metadata'] = meta
                due.append(int(deal_id))
        return due[:limit]

    def release_followup_claim(self, deal_id):
        row = self.followups.get(str(deal_id))
        if row:
            (row.get('followup_metadata') or {}).pop(schedule_rules.CLAIM_KEY, None)

    def schedule_followup(self, deal_id, event, **options):
        row = self.followups.get(deal_id)
        updates = schedule_rules.transition(row, event, utc_now(), **options)
        if updates is None:
            return 'unchanged'
        self.followups[deal_id] = {**(row or {}), **updates}
        return describe_schedule(updates)

    def stop_followups(self, deal_id, reason):
        if deal_id in self.followups:
            self.followups[deal_id].update(status='stopped', stop_reason=reason, next_followup_date=None)

    def apply_followup_schedule(self, deal_id, state):
        stop = ('opted_out' if state['status'] == 'opt_out' else 'completed' if state['status'] == 'completed'
                else 'opt_out_winback' if state.get('crm_lost_reason') else None)
        if stop:
            self.followups[deal_id] = {'status': 'stopped', 'stop_reason': stop}
            return f'stopped ({stop})'
        if state.get('next_check_on'):
            row = self.followups.get(deal_id) or {'first_message_timestamp': utc_now(), 'followup_count': 0}
            self.followups[deal_id] = {**row, 'status': 'active', 'stop_reason': None,
                                       'next_followup_date': schedule_rules.parse_check_time(state['next_check_on']),
                                       'scheduled_followup_reason': state['reason']}
            return f"scheduled for {state['next_check_on']} New York"
        return 'unchanged'

    def deal_status(self, deal_id):
        return self.statuses.get(deal_id, 'active'), self.status_reasons.get(deal_id, '')

    def mark_handoff(self, deal_id, handoff_reason):
        if self.statuses.get(deal_id) == 'handoff_needed':
            return False
        self.statuses[deal_id], self.status_reasons[deal_id] = 'handoff_needed', handoff_reason
        return True

    def complete_run(self, deal_id, run_id, previous_notes, notes, message_html, convo_type,
                     handoff_reason=None, deal_state=None, sms_messages=None):
        self.completed.append((deal_id, run_id, notes, message_html, convo_type, sms_messages))
        self.notes[deal_id] = notes
        if handoff_reason is not None:
            self.statuses[deal_id], self.status_reasons[deal_id] = 'handoff_needed', handoff_reason
            if deal_id in self.followups:
                self.followups[deal_id].update(status='stopped', stop_reason='handoff', next_followup_date=None)
        elif deal_state is not None and self.statuses.get(deal_id) != 'handoff_needed':
            self.statuses[deal_id] = deal_state['status']
            self.status_reasons[deal_id] = deal_state['reason']
        history = self.history.setdefault(deal_id, [])
        if message_html.strip():
            history.append({'id': len(history)+1, 'role': 'sasha', 'content': message_html,
                            'run_id': run_id, 'message_type': 'EMAIL',
                            'delivery_status': 'generated', 'content_type': 'text/html'})
        if sms_messages:
            history.append({'id': len(history)+1, 'role': 'sasha', 'content': sms_body(sms_messages),
                            'run_id': run_id, 'message_type': 'SMS', 'sms_messages': deepcopy(sms_messages),
                            'delivery_status': 'generated', 'content_type': 'text'})


def setup_database(test):
    test.database = MemoryStorage()
    patcher = patch('webapp.jobs.SashaStorage', return_value=test.database)
    patcher.start()
    test.addCleanup(patcher.stop)
