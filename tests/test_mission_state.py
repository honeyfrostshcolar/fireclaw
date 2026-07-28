from dataclasses import replace

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_memory import MissionMemoryStore
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshot,
    MissionStateSnapshotBuilder,
    MissionStateSnapshotValidator,
)
from fireclaw_core.planner.llm_planner import build_system_prompt
from fireclaw_core.task.task_registry import TaskRecord


def _registry() -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims", "recon"),
            zone="east",
        ),
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("search_for_victims",),
            zone="west",
        ),
    ])


class _TaskRegistry:
    def list_records(self) -> list[TaskRecord]:
        return [
            TaskRecord(
                task_id="mission-1",
                runtime="mission_gateway",
                requester_session_id="mission-1",
                owner_id="operator",
                scope_kind="mission",
                command="去二楼搜索",
                status="running",
                delivery_status="delivered",
                notify_policy="state_changes",
                created_at="2026-07-28T00:00:00+00:00",
            ),
            TaskRecord(
                task_id="unrelated",
                runtime="mission_gateway",
                requester_session_id="other-mission",
                owner_id="operator",
                scope_kind="mission",
                command="其他任务",
                status="running",
                delivery_status="delivered",
                notify_policy="state_changes",
                created_at="2026-07-28T00:00:00+00:00",
            ),
        ]


def test_snapshot_builder_projects_authoritative_runtime_state() -> None:
    builder = MissionStateSnapshotBuilder(
        registry=_registry(),
        task_registry=_TaskRegistry(),
        environment_fact_provider=lambda mission_id: [
            MissionEnvironmentFact(
                fact_id=f"{mission_id}:stairwell-a",
                kind="passage_status",
                value="blocked",
                source="map-fusion",
                observed_at="2026-07-28T01:00:00+00:00",
                evidence_ids=("thermal-frame-7",),
                confidence=0.94,
                subject_id="stairwell-a",
            )
        ],
    )

    snapshot = builder.build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "mode": "dry_run",
                        "dry_run": True,
                        "battery_percent": 82.5,
                        "current_floor": 2,
                        "available_sensors": ["thermal_camera", "lidar"],
                        "supports_real_execution": False,
                    },
                    "environment_state": {
                        "reachable_floors": [1, 2, 3],
                        "hazards": ["smoke"],
                        "victims_by_floor": {"2": 1},
                    },
                    "task_capacity": {
                        "active_execution_tasks": 1,
                        "max_active_execution_tasks": 2,
                        "available_execution_slots": 1,
                    },
                    "active_tasks": [{"task_id": "search-2f"}],
                    "emergency_stop": {"active": False},
                },
            },
            "robot-b": {"online": False, "stale": True},
        },
        captured_at="2026-07-28T01:00:01+00:00",
    )

    assert snapshot.snapshot_id == "mission-1:state:1"
    assert [robot.robot_id for robot in snapshot.robots] == ["robot-a", "robot-b"]
    assert snapshot.robots[0].battery_percent == 82.5
    assert snapshot.robots[0].victims_by_floor == {2: 1}
    assert snapshot.robots[0].active_task_ids == ("search-2f",)
    assert snapshot.robots[1].stale is True
    assert [task.task_id for task in snapshot.tasks] == ["mission-1"]
    assert snapshot.evidence_ids == ("thermal-frame-7",)
    assert snapshot.belief_projection_version == 1
    assert len(snapshot.environment_beliefs) == 1
    belief = snapshot.environment_beliefs[0]
    assert belief.subject_id == "stairwell-a"
    assert belief.kind == "passage_status"
    assert belief.status == "confirmed"
    assert belief.value == "blocked"
    assert snapshot.to_dict()["environment_belief_summary"] == {
        "confirmed": 1,
        "conflicted": 0,
        "stale": 0,
        "uncertain": 0,
    }
    assert MissionStateSnapshotValidator().validate(snapshot) == []


def test_snapshot_validator_rejects_untraceable_fact_and_broken_version_chain() -> None:
    fact = MissionEnvironmentFact(
        fact_id="fact-1",
        kind="passage_status",
        value="clear",
        source="map-fusion",
        observed_at="2026-07-28T01:00:00+00:00",
        evidence_ids=(),
    )
    snapshot = MissionStateSnapshot(
        snapshot_id="mission-1:state:2",
        mission_id="mission-1",
        version=2,
        captured_at="2026-07-28T01:00:00+00:00",
        robots=(),
        environment_facts=(fact,),
    )

    errors = MissionStateSnapshotValidator().validate(snapshot)

    assert "Mission state version greater than 1 requires previous_snapshot_id." in errors
    assert "Mission environment fact 'fact-1' requires evidence_ids." in errors


def test_snapshot_validator_rejects_invalid_battery() -> None:
    base = MissionStateSnapshotBuilder(registry=_registry()).build(
        mission_id="mission-1",
        presence={},
        captured_at="2026-07-28T01:00:00+00:00",
    )
    invalid_robot = replace(base.robots[0], battery_percent=101.0)

    errors = MissionStateSnapshotValidator().validate(
        replace(base, robots=(invalid_robot, *base.robots[1:]))
    )

    assert errors == [
        "Mission robot state 'robot-a' battery_percent must be between 0 and 100."
    ]


def test_system_prompt_marks_snapshot_as_current_authority() -> None:
    snapshot = MissionStateSnapshotBuilder(registry=_registry()).build(
        mission_id="mission-1",
        presence={"robot-a": {"online": True}},
        captured_at="2026-07-28T01:00:00+00:00",
    )
    context = MissionPlannerContext(
        available_robots=_registry().enabled_entries(),
        state_snapshot=snapshot.to_dict(),
    )

    prompt = build_system_prompt(context)

    assert "当前任务状态快照" in prompt
    assert "历史记录不能覆盖它" in prompt
    assert '"snapshot_id": "mission-1:state:1"' in prompt


def test_mission_agent_versions_and_binds_state_snapshot(tmp_path) -> None:
    class Planner:
        def __init__(self) -> None:
            self.contexts = []

        def plan(self, command, *, context):
            self.contexts.append(context)
            return MissionPlanningResult(
                status="planned",
                message="planned",
                intent="search",
                plan=MissionPlan(
                    intent="search",
                    command=command,
                    subtasks=[
                        MissionSubtask(
                            robot_id="robot-a",
                            command="去二楼搜索受困人员",
                            floor=2,
                            capability_required="search_for_victims",
                        )
                    ],
                ),
            )

    class Client:
        def check_presence(self, entry):
            return {
                "robot_id": entry.robot_id,
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 90,
                        "current_floor": 1,
                    }
                },
            }

        def submit_task(self, entry, **kwargs):
            return {
                "status": "accepted",
                "task_id": f"{entry.robot_id}-task",
                "robot_id": entry.robot_id,
            }

    planner = Planner()
    memory = MissionMemoryStore(tmp_path / "mission-memory.jsonl")
    agent = MissionAgent(
        registry=_registry(),
        subagent_client=Client(),
        planner=planner,
        mission_memory=memory,
    )

    first = agent.plan_and_submit(
        "去二楼搜索受困人员",
        session_id="mission-1",
        use_scheduler=False,
    )
    second = agent.plan_and_submit(
        "去二楼搜索受困人员",
        session_id="mission-1",
        use_scheduler=False,
    )

    assert first["state_snapshot"]["snapshot_id"] == "mission-1:state:1"
    assert first["task_graph"]["state_snapshot_id"] == "mission-1:state:1"
    assert first["deliberation"]["status"] == "proposed"
    assert first["deliberation"]["attempts"][0]["outcome"] == "accepted"
    assert planner.contexts[0].state_snapshot == first["state_snapshot"]
    assert second["state_snapshot"]["snapshot_id"] == "mission-1:state:2"
    assert second["state_snapshot"]["previous_snapshot_id"] == "mission-1:state:1"
    observations = memory.list_records(
        mission_id="mission-1",
        record_type="observation",
    )
    assert [item.content["snapshot"]["version"] for item in observations] == [1, 2]
    deliberations = [
        item
        for item in memory.list_records(mission_id="mission-1", record_type="plan")
        if item.content.get("artifact_type") == "mission_deliberation_trace"
    ]
    assert len(deliberations) == 2
