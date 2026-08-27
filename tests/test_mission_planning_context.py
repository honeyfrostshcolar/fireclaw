from __future__ import annotations

import pytest

from fireclaw_core.agent.robot_registry import (
    RobotRegistry,
    RobotRegistryEntry,
)
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationDecision,
    MissionDeliberationRuntime,
    MissionStateObservation,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext
from fireclaw_core.mission.mission_state import MissionStateSnapshotBuilder
from fireclaw_core.mission.planning_context import (
    MissionPlanningContextAssembler,
    MissionPlanningContextAssemblyError,
    MissionPlanningContextBudget,
)
from fireclaw_core.planner.llm_planner import (
    build_constrained_graph_proposal_tool,
)


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("navigate", "recon"),
            zone="west",
        )
    ])


def _snapshot():
    registry = _registry()
    snapshot = MissionStateSnapshotBuilder(registry=registry).build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 80,
                        "current_floor": 1,
                    },
                    "emergency_stop": {"active": False},
                },
            }
        },
        captured_at="2026-07-28T01:00:01+00:00",
    )
    return registry, snapshot


def _context(snapshot, **overrides):
    values = {
        "available_robots": list(_registry().enabled_entries()),
        "state_snapshot": snapshot.to_dict(),
        "retrieved_memories": [],
        "operator_corrections": [],
        "external_knowledge": [],
    }
    values.update(overrides)
    return MissionPlannerContext(**values)


def test_assembler_preserves_critical_context_and_provenance() -> None:
    _, snapshot = _snapshot()
    observation = MissionStateObservation(
        kind="robot_state",
        subject_id="robot-a",
        iteration=1,
        data={
            "found": True,
            "robot": {
                "robot_id": "robot-a",
                "emergency_stop_active": False,
            },
        },
    )
    context = _context(
        snapshot,
        operator_clarifications=[{
            "round": 1,
            "question": "请提供坐标。",
            "answer": "map 坐标 (1.8, -0.1)",
            "answer_source": "authenticated_operator",
        }],
        operator_corrections=[{"record_id": "correction-1"}],
        retrieved_memories=[{"record_id": "memory-1"}],
        external_knowledge=[{"knowledge_id": "guide-1"}],
    )

    assembly = MissionPlanningContextAssembler().assemble(
        mission_id="mission-1",
        command="经西侧楼梯去二楼",
        state_snapshot=snapshot,
        planner_context=context,
        iteration=2,
        observations=(observation,),
        validation_errors=("previous proposal was invalid",),
        plan_revision=2,
        supersedes_plan_id="mission-1:plan:1",
        invalidation_evidence_ids=("evidence:route-blocked",),
    )

    envelope = assembly.envelope
    authoritative = envelope.authoritative
    assert authoritative["operator_command"] == "经西侧楼梯去二楼"
    assert authoritative["operator_clarifications"][0][
        "answer_source"
    ] == "authenticated_operator"
    assert authoritative["snapshot_contract"]["snapshot_id"] == (
        snapshot.snapshot_id
    )
    assert authoritative["observations"] == [observation.to_dict()]
    assert authoritative["validation_errors"] == [
        "previous proposal was invalid"
    ]
    assert authoritative["invalidation_evidence_ids"] == [
        "evidence:route-blocked"
    ]
    assert envelope.continuity["supersedes_plan_id"] == (
        "mission-1:plan:1"
    )
    assert envelope.manifest.safety_critical_context_preserved is True
    sections = {
        section.name: section
        for section in envelope.manifest.sections
    }
    assert sections["observations"].critical is True
    assert sections["observations"].trust == "authoritative"
    assert sections["operator_clarifications"].critical is True
    assert sections["operator_clarifications"].trust == "authoritative"
    assert sections["operator_corrections"].trust == (
        "operator_advisory"
    )
    assert sections["external_knowledge"].trust == (
        "untrusted_advisory"
    )


def test_assembler_prioritizes_advisory_sections_under_budget() -> None:
    _, snapshot = _snapshot()
    correction = {"record_id": "correction-1", "content": "hold west"}
    context_with_correction = _context(
        snapshot,
        operator_corrections=[correction],
    )
    correction_only = MissionPlanningContextAssembler(
        MissionPlanningContextBudget(max_dynamic_chars=100_000)
    ).assemble(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=context_with_correction,
        iteration=1,
    )
    exact_budget = correction_only.envelope.manifest.used_dynamic_chars
    context = _context(
        snapshot,
        operator_corrections=[correction],
        retrieved_memories=[
            {"record_id": "memory-1", "content": "past route"}
        ],
        external_knowledge=[
            {"knowledge_id": "guide-1", "excerpt": "route guidance"}
        ],
    )

    assembly = MissionPlanningContextAssembler(
        MissionPlanningContextBudget(
            max_dynamic_chars=exact_budget,
        )
    ).assemble(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=context,
        iteration=1,
    )

    assert assembly.planner_context.operator_corrections == [correction]
    assert assembly.planner_context.retrieved_memories == []
    assert assembly.planner_context.external_knowledge == []
    assert assembly.planner_context.state_snapshot == snapshot.to_dict()
    assert assembly.planner_context.tool_exposed_belief_ids == ()
    sections = {
        section.name: section
        for section in assembly.envelope.manifest.sections
    }
    assert dict(
        sections["retrieved_memories"].omission_reasons
    ) == {"dynamic_budget": 1}
    assert dict(
        sections["external_knowledge"].omission_reasons
    ) == {"dynamic_budget": 1}


def test_assembler_deduplicates_and_applies_item_limits() -> None:
    _, snapshot = _snapshot()
    memory = {"record_id": "memory-1"}
    context = _context(
        snapshot,
        retrieved_memories=[
            memory,
            dict(memory),
            {"record_id": "memory-2"},
        ],
    )

    assembly = MissionPlanningContextAssembler(
        MissionPlanningContextBudget(
            max_dynamic_chars=100_000,
            max_memories=1,
        )
    ).assemble(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=context,
        iteration=1,
    )

    assert assembly.planner_context.retrieved_memories == [memory]
    section = next(
        item
        for item in assembly.envelope.manifest.sections
        if item.name == "retrieved_memories"
    )
    assert dict(section.omission_reasons) == {
        "duplicate_item": 1,
        "item_limit": 1,
    }
    assert section.included_refs == ("memory-1",)
    assert section.omitted_refs == (
        ("memory-1", "duplicate_item"),
        ("memory-2", "item_limit"),
    )


def test_tool_schema_exposes_only_knowledge_in_projected_context() -> None:
    _, snapshot = _snapshot()
    context = _context(
        snapshot,
        external_knowledge=[
            {"knowledge_id": "guide-1", "excerpt": "first"},
            {"knowledge_id": "guide-2", "excerpt": "second"},
        ],
    )
    assembly = MissionPlanningContextAssembler(
        MissionPlanningContextBudget(
            max_dynamic_chars=100_000,
            max_external_knowledge=1,
        )
    ).assemble(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=context,
        iteration=1,
    )

    tool = build_constrained_graph_proposal_tool(
        assembly.planner_context
    )
    knowledge_schema = (
        tool["function"]["parameters"]["properties"]["knowledge_refs"]
    )
    assumption_schema = (
        tool["function"]["parameters"]["properties"]["nodes"]["items"][
            "properties"
        ]["belief_assumptions"]["items"]["properties"][
            "knowledge_refs"
        ]
    )
    assert knowledge_schema["items"]["enum"] == ["guide-1"]
    assert assumption_schema["items"]["enum"] == ["guide-1"]
    section = next(
        item
        for item in assembly.envelope.manifest.sections
        if item.name == "external_knowledge"
    )
    assert section.included_refs == ("guide-1",)
    assert section.omitted_refs == (("guide-2", "item_limit"),)


def test_assembler_fails_closed_instead_of_truncating_critical_context() -> None:
    _, snapshot = _snapshot()
    context = _context(snapshot)

    with pytest.raises(
        MissionPlanningContextAssemblyError,
        match="will not be silently truncated",
    ):
        MissionPlanningContextAssembler(
            MissionPlanningContextBudget(max_dynamic_chars=64)
        ).assemble(
            mission_id="mission-1",
            command="去二楼",
            state_snapshot=snapshot,
            planner_context=context,
            iteration=1,
        )


class _EscalatePolicy:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, _request):
        self.calls += 1
        return MissionDeliberationDecision.escalate(
            "operator review required",
            reason_code="test_escalation",
        )


def test_runtime_persists_exact_context_manifest_for_each_attempt() -> None:
    registry, snapshot = _snapshot()
    policy = _EscalatePolicy()
    runtime = MissionDeliberationRuntime(
        registry=registry,
        policy=policy,
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=_context(snapshot),
    )

    assert result.status == "escalated"
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.context_id is not None
    assert attempt.context_manifest is not None
    assert attempt.context_manifest[
        "safety_critical_context_preserved"
    ] is True
    serialized = result.to_dict()["attempts"][0]
    assert serialized["context_id"] == attempt.context_id
    assert serialized["context_manifest"]["used_dynamic_chars"] > 0
    assert policy.calls == 1


def test_runtime_blocks_before_policy_when_critical_context_exceeds_budget(
) -> None:
    registry, snapshot = _snapshot()
    policy = _EscalatePolicy()
    runtime = MissionDeliberationRuntime(
        registry=registry,
        policy=policy,
        context_assembler=MissionPlanningContextAssembler(
            MissionPlanningContextBudget(max_dynamic_chars=64)
        ),
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼",
        state_snapshot=snapshot,
        planner_context=_context(snapshot),
    )

    assert result.status == "blocked"
    assert result.reason_code == "context_budget_exceeded"
    assert policy.calls == 0
    assert len(result.attempts) == 1
    attempt = result.attempts[0]
    assert attempt.operation == "assemble_context"
    assert attempt.outcome == "rejected"
    assert attempt.context_id is None
    assert attempt.context_manifest is None
