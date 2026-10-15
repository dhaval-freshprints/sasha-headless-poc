import unittest
from account_manager_agent.notes import (
    MAX_NOTES_BYTES, prepare_notes, validate_notes, saved_attachment_urls,
    with_attachment_links, preserve_follow_up_history, follow_up_entries, used_follow_up_tactics,
)


class NotesTests(unittest.TestCase):
    def test_preserves_all_historical_tactics_without_accepting_new_ordinary_entries(self):
        previous = ("## Current decisions\n- Stage 1 follow-up sent: product_suggestion (navy tee)\n"
                    "- Stage 2 follow-up generated: stock_warning\n- Follow-up generated: check_on_product\n")
        candidate = "## Current decisions\n- Follow-up generated: check_on_price\n"
        result = preserve_follow_up_history(previous, candidate, False)
        self.assertEqual(follow_up_entries(result), follow_up_entries(previous))
        self.assertEqual(used_follow_up_tactics(previous),
                         ["product_suggestion", "stock_warning", "check_on_product"])

    def test_rejects_reused_tactics_stage_format_sent_claims_and_multiple_new_tactics(self):
        previous = "## Current decisions\n- Stage 1 follow-up sent: product_suggestion\n"
        for entries in [
            "- Follow-up generated: product_suggestion",  # Used under a stage: still used.
            "- Stage 2 follow-up generated: stock_warning",  # Old format is not accepted for new entries.
            "- Follow-up sent: offer_art_team",
            "- Follow-up generated: offer_art_team\n- Follow-up generated: expertise_value_add",
        ]:
            with self.subTest(entries=entries):
                result = preserve_follow_up_history(previous, "## Current decisions\n" + entries, True)
                self.assertEqual(follow_up_entries(result), follow_up_entries(previous))

    def test_accepts_one_generated_tactic_and_preserves_history_if_section_is_missing(self):
        previous = "## Current decisions\n- Stage 1 follow-up sent: product_suggestion\n"
        entry = "- Follow-up generated: offer_art_team"
        result = preserve_follow_up_history(previous, "## Client preferences\n- Navy.\n" + entry, True)
        self.assertEqual(follow_up_entries(result), follow_up_entries(previous) + [entry])
        self.assertIn("## Current decisions", result)

    def test_ignores_links_outside_attachment_section(self):
        notes = (
            "## Current decisions\n- proof.svg: <https://example.test/not-artwork.svg>\n"
            "## Attachments\n- logo.svg: <https://example.test/logo.svg>\n"
            "## Needs verification\n- page.svg: <https://example.test/another.svg>\n"
        )
        self.assertEqual(saved_attachment_urls(notes), ["https://example.test/logo.svg"])

    def test_attachment_section_does_not_delete_other_sections(self):
        notes = (
            "## Attachments\n- old.svg: <https://example.test/old.svg>\n"
            "## Unfinished work\n- Await quantity.\n"
        )
        updated = with_attachment_links(notes, ["https://example.test/new.svg"])
        self.assertIn("- Await quantity.", updated)
        self.assertNotIn("old.svg", updated)
        self.assertEqual(updated.count("## Attachments"), 1)

    def test_empty_null_and_oversize_notes_are_rejected(self):
        for value in ('', ' ', 'bad\x00notes', 'é' * (MAX_NOTES_BYTES // 2 + 1)):
            with self.subTest(value=value[:10]), self.assertRaises(ValueError):
                prepare_notes('Previous', value, [], False)

    def test_attachment_links_are_preserved_and_validated_in_memory(self):
        url = 'https://example.test/logo.svg?signature=abc%2Fdef'
        candidate = 'New notes\n## Attachments\n- fake.svg: <https://example.test/fake.svg>\n'
        notes = prepare_notes('Previous', candidate, [url], False)
        self.assertEqual(saved_attachment_urls(notes), [url])
        with self.assertRaises(ValueError):
            prepare_notes('', 'x' * MAX_NOTES_BYTES, [url], False)
