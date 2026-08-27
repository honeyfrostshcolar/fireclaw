from __future__ import annotations

from dataclasses import replace
import json
from unittest.mock import MagicMock

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationLimits,
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext
from fireclaw_core.mission.plan_artifact import (
    validate_plan_2d,
    validate_plan_target_binding,
)
from fireclaw_core.mission.mission_state import (
    MissionEnvironmentFact,
    MissionStateSnapshotBuilder,
)
from fireclaw_core.planner.llm_planner import (
    LLMMissionPlanner,
    TOOL_PROTOCOL_REPAIR_MARKER,
)
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
            capabilities=("victim_search",),
        ),
        RobotRegistryEntry(
            robot_id="robot-b",
            base_url="http://robot-b.test",
            capabilities=("victim_search",),
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
                    }
                },
            },
            "robot-b": {
                "online": True,
                "last_seen_at": "2026-07-28T01:00:00+00:00",
                "state": {
                    "robot_state": {
                        "battery_percent": 85,
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


def _plan_arguments(
    *,
    robot_id: str = "robot-b",
    frame_id: str = "map",
) -> dict:
    return {
        "intent": "search",
        "subtasks": [
            {
                "robot_id": robot_id,
                "command": "前往坐标 (2.0, 1.5) 搜索受困人员",
                "target": {
                    "frame_id": frame_id,
                    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
                },
                "capability_required": "victim_search",
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
                "node_id": "search_point_alpha",
                "task_type": "victim_search",
                "command": "搜索地图坐标 (2.0, 1.5) 周边受困人员",
                "target": {
                    "frame_id": "map",
                    "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
                },
                "capability_required": "victim_search",
                "completion_goal": "目标点周边搜索完成并上报受困人员位置",
                "depends_on": [],
                "execution_mode": "parallel",
            }
        ],
        "knowledge_refs": [],
    }


def _runtime(
    provider: MagicMock,
    *,
    limits: MissionDeliberationLimits | None = None,
):
    registry = _registry()
    snapshot = _snapshot(registry)
    planner = LLMMissionPlanner(provider=provider, model_id="test-model")
    runtime = MissionDeliberationRuntime(
        registry=registry,
        policy=planner,
        limits=limits or MissionDeliberationLimits(),
    )
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
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
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
    first_attempt_manifest = result.attempts[0].context_manifest
    assert first_attempt_manifest is not None
    assert first_attempt_manifest["model_context"]["scope"] == (
        "mission_planner"
    )
    assert first_attempt_manifest["model_context"][
        "used_input_tokens"
    ] > 0

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
    belief_id_schema = (
        graph_schema["function"]["parameters"]["properties"]["nodes"][
            "items"
        ]["properties"]["belief_assumptions"]["items"]["properties"][
            "belief_id"
        ]
    )
    assert "enum" not in belief_id_schema
    assert graph_schema["function"]["parameters"]["properties"]["nodes"][
        "items"
    ]["properties"]["belief_assumptions"]["maxItems"] == 0

    first_turn = json.loads(calls[0].kwargs["messages"][1]["content"])
    second_turn = json.loads(calls[1].kwargs["messages"][1]["content"])
    third_turn = json.loads(calls[2].kwargs["messages"][1]["content"])
    first_context = first_turn["planning_context"]
    second_context = second_turn["planning_context"]
    third_context = third_turn["planning_context"]
    assert first_context["authoritative"]["observations"] == []
    assert "battery_percent" not in calls[0].kwargs["messages"][0]["content"]
    assert second_context["authoritative"]["observations"][0]["data"][
        "robot"
    ]["battery_percent"] == 20.0
    assert [
        item["data"]["robot"]["battery_percent"]
        for item in third_context["authoritative"]["observations"]
    ] == [20.0, 85.0]
    assert all(
        item["authoritative"]["snapshot_contract"]["snapshot_id"]
        == "mission-1:state:1"
        for item in (first_context, second_context, third_context)
    )
    assert first_context["context_policy"][
        "safety_critical_context_preserved"
    ] is True


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
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "proposed"
    assert result.planning_result is not None
    assert result.planning_result.graph_proposal is not None
    assert result.planning_result.plan is not None
    subtask = result.planning_result.plan.subtasks[0]
    assert subtask.node_id == "search_point_alpha"
    assert subtask.robot_id == "robot-b"
    assert subtask.target["pose"] == {
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
    }
    assert result.task_graph is not None
    node = result.task_graph.nodes[0]
    assert node.node_id == "search_point_alpha"
    assert node.robot_id == "robot-b"
    assert node.completion_goal == "目标点周边搜索完成并上报受困人员位置"
    assert {condition.kind for condition in node.preconditions} == {
        "robot_enabled",
        "robot_has_capability",
        "emergency_stop_inactive",
    }
    assert result.to_dict()["graph_proposal"]["nodes"][0]["node_id"] == (
        "search_point_alpha"
    )


def test_llm_policy_receives_parser_error_and_repairs_plan_next_round() -> None:
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "propose_plan",
            _plan_arguments(frame_id="building"),
            call_id="invalid-plan",
        ),
        _response(
            "propose_plan",
            _plan_arguments(),
            call_id="repaired-plan",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
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
    planning_context = second_turn["planning_context"]
    assert "无效的二维 map 目标点" in planning_context["authoritative"][
        "validation_errors"
    ][0]
    assert planning_context["continuity"]["last_plan_proposal"][
        "status"
    ] == "error"


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
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
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
    planning_context = second_turn["planning_context"]
    assert planning_context["authoritative"]["plan_revision"] == 2
    assert planning_context["continuity"]["supersedes_plan_id"] == (
        "mission-1:plan:1"
    )
    assert planning_context["authoritative"][
        "invalidation_evidence_ids"
    ] == [
        "route-blocked-evidence"
    ]
    assert planning_context["authoritative"]["observations"][0]["data"][
        "environment_beliefs"
    ][0]["evidence_ids"] == ["route-blocked-evidence"]
    revision_graph_schema = calls[1].kwargs["tools"][1]
    revision_belief_schema = (
        revision_graph_schema["function"]["parameters"]["properties"][
            "nodes"
        ]["items"]["properties"]["belief_assumptions"]["items"][
            "properties"
        ]["belief_id"]
    )
    assert revision_belief_schema["enum"] == [
        snapshot.environment_beliefs[0].belief_id
    ]


def test_llm_policy_can_request_observation_only_after_belief_inspection() -> None:
    provider = MagicMock()
    runtime, snapshot, context, _ = _runtime(provider)
    fact = MissionEnvironmentFact(
        fact_id="west-corridor-camera",
        kind="passable",
        value=True,
        source="fixed_camera",
        observed_at="2026-07-28T01:00:00+00:00",
        evidence_ids=("camera-frame-1",),
        confidence=0.6,
        subject_id="west-corridor",
    )
    snapshot = MissionStateSnapshotBuilder(
        registry=_registry()
    ).with_environment_facts(snapshot, (fact,))
    belief_id = snapshot.environment_beliefs[0].belief_id
    context = replace(context, state_snapshot=snapshot.to_dict())
    provider.chat_completion.side_effect = [
        _response(
            "inspect_mission_state",
            {
                "kind": "environment_beliefs",
                "subject_id": belief_id,
            },
            call_id="inspect-belief",
        ),
        _response(
            "request_observation",
            {
                "belief_id": belief_id,
                "target": {
                    "frame_id": "map",
                    "area_id": "west-corridor",
                },
                "capability_required": "victim_search",
                "required_sensor": "thermal_camera",
                "reason": "Confirm west stair passability.",
            },
            call_id="observe-belief",
        ),
    ]

    result = runtime.deliberate(
        mission_id="mission-1",
        command="搜索地图区域 west-corridor 的受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "observation_required"
    assert result.observation_request is not None
    assert result.observation_request.belief_id == belief_id
    calls = provider.chat_completion.call_args_list
    first_tools = {
        tool["function"]["name"]
        for tool in calls[0].kwargs["tools"]
    }
    second_tools = {
        tool["function"]["name"]
        for tool in calls[1].kwargs["tools"]
    }
    assert "request_observation" not in first_tools
    assert "request_observation" in second_tools
    assert result.attempts[-1].outcome == "requested"


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
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert unknown.status == "escalated"
    assert unknown.reason_code == "unexpected_tool_name"
    assert unknown.attempts[0].operation == "escalate"
    assert unknown_provider.chat_completion.call_count == 1

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
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert multiple.status == "escalated"
    assert multiple.reason_code == "invalid_llm_decision"
    assert len(multiple.attempts) == 1
    assert multiple_provider.chat_completion.call_count == 2
    repair_messages = multiple_provider.chat_completion.call_args_list[
        1
    ].kwargs["messages"]
    assert TOOL_PROTOCOL_REPAIR_MARKER in json.dumps(repair_messages)


def test_llm_policy_recovers_one_invalid_tool_count_without_dispatch() -> None:
    invalid_response = ChatCompletion(
        content=None,
        tool_calls=[
            ToolCall(
                id="duplicate-one",
                name="inspect_mission_state",
                arguments={"kind": "fleet_state"},
            ),
            ToolCall(
                id="duplicate-two",
                name="inspect_mission_state",
                arguments={"kind": "fleet_state"},
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
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        invalid_response,
        _response(
            "propose_plan",
            _plan_arguments(),
            call_id="repaired-plan",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="前往坐标 (2.0, 1.5) 搜索受困人员",
        state_snapshot=snapshot,
        planner_context=context,
    )

    assert result.status == "proposed"
    assert result.planning_result is not None
    assert result.planning_result.plan is not None
    assert provider.chat_completion.call_count == 2
    assert len(result.attempts) == 1
    assert result.attempts[0].outcome == "accepted"
    repair_messages = provider.chat_completion.call_args_list[1].kwargs[
        "messages"
    ]
    assert TOOL_PROTOCOL_REPAIR_MARKER in json.dumps(repair_messages)


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
        "前往坐标 (2.0, 1.5) 搜索受困人员",
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
            "robot-a": ("navigate_to_waypoint",),
            "robot-b": ("navigate_to_waypoint",),
        },
    )

    result = agent.plan_and_submit(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        session_id="mission-clarify",
        use_scheduler=False,
    )

    assert result["status"] == "clarify"
    assert result["message"] == "请确认受困人员所在区域。"
    assert result["deliberation"]["reason_code"] == "victim_location_required"
    assert client.submissions == []


def test_invented_navigation_target_is_rejected_then_llm_must_clarify() -> None:
    provider = MagicMock()
    invented = _plan_arguments(robot_id="robot-a")
    invented["subtasks"][0]["target"] = {
        "frame_id": "map",
        "pose": {"x": 0.0, "y": 0.0, "yaw": 0.0},
    }
    provider.chat_completion.side_effect = [
        _response("propose_plan", invented, call_id="invented-plan"),
        _response(
            "request_clarification",
            {
                "message": "请提供明确的 map 坐标；当前没有地图候选点证据。",
                "reason_code": "navigation_target_required",
            },
            call_id="clarify-after-rejection",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command="随便往前走到一个没有障碍物的地方",
        state_snapshot=snapshot,
        planner_context=context,
        proposal_validators=(
            validate_plan_target_binding,
            validate_plan_2d,
        ),
    )

    assert result.status == "clarification_required"
    assert result.reason_code == "navigation_target_required"
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "terminal",
    ]
    assert any(
        "not bound to an explicit coordinate" in error
        for error in result.attempts[0].validation_errors
    )
    second_turn = json.loads(
        provider.chat_completion.call_args_list[1].kwargs["messages"][1][
            "content"
        ]
    )
    assert any(
        "not bound to an explicit coordinate" in error
        for error in second_turn["planning_context"]["authoritative"][
            "validation_errors"
        ]
    )
    system_prompt = provider.chat_completion.call_args_list[0].kwargs[
        "messages"
    ][0]["content"]
    assert "绝不能用 (0,0) 等臆造目标代替" in system_prompt


def test_deliberation_iteration_limit_with_unbound_coordinate_falls_back_to_clarification():
    from fireclaw_core.mission.mission_deliberation import MissionDeliberationLimits
    provider = MagicMock()
    # Always try to propose plan with unbound coordinate (99.0, 99.0)
    args = _plan_arguments(robot_id="robot-a")
    args["subtasks"][0]["target"] = {
        "frame_id": "map",
        "pose": {"x": 99.0, "y": 99.0, "yaw": 0.0},
    }
    provider.chat_completion.side_effect = [
        _response(
            "propose_plan",
            args,
            call_id="call-1",
        ),
        _response(
            "propose_plan",
            args,
            call_id="call-2",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(
        provider,
        limits=MissionDeliberationLimits(max_iterations=2),
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="先导航到A点，然后再返回现在的位置",
        state_snapshot=snapshot,
        planner_context=context,
        proposal_validators=(
            validate_plan_target_binding,
            validate_plan_2d,
        ),
    )

    assert result.status == "clarification_required"
    assert result.reason_code == "coordinate_grounding_required"
    assert "请补充说明" in result.message or "请提供" in result.message


def test_repeated_robot_state_read_without_pose_becomes_clarification():
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _response(
            "inspect_mission_state",
            {"kind": "robot_state", "subject_id": "robot-a"},
            call_id="call-1",
        ),
        _response(
            "inspect_mission_state",
            {"kind": "robot_state", "subject_id": "robot-a"},
            call_id="call-2",
        ),
    ]
    runtime, snapshot, context, _ = _runtime(provider)

    result = runtime.deliberate(
        mission_id="mission-1",
        command=(
            "先去坐标 (0.63, 0.54)，再前往 (1.0, 0.0)，"
            "最后返回现在的位置"
        ),
        state_snapshot=snapshot,
        planner_context=context,
        proposal_validators=(
            validate_plan_target_binding,
            validate_plan_2d,
        ),
    )

    assert result.status == "clarification_required"
    assert result.reason_code == "coordinate_grounding_required"
    assert "没有 robot-a 可验证的 map 位姿" in result.message
    assert [attempt.outcome for attempt in result.attempts] == [
        "observed",
        "clarification_requested",
    ]


def test_validate_plan_target_binding_allows_relative_keywords_with_pose_evidence():
    from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask

    plan = MissionPlan(
        intent="search",
        command="先导航到坐标 (0.63, 0.54)，然后再返回现在的位置",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="前往 (0.63, 0.54)",
                floor=1,
                capability_required="victim_search",
                execution_group=0,
                target={"pose": {"x": 0.63, "y": 0.54, "yaw": 0.0}},
            ),
            MissionSubtask(
                robot_id="robot-a",
                command="返回起点",
                floor=1,
                capability_required="victim_search",
                execution_group=1,
                target={"pose": {"x": -2.0, "y": -0.5, "yaw": 0.0}},
            ),
        ],
    )

    # With state snapshot containing robot pose evidence
    snapshot = {
        "robots": [
            {
                "robot_id": "robot-a",
                "pose": {"x": -2.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"},
            }
        ]
    }

    errors = validate_plan_target_binding(plan, state_snapshot=snapshot)
    assert errors == []


def test_validate_plan_target_binding_explicit_axis_format():
    from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask

    plan = MissionPlan(
        intent="navigation",
        command="前往 x=0.63, y=0.54 的位置",
        subtasks=[
            MissionSubtask(
                robot_id="robot-a",
                command="前往目标点",
                floor=1,
                capability_required="navigation",
                execution_group=0,
                target={"pose": {"x": 0.63, "y": 0.54, "yaw": 0.0}},
            ),
        ],
    )

    errors = validate_plan_target_binding(plan)
    assert errors == []
