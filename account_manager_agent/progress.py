"""Optional progress output for a managed Sasha run."""

from collections.abc import Callable


class ProgressReporter:
    def __init__(self, write: Callable[[str], None] | None = None) -> None:
        self.write = write

    def report(self, message: str) -> None:
        if self.write is not None:
            self.write(message)
