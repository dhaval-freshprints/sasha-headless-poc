"""Bound work while reserving a small read-only window to check an uncertain save."""

import time
from dataclasses import dataclass, field

import config
from tool_policy import VERIFICATION_TOOLS


@dataclass
class RunLimits:
    started: float = field(default_factory=time.monotonic)
    actions: int = 0
    verification_actions: int = 0
    verification_started: float | None = None
    reason: str = ""

    def update(self) -> None:
        if self.reason:
            return
        if self.actions >= config.MAX_ACTIONS:
            self.reason = "action_limit"
        elif time.monotonic() - self.started >= config.MAX_TURN_SECONDS:
            self.reason = "time_limit"
        if self.reason:
            self.verification_started = time.monotonic()

    def start_verification(self, reason: str) -> None:
        if not self.reason:
            self.reason = reason
            self.verification_started = time.monotonic()

    def seconds_left(self) -> float:
        self.update()
        if self.verification_started is not None:
            return max(0.0, config.VERIFICATION_SECONDS - (time.monotonic() - self.verification_started))
        return max(0.0, config.MAX_TURN_SECONDS - (time.monotonic() - self.started))

    def exhausted(self) -> bool:
        return self.seconds_left() <= 0 or self.verification_actions >= config.MAX_VERIFICATION_ACTIONS

    def reserve_action(self, name: str) -> str:
        self.update()
        if self.reason:
            if name not in VERIFICATION_TOOLS or self.exhausted():
                return "The work limit is reached. Only bounded read-only verification and a final reply are allowed."
            self.verification_actions += 1
        self.actions += 1
        return ""
