from __future__ import annotations

from dataclasses import dataclass

from fireclaw_core.skills import Skill


@dataclass(frozen=True)
class FailureDecision:
    action: str
    failure_category: str | None = None
    operator_action: str | None = None


class FailurePolicy:
    def decide(self, *, skill: Skill, attempt_number: int) -> FailureDecision:
        max_attempts = max(1, skill.max_attempts)
        if attempt_number < max_attempts:
            return FailureDecision(action="retry")
        return FailureDecision(
            action="stop_and_escalate",
            failure_category="recoverable_exhausted",
            operator_action="escalate",
        )
