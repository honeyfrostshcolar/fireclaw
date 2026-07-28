from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from fireclaw_core.mission.task_graph import MissionTarget


VALID_ASSUMPTION_TARGET_FIELDS = frozenset({"area_id", "entity_id"})


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _value_key(value: Any) -> tuple[str, str]:
    return type(value).__name__, repr(value)


@dataclass(frozen=True)
class RequiredBeliefTemplate:
    """One authoritative belief kind required for a matched task target."""

    kind: str
    expected_value: str | int | float | bool | None
    minimum_confidence: float = 0.8
    maximum_age_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("Required belief template kind must not be empty.")
        if not _is_scalar(self.expected_value):
            raise ValueError(
                "Required belief template expected_value must be scalar."
            )
        if not 0.8 <= self.minimum_confidence <= 1.0:
            raise ValueError(
                "Required belief template minimum_confidence must be between "
                "0.8 and 1.0."
            )
        if not 0 < self.maximum_age_seconds <= 30.0:
            raise ValueError(
                "Required belief template maximum_age_seconds must be greater "
                "than 0 and at most 30."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "expected_value": self.expected_value,
            "minimum_confidence": self.minimum_confidence,
            "maximum_age_seconds": self.maximum_age_seconds,
        }


@dataclass(frozen=True)
class TaskAssumptionRule:
    """A reviewed, versioned task-to-world-assumption policy."""

    rule_id: str
    version: int
    task_type: str
    target_field: str
    required_beliefs: tuple[RequiredBeliefTemplate, ...]
    authority: str = "approved"
    source_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("rule_id", self.rule_id),
            ("task_type", self.task_type),
            ("authority", self.authority),
        ):
            if not value.strip():
                raise ValueError(
                    f"Task assumption rule {name} must not be empty."
                )
        if self.version < 1:
            raise ValueError(
                "Task assumption rule version must be at least 1."
            )
        if self.target_field not in VALID_ASSUMPTION_TARGET_FIELDS:
            raise ValueError(
                "Task assumption rule target_field is unsupported."
            )
        if not self.required_beliefs:
            raise ValueError(
                "Task assumption rule requires at least one belief template."
            )
        kinds = [item.kind for item in self.required_beliefs]
        if len(kinds) != len(set(kinds)):
            raise ValueError(
                "Task assumption rule has duplicate required belief kinds."
            )
        if self.authority != "approved":
            raise ValueError(
                "Only approved task assumption rules can enter the runtime "
                "registry."
            )
        if any(not item.strip() for item in self.source_refs):
            raise ValueError(
                "Task assumption rule source_refs must not contain empty values."
            )

    @property
    def versioned_rule_id(self) -> str:
        return f"{self.rule_id}:v{self.version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "version": self.version,
            "versioned_rule_id": self.versioned_rule_id,
            "task_type": self.task_type,
            "target_field": self.target_field,
            "required_beliefs": [
                item.to_dict() for item in self.required_beliefs
            ],
            "authority": self.authority,
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True)
class GroundedBeliefRequirement:
    """One rule template grounded to a concrete target subject."""

    subject_id: str
    kind: str
    expected_value: str | int | float | bool | None
    minimum_confidence: float
    maximum_age_seconds: float
    source_rule_ids: tuple[str, ...]
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class KnowledgeConstraintCandidate:
    """Advisory RAG/LLM extraction that cannot authorize runtime behavior."""

    knowledge_id: str
    task_type: str
    target_field: str
    required_beliefs: tuple[RequiredBeliefTemplate, ...]
    citation: str | None = None
    advisory_only: bool = True
    can_authorize_action: bool = field(default=False, init=False)
    can_assert_current_state: bool = field(default=False, init=False)
    requires_current_state_revalidation: bool = field(
        default=True,
        init=False,
    )

    def __post_init__(self) -> None:
        if not self.knowledge_id.strip() or not self.task_type.strip():
            raise ValueError(
                "Knowledge constraint candidate identifiers must not be empty."
            )
        if self.target_field not in VALID_ASSUMPTION_TARGET_FIELDS:
            raise ValueError(
                "Knowledge constraint candidate target_field is unsupported."
            )
        if not self.required_beliefs:
            raise ValueError(
                "Knowledge constraint candidate requires belief templates."
            )
        if self.advisory_only is not True:
            raise ValueError(
                "Knowledge constraint candidates must remain advisory."
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "knowledge_id": self.knowledge_id,
            "task_type": self.task_type,
            "target_field": self.target_field,
            "required_beliefs": [
                item.to_dict() for item in self.required_beliefs
            ],
            "advisory_only": self.advisory_only,
            "can_authorize_action": self.can_authorize_action,
            "can_assert_current_state": self.can_assert_current_state,
            "requires_current_state_revalidation": (
                self.requires_current_state_revalidation
            ),
        }
        if self.citation is not None:
            result["citation"] = self.citation
        return result


class TaskAssumptionRegistry:
    """Authoritative rules used by the deterministic graph compiler."""

    def __init__(
        self,
        rules: Iterable[TaskAssumptionRule] = (),
    ) -> None:
        self._rules: dict[str, TaskAssumptionRule] = {}
        for rule in rules:
            self.register(rule)

    def register(self, rule: TaskAssumptionRule) -> None:
        key = rule.versioned_rule_id
        if key in self._rules:
            raise ValueError(
                f"Duplicate task assumption rule: {key}"
            )
        self._rules[key] = rule

    def rules(self) -> tuple[TaskAssumptionRule, ...]:
        return tuple(self._rules[key] for key in sorted(self._rules))

    def ground(
        self,
        *,
        task_type: str,
        target: MissionTarget,
    ) -> tuple[GroundedBeliefRequirement, ...]:
        grouped: dict[
            tuple[str, str],
            list[tuple[TaskAssumptionRule, RequiredBeliefTemplate]],
        ] = {}
        for rule in self.rules():
            if rule.task_type != task_type:
                continue
            subject = getattr(target, rule.target_field)
            if not isinstance(subject, str) or not subject.strip():
                continue
            for template in rule.required_beliefs:
                grouped.setdefault(
                    (subject, template.kind),
                    [],
                ).append((rule, template))

        grounded: list[GroundedBeliefRequirement] = []
        for (subject_id, kind), items in sorted(grouped.items()):
            expected_values = {
                _value_key(template.expected_value)
                for _, template in items
            }
            if len(expected_values) != 1:
                rule_ids = sorted({
                    rule.versioned_rule_id for rule, _ in items
                })
                raise ValueError(
                    "Approved task assumption rules conflict for "
                    f"{subject_id!r}/{kind!r}: {rule_ids}."
                )
            template = items[0][1]
            grounded.append(
                GroundedBeliefRequirement(
                    subject_id=subject_id,
                    kind=kind,
                    expected_value=template.expected_value,
                    minimum_confidence=max(
                        item.minimum_confidence
                        for _, item in items
                    ),
                    maximum_age_seconds=min(
                        item.maximum_age_seconds
                        for _, item in items
                    ),
                    source_rule_ids=tuple(sorted({
                        rule.versioned_rule_id for rule, _ in items
                    })),
                    source_refs=tuple(sorted({
                        source_ref
                        for rule, _ in items
                        for source_ref in rule.source_refs
                    })),
                )
            )
        return tuple(grounded)


DEFAULT_TASK_ASSUMPTION_RULES = (
    TaskAssumptionRule(
        rule_id="navigation-area-entry",
        version=1,
        task_type="navigation",
        target_field="area_id",
        required_beliefs=(
            RequiredBeliefTemplate(
                kind="passage_open",
                expected_value=True,
            ),
            RequiredBeliefTemplate(
                kind="structural_stable",
                expected_value=True,
            ),
        ),
        source_refs=(
            "fireclaw-policy:embodied-navigation-safety",
        ),
    ),
)


def default_task_assumption_registry() -> TaskAssumptionRegistry:
    return TaskAssumptionRegistry(DEFAULT_TASK_ASSUMPTION_RULES)
