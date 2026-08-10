from __future__ import annotations

from dataclasses import replace

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.graph_proposal import (
    MissionGraphCompilationError,
    MissionGraphCompiler,
    MissionGraphProposal,
    MissionGraphProposalNode,
    MissionGraphProposalValidator,
)
from fireclaw_core.mission.mission_state import (
    MissionResourceReservation,
    MissionStateSnapshotBuilder,
)
from fireclaw_core.mission.task_graph import MissionTarget
from fireclaw_core.task.task_contract import structured_task_from_mission_subtask


def _registry(*, two_robots: bool = True) -> RobotRegistry:
    entries = [
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("victim_search", "navigate"),
        )
    ]
    if two_robots:
        entries.append(
            RobotRegistryEntry(
                robot_id="robot-b",
                base_url="http://robot-b.test",
                capabilities=("victim_search", "navigate"),
            )
        )
    return RobotRegistry(entries)


def _snapshot(registry: RobotRegistry):
    presence = {
        "robot-a": {
            "online": True,
            "last_seen_at": "2026-07-28T01:00:00+00:00",
            "state": {
                "robot_state": {
                    "battery_percent": 25,
                    "current_floor": 1,
                },
                "environment_state": {"reachable_floors": [1, 2, 3]},
                "task_capacity": {"available_execution_slots": 1},
                "emergency_stop": {"active": False},
            },
        },
        "robot-b": {
            "online": True,
            "last_seen_at": "2026-07-28T01:00:00+00:00",
            "state": {
                "robot_state": {
                    "battery_percent": 90,
                    "current_floor": 1,
                },
                "environment_state": {"reachable_floors": [1, 2, 3]},
                "task_capacity": {"available_execution_slots": 1},
                "emergency_stop": {"active": False},
            },
        },
    }
    return MissionStateSnapshotBuilder(registry=registry).build(
        mission_id="mission-1",
        presence=presence,
        captured_at="2026-07-28T01:00:01+00:00",
    )


def _node(
    node_id: str,
    *,
    capability: str = "victim_search",
    task_type: str | None = None,
    target: MissionTarget | None = None,
    depends_on: tuple[str, ...] = (),
) -> MissionGraphProposalNode:
    return MissionGraphProposalNode(
        node_id=node_id,
        task_type=task_type or (
            "navigation" if capability == "navigate" else "victim_search"
        ),
        command=f"execute {node_id}",
        target=target or MissionTarget(frame_id="building", floor=2),
        capability_required=capability,
        completion_goal=f"{node_id} completed with evidence",
        depends_on=depends_on,
    )


def _proposal(*nodes: MissionGraphProposalNode) -> MissionGraphProposal:
    return MissionGraphProposal(
        intent="search",
        command="去二楼救人",
        nodes=nodes,
    )


def test_compiler_allocates_robots_and_injects_runtime_constraints() -> None:
    registry = _registry()
    compiled = MissionGraphCompiler(registry).compile(
        _proposal(
            _node("scout_route", capability="navigate"),
            _node("search_floor", depends_on=("scout_route",)),
        ),
        state_snapshot=_snapshot(registry),
        mission_id="mission-1",
        plan_id="mission-1:plan:1",
    )

    scout, search = compiled.task_graph.nodes
    assert scout.robot_id == "robot-b"
    assert search.robot_id == "robot-a"
    assert search.depends_on == ("scout_route",)
    assert scout.exclusive_resources == ("robot:robot-b",)
    assert scout.recovery_policy == "replan"
    assert search.recovery_policy == "reassign"
    assert scout.timeout_seconds == 120.0
    assert search.timeout_seconds == 180.0
    assert {item.kind for item in search.success_evidence} == {
        "task_terminal_success",
        "skill_succeeded",
        "victim_search_result",
        "target_floor_confirmed",
    }
    assert {condition.kind for condition in search.preconditions} == {
        "robot_enabled",
        "robot_has_capability",
        "emergency_stop_inactive",
    }
    assert compiled.plan.subtasks[1].node_id == "search_floor"
    assert compiled.plan.subtasks[1].completion_goal == (
        "search_floor completed with evidence"
    )
    assert compiled.plan.subtasks[1].completion_contract["task_type"] == (
        "victim_search"
    )


def test_compiler_serializes_semantically_parallel_nodes_on_one_robot() -> None:
    registry = _registry(two_robots=False)
    compiled = MissionGraphCompiler(registry).compile(
        _proposal(_node("search_west"), _node("search_east")),
        state_snapshot=_snapshot(registry),
        mission_id="mission-1",
        plan_id="mission-1:plan:1",
    )

    first, second = compiled.task_graph.nodes
    assert first.depends_on == ()
    assert second.depends_on == ("search_west",)
    assert [subtask.execution_group for subtask in compiled.plan.subtasks] == [0, 1]


def test_compiler_preserves_area_entity_pose_target_without_floor() -> None:
    registry = _registry(two_robots=False)
    target = MissionTarget(
        frame_id="map",
        area_id="west_corridor",
        entity_id="victim-7",
        pose={"x": 4.0, "y": 8.5, "yaw": 1.2},
    )
    compiled = MissionGraphCompiler(registry).compile(
        _proposal(_node("approach_victim", target=target)),
        state_snapshot=_snapshot(registry),
        mission_id="mission-1",
        plan_id="mission-1:plan:1",
    )

    subtask = compiled.plan.subtasks[0]
    assert subtask.floor is None
    assert subtask.target == target.to_dict()
    structured = structured_task_from_mission_subtask(
        mission_id="mission-1",
        subtask=subtask,
    )
    assert structured.target == target.to_dict()
    assert structured.task_type == "victim_search"


def test_compiler_fails_closed_when_all_capable_robots_are_reserved() -> None:
    registry = _registry()
    snapshot = replace(
        _snapshot(registry),
        resource_reservations=(
            MissionResourceReservation(
                resource_id="robot:robot-a",
                owner_task_id="other-a",
                owner_robot_id="robot-a",
                status="active",
                acquired_at="2026-07-28T01:00:00+00:00",
            ),
            MissionResourceReservation(
                resource_id="robot:robot-b",
                owner_task_id="other-b",
                owner_robot_id="robot-b",
                status="active",
                acquired_at="2026-07-28T01:00:00+00:00",
            ),
        ),
    )

    with pytest.raises(MissionGraphCompilationError, match="no online, safe"):
        MissionGraphCompiler(registry).compile(
            _proposal(_node("search_floor")),
            state_snapshot=snapshot,
            mission_id="mission-1",
            plan_id="mission-1:plan:1",
        )


def test_proposal_validator_rejects_forward_dependency() -> None:
    proposal = _proposal(
        _node("search_floor", depends_on=("scout_route",)),
        _node("scout_route"),
    )

    errors = MissionGraphProposalValidator().validate(proposal)

    assert any("earlier declared nodes" in error for error in errors)
