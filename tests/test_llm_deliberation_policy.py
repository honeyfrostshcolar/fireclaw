from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import MagicMock

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshotBuilder,
)
from fireclaw_core.planner.llm_planner import LLMMissionPlanner
from fireclaw_core.provider.provider import (
    ChatCompletion,
    TokenUsage,
    ToolCall,
)


def _registry() -> RobotRegistry:
    return RobotRegistry([
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


def _snapshot(registry: RobotRegistry):
    return MissionStateSnapshotBuilder(registry=registry).build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 20,
                        "current_floor": 1,
                    }
                },
            },
            "robot-b": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 85,
                        "current_floor": 1,
                    }
                },
            },
        },
        captured_at="2026-07-28T01:00:01+00:00",
    )


def _response(name: str, arguments: dict, *, call_id: str) -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
            )
        ],
        usage=TokenUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        ),
        model="test-model",
        finish_reason="tool_calls",
    )


def _plan_arguments(*, robot_id: str = "robot-b", floor: int = 2) -> dict:
    return {
        "intent": "search",
        "subtasks": [
            {
                "robot_id": robot_id,
                "command": "去二楼搜索受困人员",
                "floor": floor,
                "capability_required": "search_for_victims",
                "execution_group": 0,
            }
        ],
        "knowledge_refs": [],
    }


def _graph_arguments() -> dict:
    return {
        "intent": "search",
        "nodes": [
            {
                "node_id": "search_second_floor",
                "task_type": "victim_search",
                "command": "搜索二楼受困人员",
                "target": {
                    "frame_id": "building",
                    "floor": 2,
                    "area_id": "second_floor",
                },
                "capability_required": "search_for_victims",
                "completion_goal": "二楼搜索完成并上报受困人员位置",
                "depends_on": [],
                "execution_mode": "parallel",
            }
        ],
        "knowledge_refs": [],
    }


def _runtime(provider: MagicMock):
    registry = _registry()
    snapshot = _snapshot(registry)
    planner = LLMMissionPlanner(provider=provider, model_id="test-model")
    runtime = MissionDeliberationRuntime(registry=registry, policy=planner)
    context = MissionPlannerContext(
        available_robots=registry.enabled_entries(),
        state_snapshot=snapshot.to_dict(),
    )
    return runtime, snapshot, context, planner


def test_llm_policy_reads_distinct_snapshot_views_then_proposes() -> None:
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "inspect_mission_state",
            {"kind": "robot_state", "subject_id": "robot-a"},
            call_id="read-a",
        ),
        _response(
            "inspect_mission_state",
            {"kind": "robot_state", "subject_id": "robot-b"},
            call_id="read-b",
        ),
        _response(
            "propose_plan",
            _plan_arguments(robot_id="robot-b"),
            call_id="plan",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "proposed"
    assert result.planning_result is not None
    assert result.planning_result.plan is not None
    assert result.planning_result.plan.subtasks[0].robot_id == "robot-b"
    assert [attempt.outcome for attempt in result.attempts] == [
        "observed",
        "observed",
        "accepted",
    ]
    assert provider.chat_completion.call_count == 3

    calls = provider.chat_completion.call_args_list
    tool_names = [
        tool["function"]["name"]
        for tool in calls[0].kwargs["tools"]
    ]
    assert tool_names == [
        "inspect_mission_state",
        "propose_task_graph",
        "propose_plan",
        "request_clarification",
        "escalate",
    ]
    graph_schema = calls[0].kwargs["tools"][1]
    assert "robot_id" not in json.dumps(graph_schema)

    first_turn = json.loads(calls[0].kwargs["messages"][1]["content"])
    second_turn = json.loads(calls[1].kwargs["messages"][1]["content"])
    third_turn = json.loads(calls[2].kwargs["messages"][1]["content"])
    assert first_turn["observations"] == []
    assert "battery_percent" not in calls[0].kwargs["messages"][0]["content"]
    assert second_turn["observations"][0]["data"]["robot"]["battery_percent"] == 20.0
    assert [
        item["data"]["robot"]["battery_percent"]
        for item in third_turn["observations"]
    ] == [20.0, 85.0]
    assert all(
        item["snapshot_id"] == "mission-1:state:1"
        for item in (first_turn, second_turn, third_turn)
    )


def test_llm_policy_compiles_semantic_graph_and_allocates_robot() -> None:
    provider = MagicMock()
    provider.chat_completion.return_value = _response(
        "propose_task_graph",
        _graph_arguments(),
        call_id="semantic-graph",
    )
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "proposed"
    assert result.planning_result is not None
    assert result.planning_result.graph_proposal is not None
    assert result.planning_result.plan is not None
    subtask = result.planning_result.plan.subtasks[0]
    assert subtask.node_id == "search_second_floor"
    assert subtask.robot_id == "robot-b"
    assert subtask.target["area_id"] == "second_floor"
    assert result.task_graph is not None
    node = result.task_graph.nodes[0]
    assert node.node_id == "search_second_floor"
    assert node.robot_id == "robot-b"
    assert node.completion_goal == "二楼搜索完成并上报受困人员位置"
    assert {condition.kind for condition in node.preconditions} == {
        "robot_enabled",
        "robot_has_capability",
        "emergency_stop_inactive",
    }
    assert result.to_dict()["graph_proposal"]["nodes"][0]["node_id"] == (
        "search_second_floor"
    )


def test_llm_policy_receives_parser_error_and_repairs_plan_next_round() -> None:
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "propose_plan",
            _plan_arguments(floor=0),
            call_id="invalid-plan",
        ),
        _response(
            "propose_plan",
            _plan_arguments(floor=2),
            call_id="repaired-plan",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "proposed"
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "accepted",
    ]
    second_turn = json.loads(
        provider.chat_completion.call_args_list[1].kwargs["messages"][1]["content"]
    )
    assert "无效楼层" in second_turn["validation_errors"][0]
    assert second_turn["last_plan_proposal"]["status"] == "error"


def test_llm_policy_reads_invalidation_evidence_before_plan_revision() -> None:
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "inspect_mission_state",
            {"kind": "environment_beliefs", "subject_id": "route_blocked"},
            call_id="read-invalidation",
        ),
        _response(
            "propose_plan",
            _plan_arguments(robot_id="robot-b"),
            call_id="revision",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)
    fact = MissionEnvironmentFact(
        fact_id="route-blocked",
        kind="route_blocked",
        value="task-1",
        source="execution_monitor",
        observed_at="2026-07-28T01:00:01+00:00",
        evidence_ids=("route-blocked-evidence",),
        confidence=1.0,
        subject_id="task-1",
    )
    snapshot = MissionStateSnapshotBuilder(
        registry=_registry()
    ).with_environment_facts(
        replace(
            snapshot,
            snapshot_id="mission-1:state:2",
            version=2,
            previous_snapshot_id="mission-1:state:1",
        ),
        (fact,),
        extra_evidence_ids=("route-blocked-evidence",),
    )
    context = replace(context, state_snapshot=snapshot.to_dict())

    result = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
        plan_revision=2,
        supersedes_plan_id="mission-1:plan:1",
        invalidation_evidence_ids=("route-blocked-evidence",),
    )

    assert result.status == "proposed"
    assert result.task_graph is not None
    assert result.task_graph.revision == 2
    calls = provider.chat_completion.call_args_list
    assert "必须调用 inspect_mission_state" in calls[0].kwargs["messages"][0][
        "content"
    ]
    second_turn = json.loads(calls[1].kwargs["messages"][1]["content"])
    assert second_turn["plan_revision"] == 2
    assert second_turn["supersedes_plan_id"] == "mission-1:plan:1"
    assert second_turn["invalidation_evidence_ids"] == [
        "route-blocked-evidence"
    ]
    assert second_turn["observations"][0]["data"]["environment_beliefs"][0][
        "evidence_ids"
    ] == ["route-blocked-evidence"]


def test_llm_policy_escalates_unknown_or_multiple_tool_calls() -> None:
    unknown_provider = MagicMock()
    unknown_provider.chat_completion.return_value = _response(
        "drive_robot",
        {"robot_id": "robot-b"},
        call_id="unsafe-tool",
    )
    runtime, snapshot, context, _ = _runtime(unknown_provider)

    unknown = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert unknown.status == "escalated"
    assert unknown.reason_code == "unexpected_tool_name"
    assert unknown.attempts[0].operation == "escalate"

    multiple_provider = MagicMock()
    multiple_provider.chat_completion.return_value = ChatCompletion(
        content=None,
        tool_calls=[
            ToolCall(
                id="one",
                name="inspect_mission_state",
                arguments={"kind": "fleet_state"},
            ),
            ToolCall(
                id="two",
                name="propose_plan",
                arguments=_plan_arguments(),
            ),
        ],
        usage=TokenUsage(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
        ),
        model="test-model",
        finish_reason="tool_calls",
    )
    runtime, snapshot, context, _ = _runtime(multiple_provider)

    multiple = runtime.deliberate(
        mission_id="mission-1",
        command="去二楼救人",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert multiple.status == "escalated"
    assert multiple.reason_code == "invalid_llm_decision"
    assert len(multiple.attempts) == 1


def test_mission_agent_uses_llm_planner_as_deliberation_policy() -> None:
    registry = _registry()
    planner = LLMMissionPlanner(provider=MagicMock(), model_id="test-model")

    agent = MissionAgent(registry=registry, planner=planner)

    assert agent.mission_deliberation_runtime is not None
    assert agent.mission_deliberation_runtime.policy is planner


def test_mission_agent_runs_llm_read_then_plan_flow_before_dispatch() -> None:
    class AgentClient:
        def __init__(self) -> None:
            self.submissions = []

        def check_presence(self, entry):
            battery = 20 if entry.robot_id == "robot-a" else 85
            return {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": battery,
                        "current_floor": 1,
                    }
                },
            }

        def submit_task(self, entry, **kwargs):
            self.submissions.append((entry.robot_id, kwargs))
            return {
                "status": "accepted",
                "task_id": f"task-{entry.robot_id}",
            }

    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "inspect_mission_state",
            {"kind": "robot_state", "subject_id": "robot-b"},
            call_id="read-b",
        ),
        _response(
            "propose_plan",
            _plan_arguments(robot_id="robot-b"),
            call_id="plan",
        ),
    ]
    registry = _registry()
    client = AgentClient()
    planner = LLMMissionPlanner(provider=provider, model_id="test-model")
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
    )

    result = agent.plan_and_submit(
        "去二楼救人",
        session_id="mission-1",
        use_scheduler=False,
    )

    assert result["status"] == "planned"
    assert [
        attempt["outcome"]
        for attempt in result["deliberation"]["attempts"]
    ] == ["observed", "accepted"]
    assert result["deliberation"]["observations"][0]["data"]["robot"][
        "battery_percent"
    ] == 85.0
    assert client.submissions[0][0] == "robot-b"
    assert provider.chat_completion.call_count == 2


def test_explicit_llm_clarification_cannot_trigger_primitive_fallback() -> None:
    class AgentClient:
        def __init__(self) -> None:
            self.submissions = []

        def check_presence(self, entry):
            return {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 85,
                        "current_floor": 1,
                    }
                },
            }

        def submit_task(self, entry, **kwargs):
            self.submissions.append((entry.robot_id, kwargs))
            return {"status": "accepted", "task_id": "unexpected-task"}

    provider = MagicMock()
    provider.chat_completion.return_value = _response(
        "request_clarification",
        {
            "message": "请确认受困人员所在区域。",
            "reason_code": "victim_location_required",
        },
        call_id="clarify",
    )
    registry = _registry()
    client = AgentClient()
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=LLMMissionPlanner(provider=provider, model_id="test-model"),
        primitive_skills_by_robot={
            "robot-a": ("navigate_to_floor",),
            "robot-b": ("navigate_to_floor",),
        },
    )

    result = agent.plan_and_submit(
        "去二楼救人",
        session_id="mission-clarify",
        use_scheduler=False,
    )

    assert result["status"] == "clarify"
    assert result["message"] == "请确认受困人员所在区域。"
    assert result["deliberation"]["reason_code"] == "victim_location_required"
    assert client.submissions == []
