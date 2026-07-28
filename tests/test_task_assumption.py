from __future__ import annotations

import pytest

from fireclaw_core.mission.task_assumption import (
    KnowledgeConstraintCandidate,
    RequiredBeliefTemplate,
    TaskAssumptionRegistry,
    TaskAssumptionRule,
    default_task_assumption_registry,
)
from fireclaw_core.mission.task_graph import MissionTarget


def _target() -> MissionTarget:
    return MissionTarget(
        frame_id="building",
        floor=2,
        area_id="west-stair",
    )


def test_default_registry_grounds_navigation_safety_assumptions() -> None:
    grounded = default_task_assumption_registry().ground(
        task_type="navigation",
        target=_target(),
    )

    assert [
        (item.subject_id, item.kind, item.expected_value)
        for item in grounded
    ] == [
        ("west-stair", "passage_open", True),
        ("west-stair", "structural_stable", True),
    ]
    assert {
        rule_id
        for item in grounded
        for rule_id in item.source_rule_ids
    } == {"navigation-area-entry:v1"}


def test_registry_merges_approved_rules_using_strictest_thresholds() -> None:
    registry = TaskAssumptionRegistry((
        TaskAssumptionRule(
            rule_id="navigation-area-entry",
            version=1,
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=True,
                    minimum_confidence=0.8,
                    maximum_age_seconds=15.0,
                ),
            ),
        ),
        TaskAssumptionRule(
            rule_id="incident-command-route-policy",
            version=2,
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=True,
                    minimum_confidence=0.9,
                    maximum_age_seconds=10.0,
                ),
            ),
        ),
    ))

    grounded = registry.ground(
        task_type="navigation",
        target=_target(),
    )

    assert len(grounded) == 1
    assert grounded[0].minimum_confidence == 0.9
    assert grounded[0].maximum_age_seconds == 10.0
    assert grounded[0].source_rule_ids == (
        "incident-command-route-policy:v2",
        "navigation-area-entry:v1",
    )


def test_registry_rejects_conflicting_approved_rules_when_grounded() -> None:
    registry = TaskAssumptionRegistry((
        TaskAssumptionRule(
            rule_id="route-open",
            version=1,
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=True,
                ),
            ),
        ),
        TaskAssumptionRule(
            rule_id="route-closed",
            version=1,
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=False,
                ),
            ),
        ),
    ))

    with pytest.raises(ValueError, match="rules conflict"):
        registry.ground(task_type="navigation", target=_target())


def test_runtime_registry_accepts_only_approved_rules() -> None:
    with pytest.raises(ValueError, match="Only approved"):
        TaskAssumptionRule(
            rule_id="rag-generated-rule",
            version=1,
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=True,
                ),
            ),
            authority="rag_candidate",
        )


def test_rag_constraint_candidate_is_advisory_only() -> None:
    candidate = KnowledgeConstraintCandidate(
        knowledge_id="rag:fire-response-manual:42",
        task_type="navigation",
        target_field="area_id",
        required_beliefs=(
            RequiredBeliefTemplate(
                kind="passage_open",
                expected_value=True,
            ),
        ),
        citation="Fire response manual, route assessment section",
    )

    assert candidate.advisory_only is True
    assert candidate.can_authorize_action is False
    assert candidate.can_assert_current_state is False
    assert candidate.requires_current_state_revalidation is True
    assert candidate.to_dict()["can_authorize_action"] is False


def test_rag_constraint_candidate_cannot_be_marked_authoritative() -> None:
    with pytest.raises(ValueError, match="must remain advisory"):
        KnowledgeConstraintCandidate(
            knowledge_id="rag:unsafe",
            task_type="navigation",
            target_field="area_id",
            required_beliefs=(
                RequiredBeliefTemplate(
                    kind="passage_open",
                    expected_value=True,
                ),
            ),
            advisory_only=False,
        )
