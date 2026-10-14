import json
import queue
import threading
import time
import unittest
from types import SimpleNamespace

from openai_managed.conversation import ConversationStore
from openai_managed.additional_messages import AdditionalMessageInbox
from openai_managed.task import SashaTask
from openai_managed.notes import NotesStore, NOTES_OUTPUT_NAME, follow_up_entries
from tests import test_openai_managed_runner as runner_tests


class ControlledStream:
    def __init__(self):
        self.queue = queue.Queue()

    def __iter__(self):
        while True:
            event = self.queue.get(timeout=5)
            if event is None:
                return
            yield event

    def close(self):
        self.queue.put(None)


class ControlledSession:
    def __init__(self, sessions):
        self.sessions = sessions
        self.stream = ControlledStream()
        self.sent = []
        self.items = []
        self.turns = {"turn-1": "in_progress"}
        self.initial_sent = threading.Event()
        self.additional_message_sent = threading.Event()
        self.before_additional_message = None
        self.delivery_error = None
        self.next_turn = "turn-1"
        sessions.events = SimpleNamespace(stream=lambda session_id: self.stream, create=self.send)
        sessions.items = SimpleNamespace(list=lambda *args, **kwargs: list(self.items))
        sessions.turns = SimpleNamespace(retrieve=self.retrieve_turn)
        self.stream.queue.put({"type": "agent.session.environment.connected"})

    def send(self, session_id, events, idempotency_key=None):
        if events == [runner_tests.CANCEL_EVENT]:
            self.sessions.operations.append("cancel")
            return
        self.sent.append((session_id, idempotency_key, events))
        if len(self.sent) == 1:
            self.stream.queue.put({"type": "agent.session.turn.created", "turn": {"id": "turn-1"}})
            self.initial_sent.set()
            return
        if self.before_additional_message:
            self.before_additional_message()
        if self.delivery_error:
            error = self.delivery_error
            self.delivery_error = None
            raise error
        turn_id = self.next_turn
        self.turns[turn_id] = "in_progress"
        self.items.append({"type": "message", "role": "user", "turn_id": turn_id, "content": events[0]["input"][0]["content"]})
        self.stream.queue.put({"type": "agent.session.turn.created", "turn": {"id": turn_id}})
        self.additional_message_sent.set()

    def retrieve_turn(self, turn_id, session_id):
        return SimpleNamespace(id=turn_id, status=self.turns[turn_id], error=None)

    def complete(self, turn_id, answer):
        self.turns[turn_id] = "completed"
        result = {"deal_id": "303839", "status": "completed", "message_html": answer}
        self.items.append({"role": "assistant", "phase": "final_answer", "turn_id": turn_id, "content": [{"type": "output_text", "text": json.dumps(result)}]})
        self.stream.queue.put({"type": "agent.session.turn.completed", "turn_id": turn_id, "turn": {"id": turn_id, "subagent_id": None}})


def wait_until(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Condition did not become true")


class AdditionalMessageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runner_tests.ManagedRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.runner, self.sessions, self.sandboxes = self.fixture._make_runner([], turn_timeout=4)
        self.control = ControlledSession(self.sessions)
        self.updates = []
        self.runner.enable_additional_messages(self.updates.append)
        self.results = []
        self.task = SashaTask("303839", "original", runner_tests.TASK_URL, client_message="Find navy shirts for 40")
        self.worker = threading.Thread(target=lambda: self.results.append(self.runner.run(self.task)))

    def start(self):
        self.worker.start()
        self.addCleanup(self.worker.join, 5)
        wait_until(lambda: self.runner.accepting_messages)

    def finish(self):
        self.worker.join(5)
        self.assertFalse(self.worker.is_alive())
        return self.results[0]

    def followup_task(self):
        self.task = SashaTask("303839", "follow-up", runner_tests.TASK_URL,
                             turn_type="follow_up", as_of_date="2026-10-13")

    def test_followup_completion_race_selects_response_and_discards_proposed_tactic(self):
        self.followup_task()
        notes = NotesStore(self.runner.settings.runs_directory, "303839")
        notes.path.parent.mkdir(parents=True)
        prior = "- Stage 1 follow-up generated: offer_art_team"  # Older entry format.
        notes.path.write_text("## Current decisions\n" + prior)
        original_publish = self.runner._publish_notes
        def publish(store, task, result):
            candidate = self.runner.last_run_directory / "workspace" / NOTES_OUTPUT_NAME
            candidate.write_text("## Current decisions\n- Follow-up generated: close_the_loop")
            original_publish(store, task, result)
        self.runner._publish_notes = publish
        self.test_completion_during_delivery_waits_for_new_turn_and_uses_its_answer()
        self.assertEqual(follow_up_entries(notes.load()), [prior])

    def test_message_attached_after_same_turn_answer_continues_before_completing(self):
        self.followup_task()
        self.control.before_additional_message = lambda: self.control.complete("turn-1", "")
        self.start()
        self.runner.submit_message("late-inbound", "Are samples free?")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        # Simulate the API's accepted message remaining on a completed turn.
        self.control.turns["turn-1"] = "completed"
        self.control.stream.queue.put({"type": "agent.session.turn.completed", "turn_id": "turn-1"})
        self.control.before_additional_message = None
        self.control.next_turn = "turn-2"
        wait_until(lambda: len(self.control.sent) == 3)
        self.assertTrue(self.worker.is_alive())
        self.assertEqual(self.control.sent[-1][1], "completion-continuation:late-inbound")
        self.control.complete("turn-2", "<p>Samples are free and blank.</p>")
        self.assertEqual(self.finish().message_html, "<p>Samples are free and blank.</p>")
        self.assertEqual(len(self.sandboxes), 1)
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual([item["content"] for item in history],
                         ["Are samples free?", "<p>Samples are free and blank.</p>"])
        self.assertEqual(len(self.runner.additional_messages.messages), 1)

    def test_followup_deferred_delivery_reuses_session(self):
        self.followup_task()
        self.test_unsteerable_message_waits_then_reuses_same_submission_key()
        self.assertEqual(len(self.sandboxes), 1)

    def test_followup_message_during_final_check_prevents_cleanup(self):
        self.followup_task()
        self.test_message_arriving_during_final_check_prevents_cleanup()

    def test_message_uses_existing_session_and_is_saved_in_history(self):
        self.start()
        self.runner.submit_message("correction-1", "Actually black and quantity 60")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        self.control.complete("turn-1", "<p>Black shirts for 60</p>")
        result = self.finish()
        self.assertEqual(result.status, "completed")
        self.assertEqual([sent[0] for sent in self.control.sent], ["session-1", "session-1"])
        self.assertEqual(self.control.sent[1][1], "correction-1")
        self.assertEqual(len(self.sandboxes), 1)
        self.assertEqual(self.sessions.operations, ["delete", "stop"])
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual([item["content"] for item in history], [self.task.client_message, "Actually black and quantity 60", result.message_html])
        self.assertEqual(self.updates[-1]["turn_id"], "turn-1")
        self.assertFalse(self.runner.accepting_messages)

    def test_first_client_message_during_outreach_uses_same_session(self):
        self.task = SashaTask("303839", "original", runner_tests.TASK_URL)
        self.start()
        self.runner.submit_message("first-client", "Do you offer samples?")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        prompt = self.control.sent[-1][2][0]["input"][0]["content"][0]["text"]
        self.assertIn("reconsider", prompt)
        self.assertNotIn("Keep the selected workflow", prompt)
        self.control.complete("turn-1", "<p>Samples are free and blank.</p>")
        result = self.finish()
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.sandboxes), 1)
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual([item["content"] for item in history], ["Do you offer samples?", result.message_html])

    def test_successful_no_reply_keeps_received_messages(self):
        self.task = SashaTask("303839", "original", runner_tests.TASK_URL, "Thanks")
        self.start()
        self.runner.submit_message("acknowledgement", "All set")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        self.control.complete("turn-1", "")
        result = self.finish()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.message_html, "")
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual([item["content"] for item in history], ["Thanks", "All set"])
        self.assertTrue(all(item["role"] == "client" for item in history))

    def test_completion_during_delivery_waits_for_new_turn_and_uses_its_answer(self):
        sending = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        self.control.before_additional_message = lambda: (sending.set(), release.wait(3))
        self.control.next_turn = "turn-2"
        self.start()
        self.runner.submit_message("correction-2", "Use black instead")
        self.assertTrue(sending.wait(2))
        self.control.complete("turn-1", "<p>Old navy answer</p>")
        release.set()
        self.assertTrue(self.control.additional_message_sent.wait(2))
        time.sleep(0.3)
        self.assertTrue(self.worker.is_alive())
        self.assertFalse(self.sandboxes[0].stopped)
        self.control.complete("turn-2", "<p>New black answer</p>")
        result = self.finish()
        self.assertEqual(result.message_html, "<p>New black answer</p>")
        self.assertEqual(self.runner.final_turn_id, "turn-2")

    def test_unsteerable_message_waits_then_reuses_same_submission_key(self):
        error = RuntimeError("Cannot steer now")
        error.code = "active_turn_not_steerable"
        self.control.delivery_error = error
        self.start()
        self.runner.submit_message("correction-3", "Use black")
        wait_until(lambda: any(update["error"] for update in self.updates))
        self.assertEqual(len(self.control.sent), 2)
        self.control.next_turn = "turn-2"
        self.control.complete("turn-1", "<p>Navy</p>")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        self.control.complete("turn-2", "<p>Black</p>")
        self.assertEqual(self.finish().status, "completed")
        self.assertEqual([sent[1] for sent in self.control.sent[1:]], ["correction-3", "correction-3"])

    def test_uncertain_delivery_fails_run_and_is_visible(self):
        self.control.delivery_error = TimeoutError("Lost response")
        self.start()
        self.runner.submit_message("correction-4", "Use black")
        self.assertEqual(self.finish().status, "failed")
        self.assertEqual(self.updates[-1]["status"], "uncertain")
        self.assertIn("cancel", self.sessions.operations)
        self.assertTrue((self.runner.last_run_directory / "workspace" / "session-items.json").is_file())

    def test_multiple_messages_are_delivered_and_saved_in_order(self):
        self.start()
        self.runner.submit_message("color", "Use black")
        self.runner.submit_message("quantity", "Make it 60")
        wait_until(lambda: len(self.control.items) == 2)
        self.control.complete("turn-1", "<p>Black, 60</p>")
        self.assertEqual(self.finish().status, "completed")
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual([item["content"] for item in history[1:3]], ["Use black", "Make it 60"])
        self.assertEqual([sent[1] for sent in self.control.sent[1:]], ["color", "quantity"])

    def test_rejected_delivery_is_not_added_to_conversation(self):
        error = RuntimeError("Invalid input")
        error.status_code = 400
        self.control.delivery_error = error
        self.start()
        self.runner.submit_message("rejected", "Use black")
        wait_until(lambda: any(update["status"] == "failed" for update in self.updates))
        self.control.complete("turn-1", "<p>Original answer</p>")
        self.assertEqual(self.finish().status, "completed")
        history = ConversationStore(self.runner.settings.runs_directory, "303839").load()
        self.assertEqual(len(history), 2)

    def test_message_arriving_during_final_check_prevents_cleanup(self):
        original_check = self.runner._additional_message_turn_finished
        arrived = threading.Event()

        def receive_during_check(events):
            finished = original_check(events)
            if not arrived.is_set():
                self.runner.submit_message("last-second", "Use black")
                arrived.set()
            return finished

        self.runner._additional_message_turn_finished = receive_during_check
        self.control.next_turn = "turn-2"
        self.start()
        self.control.complete("turn-1", "<p>Navy</p>")
        self.assertTrue(self.control.additional_message_sent.wait(2))
        self.assertFalse(self.sandboxes[0].stopped)
        self.control.complete("turn-2", "<p>Black</p>")
        self.assertEqual(self.finish().message_html, "<p>Black</p>")

    def test_subagent_completion_does_not_finish_root_run(self):
        self.start()
        self.control.stream.queue.put({"type": "agent.session.turn.completed", "turn": {"id": "child", "subagent_id": "child-agent"}})
        time.sleep(0.3)
        self.assertTrue(self.worker.is_alive())
        self.control.complete("turn-1", "<p>Root answer</p>")
        self.assertEqual(self.finish().status, "completed")

    def test_inbox_closure_rejects_late_messages(self):
        inbox = AdditionalMessageInbox(lambda message: None)
        inbox.open()
        inbox.submit("one", "First")
        self.assertFalse(inbox.close_if_empty())
        self.assertEqual(inbox.take()["text"], "First")
        self.assertTrue(inbox.close_if_empty())
        with self.assertRaises(ValueError):
            inbox.submit("two", "Too late")
