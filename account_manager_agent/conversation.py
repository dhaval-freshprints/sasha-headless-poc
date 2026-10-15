"""DB-backed conversation reads for callers outside the runner."""
import re
from sasha_database.storage import SashaStorage

DEAL_ID = re.compile(r"^[0-9]+$")


class ConversationStore:
    def __init__(self, deal_id: str, storage=None):
        self.deal_id = deal_id
        self.storage = storage or SashaStorage()

    def load(self):
        return self.storage.load_context(self.deal_id)[1]
