"""Follow-up trigger and bookkeeping in sasha_database.followup_schedule.
Account Manager chooses every follow-up day and time; code never does."""
import unittest
from datetime import datetime

from sasha_database import followup_schedule as schedule_rules

NOW = datetime(2026, 10, 14, 15)


class CheckTimeTests(unittest.TestCase):
    def test_new_york_time_becomes_utc(self):
        self.assertEqual(schedule_rules.parse_check_time("2026-10-20T10:30"), datetime(2026, 10, 20, 14, 30))
        self.assertEqual(schedule_rules.parse_check_time("2026-12-20T10:30"), datetime(2026, 12, 20, 15, 30))

    def test_a_bare_day_is_refused(self):
        with self.assertRaisesRegex(ValueError, "date and time"):
            schedule_rules.parse_check_time("2026-10-20")


class DueTests(unittest.TestCase):
    def test_due_once_its_hour_arrives_and_never_twice_a_day(self):
        scheduled = datetime(2026, 10, 14, 15, 40)  # 11:40 New York
        self.assertFalse(schedule_rules.is_due(scheduled, datetime(2026, 10, 14, 14, 59), None, {}))
        self.assertTrue(schedule_rules.is_due(scheduled, datetime(2026, 10, 14, 15, 15), None, {}))
        self.assertTrue(schedule_rules.is_due(scheduled, datetime(2026, 10, 16, 13, 15), None, {}))  # catch-up
        self.assertFalse(schedule_rules.is_due(scheduled, datetime(2026, 10, 14, 16, 15),
                                               datetime(2026, 10, 14, 12), {}))
        self.assertFalse(schedule_rules.is_due(scheduled, datetime(2026, 10, 14, 16, 15), None,
                                               {schedule_rules.CLAIM_KEY: "2026-10-14"}))


class TransitionTests(unittest.TestCase):
    def row(self, **changes):
        return {"status": "active", "first_message_timestamp": datetime(2026, 10, 1), "followup_count": 1,
                "next_followup_date": datetime(2026, 10, 14, 14), "followup_metadata": {}, **changes}

    def test_never_picks_a_date(self):
        for event in schedule_rules.EVENTS:
            updates = schedule_rules.transition(self.row(), event, NOW, agent_set_date=True) or {}
            self.assertNotIn("next_followup_date", updates, event)

    def test_follow_up_turns_without_a_chosen_check_pause(self):
        for event in ("outreach", "followup_sent", "followup_unsent"):
            updates = schedule_rules.transition(self.row(), event, NOW)
            self.assertEqual((updates["status"], updates["next_followup_date"]), ("paused", None), event)

    def test_a_client_reply_keeps_the_plan_and_records_the_time(self):
        updates = schedule_rules.transition(self.row(), "client_reply_answered", NOW, reply_at=NOW)
        self.assertNotIn("status", updates)
        self.assertEqual(len(schedule_rules.reply_timestamps(updates["followup_metadata"])), 1)

    def test_sends_are_counted_and_the_day_claim_cleared(self):
        row = self.row(followup_metadata={schedule_rules.CLAIM_KEY: "2026-10-14"})
        updates = schedule_rules.transition(row, "followup_sent", NOW, channel="sms", agent_set_date=True)
        self.assertEqual((updates["followup_count"], updates["last_channel_used"]), (2, "sms"))
        self.assertNotIn(schedule_rules.CLAIM_KEY, updates["followup_metadata"])

    def test_stopped_and_completed_rows_are_left_alone(self):
        for status in ("stopped", "completed"):
            self.assertIsNone(schedule_rules.transition(self.row(status=status), "client_reply_answered", NOW))


if __name__ == "__main__":
    unittest.main()
