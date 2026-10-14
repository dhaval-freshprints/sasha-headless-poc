"""In-memory DB boundary for orchestration tests; never contacts configured databases."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch
from sasha_database.storage import validate_deal_id


class MemoryStorage:
    def __init__(self):
        self.notes = {}
        self.history = {}
        self.statuses = {}
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

    def complete_run(self, deal_id, run_id, previous_notes, notes, message_html, convo_type):
        self.completed.append((deal_id, run_id, notes, message_html, convo_type))
        self.notes[deal_id] = notes
        history = self.history.setdefault(deal_id, [])
        if message_html.strip():
            history.append({'id': len(history)+1, 'role': 'sasha', 'content': message_html,
                            'delivery_status': 'generated', 'content_type': 'text/html'})


def setup_database(test):
    test.database = MemoryStorage()
    patcher = patch('webapp.jobs.SashaStorage', return_value=test.database)
    patcher.start()
    test.addCleanup(patcher.stop)
