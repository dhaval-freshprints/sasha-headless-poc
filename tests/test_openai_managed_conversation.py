import unittest
from unittest.mock import Mock
from openai_managed.conversation import ConversationStore
from openai_managed.notes import NotesStore


class DatabaseStoreTests(unittest.TestCase):
    def test_stores_read_database_without_file_fallback(self):
        database = Mock()
        database.load_context.return_value = ('notes', [{'id': 1, 'content': 'hello'}])
        database.load_notes.return_value = 'notes'
        self.assertEqual(ConversationStore('42', database).load(), [{'id': 1, 'content': 'hello'}])
        self.assertEqual(NotesStore('42', database).load(), 'notes')
        database.load_context.assert_called_once_with('42')
        database.load_notes.assert_called_once_with('42')

    def test_database_errors_propagate(self):
        database = Mock()
        database.load_context.side_effect = RuntimeError('database unavailable')
        with self.assertRaises(RuntimeError):
            ConversationStore('42', database).load()
