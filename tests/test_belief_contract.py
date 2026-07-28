from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from fireclaw_core.agent.robot_registry import (
    RobotRegistry,
    RobotRegistryEntry,
)
from fireclaw_core.mission.belief_contract import MissionBeliefGate
from fireclaw_core.mission.graph_proposal import (
    MissionGraphBeliefAssumption,
    MissionGraphCompilationError,
    MissionGraphCompiler,
    MissionGraphProposal,
    MissionGraphProposalNode,
)
from fireclaw_core.mission.mission_scheduler import MissionScheduler
from fireclaw_core.mission.revision_dispatcher import MissionNodeExecution
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshot,
    MissionStateSnapshotBuilder,
)
from fireclaw_core.mission.task_graph import MissionTarget


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("navigate",),
        )
    ])


def _presence() -> dict[str, dict[str, Any]]:
    return {
        "robot-a": {
            "online": True,
            "last_seen_at": "2026-07-28T01:00:00+00:00",
            "state": {
                "robot_state": {
                    "battery_percent": 90,
                    "current_floor": 1,
                },
                "environment_state": {"reachable_floors": [1, 2]},
                "task_capacity": {"available_execution_slots": 1},
                "emergency_stop": {"active": False},
            },
        }
    }


def _snapshot(
    registry: RobotRegistry,
    *,
    passage_open: bool,
    snapshot_id_version: int = 1,
) -> MissionStateSnapshot:
    observed_at = (
        "2026-07-28T01:00:09+00:00"
        if snapshot_id_version == 1
        else "2026-07-28T01:00:19+00:00"
    )
    facts = [
        MissionEnvironmentFact(
            fact_id=f"west-stair:passage:{snapshot_id_version}",
            subject_id="west-stair",
            kind="passage_open",
            value=passage_open,
            source="map_fusion",
            observed_at=observed_at,
            evidence_ids=(
                f"evidence:west-stair:passage:{snapshot_id_version}",
            ),
            confidence=0.95,
        ),
        MissionEnvironmentFact(
            fact_id=f"west-stair:structure:{snapshot_id_version}",
            subject_id="west-stair",
            kind="structural_stable",
            value=True,
            source="structural_monitor",
            observed_at=observed_at,
            evidence_ids=(
                f"evidence:west-stair:structure:{snapshot_id_version}",
            ),
            confidence=0.95,
        ),
    ]
    return MissionStateSnapshotBuilder(
        registry=registry,
        environment_fact_provider=lambda _mission_id: facts,
    ).build(
        mission_id="mission-1",
        presence=_presence(),
        version=snapshot_id_version,
        captured_at=(
            "2026-07-28T01:00:10+00:00"
            if snapshot_id_version == 1
            else "2026-07-28T01:00:20+00:00"
        ),
    )


def _compile(
    registry: RobotRegistry,
    snapshot: MissionStateSnapshot,
    *,
    declare_passage: bool = True,
    expected_passage: bool = True,
    knowledge_refs: tuple[str, ...] = (),
):
    passage_belief = next(
        belief
        for belief in snapshot.environment_beliefs
        if belief.kind == "passage_open"
    )
    assumptions = (
        (
            MissionGraphBeliefAssumption(
                belief_id=passage_belief.belief_id,
                expected_value=expected_passage,
                knowledge_refs=knowledge_refs,
            ),
        )
        if declare_passage
        else ()
    )
    proposal = MissionGraphProposal(
        intent="search",
        command="经西侧楼梯去二楼救人",
        nodes=(
            MissionGraphProposalNode(
                node_id="navigate_second_floor",
                task_type="navigation",
                command="经西侧楼梯前往二楼",
                target=MissionTarget(
                    frame_id="building",
                    floor=2,
                    area_id="west-stair",
                ),
                capability_required="navigate",
                completion_goal="robot reached floor 2",
                belief_assumptions=assumptions,
            ),
        ),
        knowledge_refs=knowledge_refs,
    )
    return MissionGraphCompiler(registry).compile(
        proposal,
        state_snapshot=snapshot,
        mission_id="mission-1",
        plan_id="mission-1:plan:1",
    )


def test_compiler_binds_planner_assumption_to_snapshot_evidence() -> None:
    registry = _registry()
    snapshot = _snapshot(registry, passage_open=True)

    compiled = _compile(registry, snapshot)

    requirements = compiled.task_graph.nodes[0].belief_requirements
    assert [item.kind for item in requirements] == [
        "passage_open",
        "structural_stable",
    ]
    passage, structure = requirements
    assert passage.subject_id == "west-stair"
    assert passage.expected_value is True
    assert passage.required_status == "confirmed"
    assert passage.minimum_confidence == 0.8
    assert passage.maximum_age_seconds == 15.0
    assert passage.planning_snapshot_id == snapshot.snapshot_id
    assert passage.planning_evidence_ids == (
        "evidence:west-stair:passage:1",
    )
    assert passage.origin == "planner_and_authoritative_rule"
    assert passage.source_rule_ids == ("navigation-area-entry:v1",)
    assert structure.origin == "authoritative_rule"
    assert compiled.plan.subtasks[0].belief_requirements == [
        requirement.to_dict() for requirement in requirements
    ]
    assert compiled.task_graph.from_dict(
        compiled.task_graph.to_dict()
    ) == compiled.task_graph


def test_compiler_rejects_assumption_that_is_false_in_planning_snapshot() -> None:
    registry = _registry()
    snapshot = _snapshot(registry, passage_open=False)

    with pytest.raises(
        MissionGraphCompilationError,
        match="does not match",
    ):
        _compile(registry, snapshot)


def test_compiler_auto_adds_authoritative_beliefs_omitted_by_planner() -> None:
    registry = _registry()
    snapshot = _snapshot(registry, passage_open=True)

    compiled = _compile(
        registry,
        snapshot,
        declare_passage=False,
    )

    requirements = compiled.task_graph.nodes[0].belief_requirements
    assert [item.kind for item in requirements] == [
        "passage_open",
        "structural_stable",
    ]
    assert {
        item.origin for item in requirements
    } == {"authoritative_rule"}


def test_compiler_rejects_when_mandatory_belief_is_not_observed() -> None:
    registry = _registry()
    passage_fact = MissionEnvironmentFact(
        fact_id="west-stair:passage",
        subject_id="west-stair",
        kind="passage_open",
        value=True,
        source="map_fusion",
        observed_at="2026-07-28T01:00:09+00:00",
        evidence_ids=("evidence:west-stair:passage",),
        confidence=0.95,
    )
    snapshot = MissionStateSnapshotBuilder(
        registry=registry,
        environment_fact_provider=lambda _mission_id: [passage_fact],
    ).build(
        mission_id="mission-1",
        presence=_presence(),
        captured_at="2026-07-28T01:00:10+00:00",
    )

    with pytest.raises(
        MissionGraphCompilationError,
        match="structural_stable.*no such belief",
    ):
        _compile(registry, snapshot, declare_passage=False)


def test_authoritative_rule_rejects_conflicting_rag_grounded_assumption() -> None:
    registry = _registry()
    snapshot = _snapshot(registry, passage_open=True)

    with pytest.raises(
        MissionGraphCompilationError,
        match="conflicting expected value",
    ):
        _compile(
            registry,
            snapshot,
            expected_passage=False,
            knowledge_refs=("rag:unsafe-route-guidance",),
        )


def test_dispatch_gate_rejects_world_value_changed_after_planning() -> None:
    registry = _registry()
    compiled = _compile(
        registry,
        _snapshot(registry, passage_open=True),
    )
    dispatch_snapshot = _snapshot(
        registry,
        passage_open=False,
        snapshot_id_version=2,
    )

    result = MissionBeliefGate().evaluate(
        mission_id="mission-1",
        plan_id=compiled.task_graph.plan_id,
        node=compiled.task_graph.nodes[0],
        snapshot=dispatch_snapshot,
    )

    assert result.allowed is False
    assert result.failed_belief_ids == (
        compiled.task_graph.nodes[0].belief_requirements[0].belief_id,
    )
    assert result.checks[0].reason_code == "belief_value_changed"


def test_dispatch_gate_allows_unchanged_fresh_confirmed_belief() -> None:
    registry = _registry()
    planning_snapshot = _snapshot(registry, passage_open=True)
    compiled = _compile(registry, planning_snapshot)

    result = MissionBeliefGate().evaluate(
        mission_id="mission-1",
        plan_id=compiled.task_graph.plan_id,
        node=compiled.task_graph.nodes[0],
        snapshot=planning_snapshot,
    )

    assert result.allowed is True
    assert result.failed_belief_ids == ()
    assert result.checks[0].reason_code == (
        "belief_requirement_satisfied"
    )


class _GateBlockingMissionAgent:
    def __init__(
        self,
        *,
        registry: RobotRegistry,
        graph: Any,
        dispatch_snapshot: MissionStateSnapshot,
    ) -> None:
        self.registry = registry
        self.mission_registry = None
        self._graph = graph
        self._dispatch_snapshot = dispatch_snapshot
        self.submit_calls = 0
        self.events = []

    def active_task_graph(self, _mission_id: str):
        return self._graph

    def refresh_mission_state_snapshot(
        self,
        _mission_id: str,
    ) -> MissionStateSnapshot:
        return self._dispatch_snapshot

    def submit_subtask(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.submit_calls += 1
        raise AssertionError("belief gate must block before robot dispatch")

    def handle_execution_event(self, event: Any) -> dict[str, Any]:
        self.events.append(event)
        return {
            "status": "escalated",
            "message": "test revision policy stopped execution",
            "event": event.to_dict(),
        }


def test_scheduler_blocks_before_calling_robot_when_belief_changed() -> None:
    registry = _registry()
    compiled = _compile(
        registry,
        _snapshot(registry, passage_open=True),
    )
    mission_agent = _GateBlockingMissionAgent(
        registry=registry,
        graph=compiled.task_graph,
        dispatch_snapshot=_snapshot(
            registry,
            passage_open=False,
            snapshot_id_version=2,
        ),
    )

    result = MissionScheduler(mission_agent=mission_agent).schedule(
        compiled.plan,
        mission_id="mission-1",
    )

    assert result["status"] == "escalated"
    assert mission_agent.submit_calls == 0
    assert len(mission_agent.events) == 1
    assert mission_agent.events[0].event_type == (
        "belief_requirement_failed"
    )
    terminal = result["group_results"][0]["terminal_states"][0]
    assert terminal["status"] == "block"
    assert terminal["dispatch_belief_gate"]["allowed"] is False
    assert terminal["dispatch_belief_gate"]["checks"][0][
        "reason_code"
    ] == "belief_value_changed"


def test_gate_rejects_confirmed_belief_that_exceeds_node_age_limit() -> None:
    registry = _registry()
    compiled = _compile(
        registry,
        _snapshot(registry, passage_open=True),
    )
    dispatch_snapshot = _snapshot(
        registry,
        passage_open=True,
        snapshot_id_version=2,
    )
    old_belief = replace(
        dispatch_snapshot.environment_beliefs[0],
        observed_at="2026-07-28T00:59:00+00:00",
    )
    dispatch_snapshot = replace(
        dispatch_snapshot,
        environment_beliefs=(old_belief,),
    )

    result = MissionBeliefGate().evaluate(
        mission_id="mission-1",
        plan_id=compiled.task_graph.plan_id,
        node=compiled.task_graph.nodes[0],
        snapshot=dispatch_snapshot,
    )

    assert result.allowed is False
    assert result.checks[0].reason_code == "belief_too_old"


def test_node_checkpoint_round_trip_preserves_dispatch_gate_result() -> None:
    execution = MissionNodeExecution(
        plan_id="mission-1:plan:1",
        node_id="navigate_second_floor",
        robot_id="robot-a",
        status="blocked",
        updated_at="2026-07-28T01:00:20+00:00",
        dispatch_belief_gate={
            "allowed": False,
            "snapshot_id": "mission-1:state:2",
            "failed_belief_ids": ["belief:west-stair"],
        },
    )

    restored = MissionNodeExecution.from_dict(execution.to_dict())

    assert restored == execution
