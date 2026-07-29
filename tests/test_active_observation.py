from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fireclaw_core.agent.robot_registry import (
    RobotRegistry,
    RobotRegistryEntry,
)
from fireclaw_core.mission.active_observation import (
    ActiveObservationError,
    MissionActiveObservationLimits,
    MissionObservationCompiler,
    MissionObservationRequest,
    environment_fact_from_observation_trace,
)
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationDecision,
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshotBuilder,
)
from fireclaw_core.mission.task_graph import MissionTarget


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("recon",),
        ),
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("recon",),
        ),
    ])


def _snapshot(registry: RobotRegistry):
    fact = MissionEnvironmentFact(
        fact_id="fact-stairs-a",
        subject_id="west-stairs",
        kind="passable",
        value=True,
        source="robot:robot-a",
        observed_at="2026-07-29T00:59:55+00:00",
        evidence_ids=("image-a",),
        confidence=0.6,
    )
    return MissionStateSnapshotBuilder(
        registry=registry,
        environment_fact_provider=lambda _mission_id: [fact],
    ).build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-29T01:00:00+00:00",
                "state": {
                        "robot_state": {
                        "battery_percent": 30,
                        "current_floor": 1,
                        "available_sensors": ["camera"],
                        },
                        "environment_state": {
                            "reachable_floors": [1, 2],
                        },
                },
            },
            "robot-b": {
                "online": True,
                "last_seen_at": "2026-07-29T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 85,
                        "current_floor": 2,
                        "available_sensors": ["camera", "thermal_camera"],
                        },
                        "environment_state": {
                            "reachable_floors": [1, 2],
                        },
                },
            },
        },
        captured_at="2026-07-29T01:00:00+00:00",
    )


def _request(snapshot) -> MissionObservationRequest:
    belief = snapshot.environment_beliefs[0]
    return MissionObservationRequest(
        belief_id=belief.belief_id,
        target=MissionTarget(
            frame_id="building",
            floor=2,
            area_id="west-stairs",
        ),
        capability_required="recon",
        required_sensor="thermal_camera",
        reason="Confirm whether the west stairs are passable.",
    )


def test_compiler_selects_safe_capable_robot_and_builds_typed_subtask() -> None:
    registry = _registry()
    snapshot = _snapshot(registry)

    compiled = MissionObservationCompiler(registry).compile(
        _request(snapshot),
        snapshot=snapshot,
    )

    assert compiled.robot_id == "robot-b"
    assert compiled.subtask.robot_id == "robot-b"
    assert compiled.subtask.task_type == "reconnaissance"
    assert compiled.subtask.capability_required == "recon"
    assert compiled.subtask.target["area_id"] == "west-stairs"
    assert compiled.subtask.completion_contract["risk_level"] == "medium"


def test_compiler_rejects_confirmed_belief() -> None:
    registry = _registry()
    snapshot = _snapshot(registry)
    high_confidence_fact = MissionEnvironmentFact(
        fact_id="fact-stairs-confirmed",
        subject_id="west-stairs",
        kind="passable",
        value=True,
        source="robot:robot-b",
        observed_at="2026-07-29T00:59:59+00:00",
        evidence_ids=("thermal-b",),
        confidence=0.95,
    )
    confirmed = MissionStateSnapshotBuilder(
        registry=registry,
        environment_fact_provider=lambda _mission_id: [
            high_confidence_fact
        ],
    ).build(
        mission_id="mission-1",
        presence={},
        captured_at="2026-07-29T01:00:00+00:00",
    )
    request = MissionObservationRequest(
        belief_id=confirmed.environment_beliefs[0].belief_id,
        target=MissionTarget(
            frame_id="building",
            floor=2,
            area_id="west-stairs",
        ),
        capability_required="recon",
        reason="Observe again.",
    )

    with pytest.raises(
        ActiveObservationError,
        match="unresolved belief",
    ):
        MissionObservationCompiler(registry).compile(
            request,
            snapshot=confirmed,
        )


def test_structured_success_trace_becomes_host_attributed_fact() -> None:
    registry = _registry()
    snapshot = _snapshot(registry)
    compiled = MissionObservationCompiler(registry).compile(
        _request(snapshot),
        snapshot=snapshot,
    )
    trace = {
        "status": "succeeded",
        "result": {
            "observation": {
                "belief_id": compiled.belief.belief_id,
                "subject_id": "west-stairs",
                "kind": "passable",
                "value": False,
                "confidence": 0.94,
                "observed_at": "2026-07-29T01:00:10+00:00",
                "evidence_ids": ["thermal-frame-42"],
            },
        },
    }

    fact = environment_fact_from_observation_trace(
        trace,
        compiled=compiled,
        mission_id="mission-1",
        task_id="robot-task-7",
    )

    assert fact.fact_id == "mission-1:observation:robot-task-7"
    assert fact.source == "robot:robot-b"
    assert fact.value is False
    assert fact.evidence_ids == ("robot-task-7", "thermal-frame-42")


def test_free_text_result_is_never_promoted_to_environment_fact() -> None:
    registry = _registry()
    snapshot = _snapshot(registry)
    compiled = MissionObservationCompiler(registry).compile(
        _request(snapshot),
        snapshot=snapshot,
    )

    with pytest.raises(
        ActiveObservationError,
        match="structured observation",
    ):
        environment_fact_from_observation_trace(
            {
                "status": "succeeded",
                "result": {"message": "West stairs look blocked."},
            },
            compiled=compiled,
            mission_id="mission-1",
            task_id="robot-task-8",
        )


class _ActiveObservationPolicy:
    def decide(self, request):
        belief = request.state_snapshot.environment_beliefs[0]
        if not request.observations:
            return MissionDeliberationDecision.inspect(
                "environment_beliefs",
                subject_id=belief.belief_id,
            )
        if request.state_snapshot.version == 1:
            return MissionDeliberationDecision.observe(
                MissionObservationRequest(
                    belief_id=belief.belief_id,
                    target=MissionTarget(
                        frame_id="building",
                        floor=2,
                        area_id="west-stairs",
                    ),
                    capability_required="recon",
                    required_sensor="thermal_camera",
                    reason="Resolve stair passability before rescue.",
                )
            )
        return MissionDeliberationDecision.propose(
            MissionPlanningResult(
                status="planned",
                message="Plan on refreshed state.",
                intent="search",
                plan=MissionPlan(
                    intent="search",
                    command=request.command,
                    subtasks=[
                        MissionSubtask(
                            robot_id="robot-b",
                            command="Search the second floor.",
                            floor=2,
                            capability_required="search_for_victims",
                        )
                    ],
                ),
            )
        )


class _ObservationSubagentClient:
    def __init__(self) -> None:
        self.observation_belief_id: str | None = None
        self.submitted: list[dict] = []

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "state": {
                "robot_state": {
                    "battery_percent": 88,
                    "current_floor": 2,
                    "available_sensors": [
                        "camera",
                        "thermal_camera",
                    ],
                },
                "environment_state": {
                    "reachable_floors": [1, 2],
                },
                "task_capacity": {
                    "active_execution_tasks": 0,
                    "max_active_execution_tasks": 2,
                    "available_execution_slots": 2,
                },
                "emergency_stop": {"active": False},
            },
        }

    def submit_task(self, entry, **kwargs):
        self.submitted.append(kwargs)
        mission = kwargs.get("mission") or {}
        if mission.get("purpose") == "active_observation":
            self.observation_belief_id = mission["belief_id"]
            return {
                "status": "accepted",
                "task_id": "observation-task-1",
            }
        return {"status": "accepted", "task_id": "mission-task-1"}

    def get_task_trace(self, entry, task_id):
        assert task_id == "observation-task-1"
        return {
            "status": "succeeded",
            "result": {
                "observation": {
                    "belief_id": self.observation_belief_id,
                    "subject_id": "west-stairs",
                    "kind": "passable",
                    "value": True,
                    "confidence": 0.95,
                    "observed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "evidence_ids": ["thermal-frame-live"],
                },
            },
        }


def test_mission_agent_observes_refreshes_snapshot_and_resumes_planning() -> None:
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("recon", "search_for_victims"),
        ),
    ])
    observed_at = datetime.now(timezone.utc).isoformat()
    initial_fact = MissionEnvironmentFact(
        fact_id="fixed-camera-stairs",
        subject_id="west-stairs",
        kind="passable",
        value=True,
        source="camera:west-stairs",
        observed_at=observed_at,
        evidence_ids=("fixed-frame-1",),
        confidence=0.6,
    )
    snapshot_builder = MissionStateSnapshotBuilder(
        registry=registry,
        environment_fact_provider=lambda _mission_id: [initial_fact],
    )
    runtime = MissionDeliberationRuntime(
        registry=registry,
        policy=_ActiveObservationPolicy(),
    )
    client = _ObservationSubagentClient()
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_state_snapshot_builder=snapshot_builder,
        mission_deliberation_runtime=runtime,
        active_observation_limits=MissionActiveObservationLimits(
            max_rounds=2,
            timeout_seconds=1,
            poll_interval_seconds=0.001,
        ),
    )

    result = agent.plan_and_submit(
        "Search the second floor after confirming the west stairs.",
        session_id="mission-1",
        use_scheduler=False,
    )

    assert result["status"] == "planned"
    assert result["state_snapshot"]["snapshot_id"] == "mission-1:state:2"
    assert result["deliberation"]["snapshot_id"] == "mission-1:state:2"
    assert len(result["active_observation_rounds"]) == 1
    observation_round = result["active_observation_rounds"][0]
    assert observation_round["snapshot_id"] == "mission-1:state:1"
    assert observation_round["next_snapshot_id"] == "mission-1:state:2"
    assert observation_round["terminal_status"] == "succeeded"
    assert observation_round["environment_fact"]["source"] == (
        "robot:robot-b"
    )
    assert [
        item["mission"].get("purpose")
        for item in client.submitted
    ] == ["active_observation", None]
