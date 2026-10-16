"""Independent sample Freshdesk worker behavior."""

import threading
import unittest
from unittest.mock import patch

from webapp.app import _start_sample_freshdesk_worker


class SampleSyncWorkerTests(unittest.TestCase):
    def test_worker_syncs_immediately_before_waiting_for_interval(self):
        stopped = threading.Event()
        called = threading.Event()

        def sync():
            called.set()
            stopped.set()
            return {"configured": True, "deals": []}

        with patch.dict("os.environ", {"SAMPLE_FRESHDESK_SYNC_SECONDS": "3600"}):
            _start_sample_freshdesk_worker(sync, stopped)

        self.assertTrue(called.wait(timeout=1))


if __name__ == "__main__":
    unittest.main()
