import tempfile
import unittest
from pathlib import Path

from webapp.deals import DealStore


class DealStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_adds_empty_deal_and_reloads_it_from_disk(self):
        store = DealStore(self.root)
        created = store.add("303839")

        reloaded = DealStore(self.root).get("303839")

        self.assertEqual(reloaded.deal_id, created.deal_id)
        self.assertEqual(reloaded.created_at, created.created_at)

    def test_adding_existing_deal_is_idempotent(self):
        store = DealStore(self.root)
        first = store.add("303839")
        second = store.add("303839")

        self.assertEqual(first, second)
        self.assertEqual(len(store.list_all()), 1)

    def test_rejects_invalid_deal_id(self):
        store = DealStore(self.root)

        with self.assertRaisesRegex(ValueError, "only digits"):
            store.add("deal-303839")


if __name__ == "__main__":
    unittest.main()
