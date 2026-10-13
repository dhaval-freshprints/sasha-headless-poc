import json
import tempfile
import unittest
from pathlib import Path

from openai_managed.conversation import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.runs_directory = Path(self.temporary_directory.name) / "runs"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_creates_an_empty_file_for_a_new_deal(self):
        store = ConversationStore(self.runs_directory, "303839")

        history = store.load()

        self.assertEqual(history, [])
        self.assertTrue(store.path.is_file())
        self.assertEqual(
            json.loads(store.path.read_text()),
            {"deal_id": "303839", "conversation_history": []},
        )

    def test_keeps_different_deals_in_different_files(self):
        first = ConversationStore(self.runs_directory, "303839")
        second = ConversationStore(self.runs_directory, "303840")

        first.append_completed_turn([], "First client", "First Sasha")
        second.append_completed_turn([], "Second client", "Second Sasha")

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.load()[0]["content"], "First client")
        self.assertEqual(second.load()[0]["content"], "Second client")

    def test_no_reply_preserves_client_messages_without_an_empty_draft(self):
        store = ConversationStore(self.runs_directory, "303839")
        previous = [{"role": "sasha", "content": "Earlier draft", "delivery_status": "generated"}]
        for reply in ("", "  "):
            with self.subTest(reply=reply):
                store.append_completed_turn(previous, "Thanks", reply, ["All set"])
                history = store.load()
                self.assertEqual(history[0], previous[0])
                self.assertEqual([item["content"] for item in history], ["Earlier draft", "Thanks", "All set"])
                self.assertEqual([item["role"] for item in history], ["sasha", "client", "client"])

    def test_no_reply_without_messages_keeps_history_unchanged(self):
        store = ConversationStore(self.runs_directory, "303839")
        store.append_completed_turn([], None, "")
        self.assertEqual(store.load(), [])

    def test_nonblank_reply_remains_generated(self):
        store = ConversationStore(self.runs_directory, "303839")
        store.append_completed_turn([], None, "<p>Hello</p>")
        self.assertEqual(store.load()[0]["delivery_status"], "generated")

    def test_rejects_an_unsafe_deal_id(self):
        with self.assertRaisesRegex(ValueError, "only digits"):
            ConversationStore(self.runs_directory, "../303839")


if __name__ == "__main__":
    unittest.main()
