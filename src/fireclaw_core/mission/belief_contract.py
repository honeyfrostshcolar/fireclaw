from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fireclaw_core.mission.mission_state import MissionStateSnapshot
from fireclaw_core.mission.task_graph import (
    MissionBeliefRequirement,
    MissionTaskNode,
)
from fireclaw_core.mission.world_state_belief import WorldStateBelief


@dataclass(frozen=True)
class BeliefRequirementCheck:
    belief_id: str
    passed: bool
    reason_code: str
    message: str
    observed_status: str | None = None
    observed_value: str | int | float | bool | None = None
    observed_confidence: float | None = None
    observed_age_seconds: float | None = None
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "belief_id": self.belief_id,
            "passed": self.passed,
            "reason_code": self.reason_code,
            "message": self.message,
            "evidence_ids": list(self.evidence_ids),
        }
        optional = {
            "observed_status": self.observed_status,
            "observed_value": self.observed_value,
            "observed_confidence": self.observed_confidence,
            "observed_age_seconds": self.observed_age_seconds,
        }
        result.update({
            key: value for key, value in optional.items() if value is not None
        })
        return result


@dataclass(frozen=True)
class MissionBeliefGateResult:
    allowed: bool
    mission_id: str
    plan_id: str
    node_id: str
    snapshot_id: str
    checks: tuple[BeliefRequirementCheck, ...]

    @property
    def failed_belief_ids(self) -> tuple[str, ...]:
        return tuple(
            check.belief_id for check in self.checks if not check.passed
        )

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            evidence_id
            for check in self.checks
            for evidence_id in check.evidence_ids
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mission_id": self.mission_id,
            "plan_id": self.plan_id,
            "node_id": self.node_id,
            "snapshot_id": self.snapshot_id,
            "failed_belief_ids": list(self.failed_belief_ids),
            "evidence_ids": list(self.evidence_ids),
            "checks": [check.to_dict() for check in self.checks],
        }


class MissionBeliefGate:
    """Revalidate compiled world assumptions immediately before dispatch."""

    def evaluate(
        self,
        *,
        mission_id: str,
        plan_id: str,
        node: MissionTaskNode,
        snapshot: MissionStateSnapshot,
    ) -> MissionBeliefGateResult:
        if snapshot.mission_id != mission_id:
            raise ValueError(
                "Belief gate snapshot does not belong to the scheduled mission."
            )
        beliefs = {
            belief.belief_id: belief
            for belief in snapshot.environment_beliefs
        }
        checks = tuple(
            self._check(
                requirement,
                beliefs.get(requirement.belief_id),
                captured_at=snapshot.captured_at,
            )
            for requirement in node.belief_requirements
        )
        return MissionBeliefGateResult(
            allowed=all(check.passed for check in checks),
            mission_id=mission_id,
            plan_id=plan_id,
            node_id=node.node_id,
            snapshot_id=snapshot.snapshot_id,
            checks=checks,
        )

    def _check(
        self,
        requirement: MissionBeliefRequirement,
        belief: WorldStateBelief | None,
        *,
        captured_at: str,
    ) -> BeliefRequirementCheck:
        if belief is None:
            return self._failed(
                requirement,
                reason_code="belief_missing",
                message="Required world-state belief is missing.",
            )
        observed = {
            "observed_status": belief.status,
            "observed_value": belief.value,
            "observed_confidence": belief.confidence,
            "evidence_ids": belief.evidence_ids,
        }
        if (
            belief.subject_id != requirement.subject_id
            or belief.kind != requirement.kind
        ):
            return self._failed(
                requirement,
                reason_code="belief_identity_changed",
                message="Required world-state belief identity changed.",
                **observed,
            )
        if belief.status != requirement.required_status:
            return self._failed(
                requirement,
                reason_code=f"belief_{belief.status}",
                message=(
                    "Required world-state belief is not confirmed at dispatch."
                ),
                **observed,
            )
        if not _same_scalar(belief.value, requirement.expected_value):
            return self._failed(
                requirement,
                reason_code="belief_value_changed",
                message="Required world-state belief no longer has the planned value.",
                **observed,
            )
        if belief.confidence < requirement.minimum_confidence:
            return self._failed(
                requirement,
                reason_code="belief_confidence_below_minimum",
                message="Required world-state belief confidence is too low.",
                **observed,
            )
        age_seconds = _age_seconds(
            captured_at=captured_at,
            observed_at=belief.observed_at,
        )
        if age_seconds is None or age_seconds < 0:
            return self._failed(
                requirement,
                reason_code="belief_timestamp_invalid",
                message="Required world-state belief has an invalid timestamp.",
                **observed,
            )
        if age_seconds > requirement.maximum_age_seconds:
            return self._failed(
                requirement,
                reason_code="belief_too_old",
                message="Required world-state belief is too old for dispatch.",
                observed_age_seconds=age_seconds,
                **observed,
            )
        return BeliefRequirementCheck(
            belief_id=requirement.belief_id,
            passed=True,
            reason_code="belief_requirement_satisfied",
            message="Required world-state belief is valid for dispatch.",
            observed_age_seconds=age_seconds,
            **observed,
        )

    @staticmethod
    def _failed(
        requirement: MissionBeliefRequirement,
        *,
        reason_code: str,
        message: str,
        observed_status: str | None = None,
        observed_value: str | int | float | bool | None = None,
        observed_confidence: float | None = None,
        observed_age_seconds: float | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> BeliefRequirementCheck:
        return BeliefRequirementCheck(
            belief_id=requirement.belief_id,
            passed=False,
            reason_code=reason_code,
            message=message,
            observed_status=observed_status,
            observed_value=observed_value,
            observed_confidence=observed_confidence,
            observed_age_seconds=observed_age_seconds,
            evidence_ids=evidence_ids,
        )


def _same_scalar(left: Any, right: Any) -> bool:
    return type(left) is type(right) and left == right


def _age_seconds(*, captured_at: str, observed_at: str) -> float | None:
    try:
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if (
        captured.tzinfo is None
        or captured.utcoffset() is None
        or observed.tzinfo is None
        or observed.utcoffset() is None
    ):
        return None
    return (captured - observed).total_seconds()
