from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.completion_contract import (
    CompletionContractCompiler,
    CompletionContractError,
    ExecutionEvidenceValidator,
    TaskTypeDefinition,
    TaskTypeRegistry,
    default_task_type_registry,
)
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationDecision,
)
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_planner import MissionPlanningResult
from fireclaw_core.mission.mission_planning_audit import (
    MissionPlanningAuditRecord,
)
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_scheduler import (
    MissionScheduler,
    MissionSchedulerConfig,
)
from fireclaw_core.mission.revision_dispatcher import (
    JsonlMissionDispatchStore,
)
from fireclaw_core.mission.task_graph import (
    MissionTarget,
    task_graph_from_mission_plan,
)


def _victim_search_contract():
    return CompletionContractCompiler().compile(
        task_type="victim_search",
        capability_required="search_for_victims",
        target=MissionTarget(frame_id="building", floor=2),
        completion_goal="Search floor 2 and report the number of victims found.",
    )


def _terminal_state(*, floor: int = 2, victims_found: int | None = 1):
    data = {"floor": floor}
    if victims_found is not None:
        data["victims_found"] = victims_found
    return {
        "status": "succeeded",
        "updated_at": "2026-07-28T01:00:01+00:00",
        "robot_trace": {
            "status": "succeeded",
            "result": {
                "status": "succeeded",
                "timestamp": "2026-07-28T01:00:01+00:00",
                "execution": {
                    "steps": [
                        {
                            "skill_name": "search_for_victims",
                            "status": "succeeded",
                            "output": {
                                "action": "search_for_victims",
                                "timestamp": "2026-07-28T01:00:01+00:00",
                                "data": data,
                            },
                        }
                    ]
                },
            },
        },
    }


def test_default_task_type_registry_exposes_typed_policies() -> None:
    registry = default_task_type_registry()

    assert "victim_search" in registry.names()
    search = registry.require("victim_search")
    assert search.allowed_capabilities == ("search_for_victims",)
    assert search.default_timeout_seconds == 180.0
    assert search.recovery_policy == "reassign"


def test_task_type_registry_rejects_duplicate_definition() -> None:
    definition = TaskTypeDefinition(
        task_type="inspect",
        allowed_capabilities=("inspect",),
        success_skills=("inspect",),
        default_timeout_seconds=30.0,
        recovery_policy="replan",
    )
    registry = TaskTypeRegistry((definition,))

    with pytest.raises(ValueError, match="Duplicate task type"):
        registry.register(definition)


def test_completion_contract_compiler_rejects_capability_mismatch() -> None:
    with pytest.raises(CompletionContractError, match="does not allow capability"):
        CompletionContractCompiler().compile(
            task_type="victim_search",
            capability_required="navigate",
            target=MissionTarget(frame_id="building", floor=2),
            completion_goal="Search floor 2.",
        )


def test_plan_projection_rejects_declared_contract_without_evidence() -> None:
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="去二楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
                task_type="victim_search",
                completion_goal="Search floor 2.",
                completion_contract={
                    "task_type": "victim_search",
                    "completion_goal": "Search floor 2.",
                    "success_evidence": [],
                },
            )
        ],
    )

    with pytest.raises(ValueError, match="empty completion contract"):
        task_graph_from_mission_plan(
            plan,
            mission_id="mission-1",
            plan_id="mission-1:plan:1",
        )


def test_victim_search_contract_requires_skill_result_and_target_evidence() -> None:
    contract = _victim_search_contract()

    assert {item.kind for item in contract.success_evidence} == {
        "task_terminal_success",
        "skill_succeeded",
        "victim_search_result",
        "target_floor_confirmed",
    }
    assert contract.timeout_seconds == 180.0
    assert contract.recovery_policy == "reassign"
    assert contract.risk_level == "medium"


def test_evidence_validator_accepts_matching_robot_execution() -> None:
    contract = _victim_search_contract()

    result = ExecutionEvidenceValidator().validate(
        requirements=contract.success_evidence,
        terminal_state=_terminal_state(),
        now=datetime(2026, 7, 28, 1, 0, 2, tzinfo=timezone.utc),
    )

    assert result.satisfied is True
    assert all(check.satisfied for check in result.checks)


def test_evidence_validator_accepts_flattened_robot_action_output() -> None:
    contract = _victim_search_contract()
    terminal = _terminal_state()
    output = terminal["robot_trace"]["result"]["execution"]["steps"][0][
        "output"
    ]
    data = output.pop("data")
    output.update(data)

    result = ExecutionEvidenceValidator().validate(
        requirements=contract.success_evidence,
        terminal_state=terminal,
    )

    assert result.satisfied is True


@pytest.mark.parametrize(
    ("terminal_state", "missing_kind"),
    [
        (_terminal_state(victims_found=None), "victim_search_result"),
        (_terminal_state(floor=3), "target_floor_confirmed"),
    ],
)
def test_evidence_validator_rejects_incomplete_or_wrong_success_evidence(
    terminal_state,
    missing_kind: str,
) -> None:
    contract = _victim_search_contract()

    result = ExecutionEvidenceValidator().validate(
        requirements=contract.success_evidence,
        terminal_state=terminal_state,
    )

    assert result.satisfied is False
    failed = {
        check.requirement.kind
        for check in result.checks
        if not check.satisfied
    }
    assert missing_kind in failed


class _EvidenceClient:
    def __init__(self, *, include_result: bool) -> None:
        self.include_result = include_result

    def submit_task(self, entry, **kwargs):
        return {
            "status": "accepted",
            "task_id": f"task-{entry.robot_id}",
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        state = _terminal_state(
            victims_found=1 if self.include_result else None
        )["robot_trace"]
        return {
            "task_id": task_id,
            "robot_id": entry.robot_id,
            **state,
        }

    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-07-28T01:00:00+00:00",
            "state": {},
        }


def _schedule_typed_search(tmp_path, *, include_result: bool):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims",),
        )
    ])
    contract = _victim_search_contract()
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="去二楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
                node_id="search-floor-2",
                task_type="victim_search",
                target=MissionTarget(
                    frame_id="building",
                    floor=2,
                ).to_dict(),
                completion_goal=contract.completion_goal,
                completion_contract=contract.to_dict(),
            )
        ],
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=_EvidenceClient(include_result=include_result),
        mission_registry=JsonlMissionRegistry(tmp_path / "missions.jsonl"),
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(poll_interval_seconds=0.001),
    )
    return scheduler.schedule(plan, mission_id="mission-1")


def test_scheduler_accepts_success_only_after_completion_evidence_validation(
    tmp_path,
) -> None:
    result = _schedule_typed_search(tmp_path, include_result=True)

    assert result["status"] == "succeeded"
    terminal = result["group_results"][0]["terminal_states"][0]
    assert terminal["completion_evidence_validation"]["satisfied"] is True


def test_scheduler_blocks_robot_success_without_required_evidence(tmp_path) -> None:
    result = _schedule_typed_search(tmp_path, include_result=False)

    assert result["status"] == "blocked"
    terminal = result["group_results"][0]["terminal_states"][0]
    assert terminal["status"] == "blocked"
    assert terminal["reported_status"] == "completed"
    assert terminal["completion_evidence_validation"]["satisfied"] is False
    assert terminal["recovery_decision"]["action"] == "replan"
    assert terminal["invalidation_event"]["event_type"] == (
        "completion_evidence_rejected"
    )


def _typed_search_plan(
    robot_id: str,
    *,
    recovery_policy: str | None = None,
) -> MissionPlan:
    contract = _victim_search_contract()
    contract_value = contract.to_dict()
    if recovery_policy is not None:
        contract_value["recovery_policy"] = recovery_policy
    return MissionPlan(
        intent="search",
        command="去二楼搜索受困人员",
        subtasks=[
            MissionSubtask(
                robot_id=robot_id,
                command="去二楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
                node_id="search-floor-2",
                task_type="victim_search",
                target=MissionTarget(
                    frame_id="building",
                    floor=2,
                ).to_dict(),
                completion_goal=contract.completion_goal,
                completion_contract=contract_value,
            )
        ],
    )


def _typed_planning_result(robot_id: str) -> MissionPlanningResult:
    plan = _typed_search_plan(robot_id)
    return MissionPlanningResult(
        status="planned",
        message=f"planned for {robot_id}",
        intent=plan.intent,
        plan=plan,
        audit_record=MissionPlanningAuditRecord(
            command=plan.command,
            available_robots=[],
            tool_schema=None,
            llm_tool_call=None,
            decisions=[],
            final_status="planned",
            final_message="planned",
            created_at="2026-07-28T01:00:00+00:00",
        ),
    )


def test_completion_rejection_returns_to_llm_and_dispatches_revised_plan(
    tmp_path,
) -> None:
    class Policy:
        supports_mission_deliberation = True
        supports_plan_revision = True

        def __init__(self) -> None:
            self.requests = []
            self.decisions = [
                MissionDeliberationDecision.propose(
                    _typed_planning_result("robot-a")
                ),
                MissionDeliberationDecision.inspect(
                    "environment_beliefs",
                    subject_id="completion_evidence_rejected",
                ),
                MissionDeliberationDecision.propose(
                    _typed_planning_result("robot-b")
                ),
            ]

        def decide(self, request):
            self.requests.append(request)
            return self.decisions.pop(0)

    class Client:
        def __init__(self) -> None:
            self.calls = []

        def submit_task(self, entry, **kwargs):
            self.calls.append((entry.robot_id, kwargs))
            return {
                "status": "accepted",
                "task_id": f"task-{entry.robot_id}",
                "robot_id": entry.robot_id,
            }

        def get_task_trace(self, entry, task_id):
            state = _terminal_state(
                victims_found=(
                    None if entry.robot_id == "robot-a" else 1
                )
            )["robot_trace"]
            return {
                "task_id": task_id,
                "robot_id": entry.robot_id,
                **state,
            }

        def check_presence(self, entry):
            return {
                "robot_id": entry.robot_id,
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": (
                            80 if entry.robot_id == "robot-a" else 90
                        ),
                        "current_floor": 1,
                    },
                    "environment_state": {
                        "reachable_floors": [1, 2],
                    },
                    "task_capacity": {
                        "available_execution_slots": 1,
                    },
                    "emergency_stop": {"active": False},
                },
            }

        def cancel_task(self, entry, task_id, *, operator=None):
            return {
                "status": "cancel_requested",
                "task_id": task_id,
                "robot_id": entry.robot_id,
            }

    policy = Policy()
    client = Client()
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims",),
        ),
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("search_for_victims",),
        ),
    ])
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=JsonlMissionRegistry(tmp_path / "missions.jsonl"),
        planner=policy,
    )

    result = agent.plan_and_submit(
        "去二楼搜索受困人员",
        session_id="mission-1",
        use_scheduler=True,
    )

    assert result["status"] == "succeeded"
    assert [robot_id for robot_id, _ in client.calls] == [
        "robot-a",
        "robot-b",
    ]
    assert len(policy.requests) == 3
    revision_snapshot = policy.requests[1].state_snapshot
    assert any(
        fact.kind == "completion_evidence_rejected"
        and fact.value == "search-floor-2"
        for fact in revision_snapshot.environment_facts
    )
    assert any(
        fact.kind == "completion_requirement_failed"
        and fact.value == "victim_search_result"
        for fact in revision_snapshot.environment_facts
    )
    assert result["plan_revision"]["status"] == "revised"
    assert result["active_task_graph"]["plan_id"] == "mission-1:plan:2"


def test_incomplete_result_gets_one_persisted_result_recheck(tmp_path) -> None:
    plan = _typed_search_plan("robot-a", recovery_policy="retry")

    class Policy:
        supports_mission_deliberation = True
        supports_plan_revision = True

        def __init__(self) -> None:
            self.requests = []

        def decide(self, request):
            self.requests.append(request)
            return MissionDeliberationDecision.propose(
                MissionPlanningResult(
                    status="planned",
                    message="planned",
                    intent=plan.intent,
                    plan=plan,
                    audit_record=MissionPlanningAuditRecord(
                        command=plan.command,
                        available_robots=[],
                        tool_schema=None,
                        llm_tool_call=None,
                        decisions=[],
                        final_status="planned",
                        final_message="planned",
                        created_at="2026-07-28T01:00:00+00:00",
                    ),
                )
            )

    class Client:
        def __init__(self) -> None:
            self.task_count = 0
            self.trace_count = 0

        def submit_task(self, entry, **kwargs):
            self.task_count += 1
            return {
                "status": "accepted",
                "task_id": f"task-{self.task_count}",
                "robot_id": entry.robot_id,
            }

        def get_task_trace(self, entry, task_id):
            self.trace_count += 1
            if self.trace_count == 1:
                return {
                    "task_id": task_id,
                    "robot_id": entry.robot_id,
                    "status": "succeeded",
                    "result": {"status": "succeeded"},
                }
            state = _terminal_state()["robot_trace"]
            return {
                "task_id": task_id,
                "robot_id": entry.robot_id,
                **state,
            }

        def check_presence(self, entry):
            return {
                "robot_id": entry.robot_id,
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 90,
                        "current_floor": 1,
                    },
                    "environment_state": {
                        "reachable_floors": [1, 2],
                    },
                    "task_capacity": {
                        "available_execution_slots": 1,
                    },
                    "emergency_stop": {"active": False},
                },
            }

    client = Client()
    policy = Policy()
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims",),
        )
    ])
    mission_path = tmp_path / "missions.jsonl"
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=JsonlMissionRegistry(mission_path),
        planner=policy,
    )

    result = agent.plan_and_submit(
        plan.command,
        session_id="mission-1",
        use_scheduler=True,
    )

    assert result["status"] == "succeeded"
    assert client.task_count == 1
    assert client.trace_count >= 2
    assert len(policy.requests) == 1
    first_terminal = result["group_results"][0]["terminal_states"][0]
    assert first_terminal["recovery_decision"]["action"] == "recheck"
    checkpoint = JsonlMissionDispatchStore(
        tmp_path / "missions.dispatch.jsonl"
    ).latest("mission-1")
    assert checkpoint is not None
    assert checkpoint.node_executions[0].recovery_attempt == 1
    assert checkpoint.node_executions[0].recovery_action == "recheck"
