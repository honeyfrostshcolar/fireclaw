from __future__ import annotations

import hashlib
import json
import re
from urllib import request
from urllib.error import HTTPError

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.gateway.control import DEFAULT_LOCAL_OPERATOR
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationDecision,
)
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.plan_artifact import PlanArtifactError, PlanArtifactStore
from fireclaw_core.mission.runtime_identity import GatewayRuntimeIdentity


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, command, *, context):
        self.calls += 1
        robot = context.available_robots[0]
        return MissionPlanningResult(
            status="planned",
            message="Plan created by canonical planner.",
            intent="search",
            plan=MissionPlan(
                intent="search",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command="在当前地图坐标 (2.0, 1.5) 搜索受困人员",
                        floor=None,
                        capability_required="victim_search",
                        target={
                            "frame_id": "map",
                            "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
                        },
                    )
                ],
            ),
        )


class _RelativePosePlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, command, *, context):
        self.calls += 1
        robot = context.available_robots[0]
        live_pose = context.state_snapshot["robots"][0].get("pose")
        if live_pose is None:
            live_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        pose = {axis: live_pose[axis] for axis in ("x", "y", "yaw")}
        return MissionPlanningResult(
            status="planned",
            message="已绑定当前实时位姿。",
            intent="navigation",
            plan=MissionPlan(
                intent="navigation",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command="返回现在的位置",
                        floor=None,
                        capability_required="navigation",
                        target={"frame_id": "map", "pose": pose},
                    )
                ],
            ),
        )


class _RelativeDefaultYawPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, command, *, context):
        self.calls += 1
        robot = context.available_robots[0]
        live_pose = context.state_snapshot["robots"][0]["pose"]
        return MissionPlanningResult(
            status="planned",
            message="已生成未指定朝向的相对目标。",
            intent="navigation",
            plan=MissionPlan(
                intent="navigation",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command="返回现在的位置",
                        floor=None,
                        capability_required="navigation",
                        target={
                            "frame_id": "map",
                            # Simulate the planner/schema default. The host
                            # must replace this with live yaw before sealing.
                            "pose": {
                                "x": live_pose["x"],
                                "y": live_pose["y"],
                                "yaw": 0.0,
                            },
                        },
                    )
                ],
            ),
        )


class _ClarifyingPlanner:
    supports_mission_deliberation = True

    def __init__(self) -> None:
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        clarifications = request.planner_context.operator_clarifications
        if not clarifications:
            return MissionDeliberationDecision.clarify(
                "我没有地图候选点证据。请提供 map 坐标 (x, y) 和期望 yaw。",
                reason_code="navigation_target_required",
            )
        answer = clarifications[-1]["answer"]
        match = re.search(
            r"[（(]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"(-?\d+(?:\.\d+)?)\s*[)）].*?yaw\s*=\s*"
            r"(-?\d+(?:\.\d+)?)",
            answer,
        )
        if match is None:
            return MissionDeliberationDecision.clarify(
                "仍缺少可解析的 map 坐标和 yaw，请按 (x, y), yaw=value 提供。",
                reason_code="navigation_target_format_invalid",
            )
        x, y, yaw = (float(value) for value in match.groups())
        robot = request.planner_context.available_robots[0]
        return MissionDeliberationDecision.propose(MissionPlanningResult(
            status="planned",
            message="已根据操作员澄清生成导航计划。",
            intent="navigation",
            plan=MissionPlan(
                intent="navigation",
                command=request.command,
                subtasks=[MissionSubtask(
                    robot_id=robot.robot_id,
                    command=f"前往 map 坐标 ({x}, {y})",
                    floor=None,
                    capability_required="navigation",
                    target={
                        "frame_id": "map",
                        "pose": {"x": x, "y": y, "yaw": yaw},
                    },
                )],
            ),
        ))


class _Client:
    def check_presence(self, entry):
        return {"robot_id": entry.robot_id, "online": True, "state": {}}


class _RunManager:
    def __init__(self) -> None:
        self.calls = []

    def submit_preplanned(self, plan, **kwargs):
        self.calls.append((plan, kwargs))
        return {
            "status": "accepted",
            "mission_id": kwargs["mission_id"],
            "run_id": kwargs["mission_id"],
            "run_status": "queued",
            "plan_artifact_id": kwargs["artifact_id"],
            "plan_digest": kwargs["plan_digest"],
            "plan_source": "sealed_plan_artifact",
        }

    def shutdown(self, *, wait=False):
        return None


def _readiness(state: dict[str, object]):
    def evidence(value, source="test-readiness", freshness="fresh"):
        return {
            "value": value,
            "source": source,
            "freshness": freshness,
            "observed_at": "2026-08-20T00:00:00+00:00",
            "evidence_id": "sha256:test",
        }

    robot_value = {
        "status": state.get("robot_status", "online"),
        "last_seen_at": state.get("last_seen_at", "first"),
        "declared_capabilities": ["victim_search", "navigation"],
    }
    robot_state = state.get("robot_state")
    if isinstance(robot_state, dict):
        robot_value["state"] = {"robot_state": dict(robot_state)}
    return {
        "observations": {
            "admission_phase": evidence(state.get("phase", "ready")),
            "admission_safe_state": evidence(state.get("safe_state", "operational")),
            "reason_code": evidence(state.get("reason_code", "ready")),
        },
        "robot_readiness": [
            {
                "robot_id": "robot-1",
                "readiness": evidence(
                    robot_value,
                    source=state.get("robot_source", "robot_gateway_state_probe"),
                    freshness=state.get("freshness", "fresh"),
                ),
            }
        ],
    }


def _gateway(
    tmp_path,
    *,
    mode="simulation",
    state=None,
    store=None,
    planner=None,
):
    profile = tmp_path / "active-profile.toml"
    profile.write_text("[robot]\nrobot_id = 'robot-1'\n", encoding="utf-8")
    digest = hashlib.sha256(profile.read_bytes()).hexdigest()
    identity = GatewayRuntimeIdentity.create(
        runtime_mode=mode,
        active_profile_path=profile,
        profile_sha256=digest,
        robot_id="robot-1",
    )
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("victim_search", "navigation"),
            )
        ]
    )
    planner = planner or _Planner()
    client = _Client()
    agent = MissionAgent(
        registry=registry,
        planner=planner,
        subagent_client=client,
    )
    run_manager = _RunManager()
    mutable_state = state if state is not None else {}
    gateway = MissionGateway(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        runtime_identity=identity,
        plan_artifact_store=store,
        plan_readiness_provider=lambda: _readiness(mutable_state),
        mission_run_manager=run_manager,
        runtime_epoch="test-runtime-epoch",
    )
    return gateway, planner, run_manager, profile, mutable_state


def _confirmation(preview):
    return {
        "artifact_id": preview["artifact_id"],
        "plan_token": preview["plan_token"],
        "plan_digest": preview["plan_digest"],
        "status_version": preview["status_version"],
        "session_id": preview["session_id"],
        "robot_ids": preview["robot_ids"],
        "operator_confirmed": True,
    }


def _json_request(base_url, path, payload):
    req = request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_preview_uses_canonical_planner_and_confirm_executes_same_plan(tmp_path):
    gateway, planner, manager, _, _ = _gateway(tmp_path)

    preview = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    confirmed = gateway.confirm_plan(
        _confirmation(preview),
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert preview["status"] == "preview_ready"
    assert preview["steps"] == [
        {
            "index": 1,
            "node_id": "task-1",
            "robot_id": "robot-1",
            "command": "在当前地图坐标 (2.0, 1.5) 搜索受困人员",
            "capability_required": "victim_search",
            "execution_group": 0,
            "target": {
                "frame_id": "map",
                "pose": {"x": 2.0, "y": 1.5, "yaw": 0.0},
            },
            "risk_level": "medium",
        }
    ]
    assert planner.calls == 1
    assert len(manager.calls) == 1
    queued_plan, queued_bindings = manager.calls[0]
    assert queued_plan.to_dict() == preview["plan"]
    assert queued_bindings["plan_digest"] == preview["plan_digest"]
    assert confirmed["plan_source"] == "sealed_plan_artifact"
    assert confirmed["artifact_status"] == "consumed"


def test_preview_runtime_rejects_invented_pose_without_creating_artifact(tmp_path):
    gateway, planner, manager, _, _ = _gateway(tmp_path)

    result = gateway.plan_mission(
        "随便往前走到一个没有障碍物的地方",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert result["status"] == "blocked"
    assert result["preview_created"] is False
    assert result["reason_code"] == "invalid_plan_proposal"
    assert planner.calls == 1
    assert manager.calls == []


def test_relative_pose_preview_freezes_live_pose_across_confirmation(tmp_path):
    pose = {"x": -1.95, "y": -0.50, "yaw": 0.0, "frame_id": "map"}
    gateway, planner, manager, _, state = _gateway(
        tmp_path,
        planner=_RelativePosePlanner(),
        state={"robot_state": {"pose": pose}},
    )

    preview = gateway.plan_mission(
        "返回现在的位置",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert preview["status"] == "preview_ready"
    assert preview["plan"]["subtasks"][0]["target"]["pose"] == {
        "x": pose["x"],
        "y": pose["y"],
        "yaw": pose["yaw"],
    }
    timing = preview["planning_timing"]
    assert "stages" in timing
    assert "by_stage_ms" in timing
    assert any(item["stage"] == "readiness" for item in timing["stages"])
    assert any(
        item["stage"] == "plan_artifact_seal"
        for item in timing["stages"]
    )
    assert planner.calls == 1

    gateway.confirm_plan(
        _confirmation(preview),
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    assert len(manager.calls) == 1

    # “现在的位置” means the pose observed while interpreting the command. The
    # sealed preview must retain that operator-approved coordinate even if
    # localization changes while the operator is reading the preview.
    second = gateway.plan_mission(
        "返回现在的位置",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    frozen_target = second["plan"]["subtasks"][0]["target"]
    state["robot_state"] = {
        "pose": {"x": -1.0, "y": -0.5, "yaw": 0.0, "frame_id": "map"}
    }
    gateway.confirm_plan(
        _confirmation(second),
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    assert len(manager.calls) == 2
    assert manager.calls[1][0].subtasks[0].target == frozen_target
    assert manager.calls[1][0].subtasks[0].target["pose"]["x"] == pose["x"]


def test_relative_pose_preview_binds_default_yaw_before_sealing(tmp_path):
    pose = {"x": -1.95, "y": -0.50, "yaw": 0.4, "frame_id": "map"}
    planner = _RelativeDefaultYawPlanner()
    gateway, _, manager, _, _ = _gateway(
        tmp_path,
        planner=planner,
        state={"robot_state": {"pose": pose}},
    )

    preview = gateway.plan_mission(
        "返回现在的位置",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert preview["status"] == "preview_ready"
    assert preview["plan"]["subtasks"][0]["target"]["pose"]["yaw"] == 0.4
    assert any(
        item["stage"] == "relative_target_binding"
        and item.get("bound_target_count") == 1
        for item in preview["planning_timing"]["stages"]
    )

    gateway.confirm_plan(
        _confirmation(preview),
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    assert len(manager.calls) == 1
    assert manager.calls[0][0].subtasks[0].target["pose"]["yaw"] == 0.4


def test_relative_pose_preview_without_map_pose_fails_closed(tmp_path):
    gateway, planner, manager, _, _ = _gateway(
        tmp_path,
        planner=_RelativePosePlanner(),
    )

    result = gateway.plan_mission(
        "返回现在的位置",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert result["status"] == "blocked"
    assert result["preview_created"] is False
    assert any(
        "not bound to an explicit coordinate" in error
        for error in result["validation_errors"]
    )
    assert planner.calls == 1
    assert manager.calls == []


def test_llm_clarification_answer_resumes_same_safe_preview_dialogue(tmp_path):
    planner = _ClarifyingPlanner()
    gateway, _, manager, _, _ = _gateway(tmp_path, planner=planner)

    question = gateway.plan_mission(
        "随便往前走到一个没有障碍物的地方",
        target_robot="robot-1",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert question["status"] == "clarification_required"
    assert question["preview_created"] is False
    assert question["reason_code"] == "navigation_target_required"
    assert "地图候选点证据" in question["question"]
    assert manager.calls == []

    preview = gateway.continue_plan_mission(
        question["planning_session_id"],
        "使用 map 坐标 (1.8, -0.1)，yaw=-2.34。",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    assert preview["status"] == "preview_ready"
    assert preview["clarification_rounds"] == 1
    assert preview["plan"]["subtasks"][0]["target"] == {
        "frame_id": "map",
        "pose": {"x": 1.8, "y": -0.1, "yaw": -2.34},
    }
    # Only operator-authored text is sealed; the Agent's sample coordinate in
    # its question cannot accidentally authorize a physical target.
    assert "地图候选点证据" not in preview["plan"]["command"]
    assert "使用 map 坐标 (1.8, -0.1)" in preview["plan"]["command"]
    assert planner.requests[1].planner_context.operator_clarifications[0][
        "answer_source"
    ] == "authenticated_operator"
    assert manager.calls == []

    gateway.confirm_plan(
        _confirmation(preview),
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    assert len(manager.calls) == 1


def test_confirmation_rejects_replay_and_token_tamper(tmp_path):
    gateway, _, manager, _, _ = _gateway(tmp_path)
    preview = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    payload = _confirmation(preview)
    tampered = dict(payload)
    replacement = "A" if payload["plan_token"][-1] != "A" else "B"
    tampered["plan_token"] = payload["plan_token"][:-1] + replacement

    with pytest.raises(PlanArtifactError) as invalid:
        gateway.confirm_plan(tampered, operator=DEFAULT_LOCAL_OPERATOR)
    assert invalid.value.code == "plan_token_invalid"

    gateway.confirm_plan(payload, operator=DEFAULT_LOCAL_OPERATOR)
    with pytest.raises(PlanArtifactError) as replay:
        gateway.confirm_plan(payload, operator=DEFAULT_LOCAL_OPERATOR)
    assert replay.value.code == "plan_token_replayed"
    assert len(manager.calls) == 1


def test_confirmation_rejects_profile_and_readiness_drift(tmp_path):
    gateway, _, manager, profile, state = _gateway(tmp_path)
    first = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    profile.write_text("[robot]\nrobot_id = 'changed'\n", encoding="utf-8")
    with pytest.raises(PlanArtifactError) as profile_drift:
        gateway.confirm_plan(_confirmation(first), operator=DEFAULT_LOCAL_OPERATOR)
    assert profile_drift.value.code == "plan_profile_drift"

    profile.write_text("[robot]\nrobot_id = 'robot-1'\n", encoding="utf-8")
    second = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    state["robot_status"] = "offline"
    with pytest.raises(PlanArtifactError) as readiness_drift:
        gateway.confirm_plan(_confirmation(second), operator=DEFAULT_LOCAL_OPERATOR)
    assert readiness_drift.value.code == "robot_not_ready"
    assert manager.calls == []


def test_confirmation_forbids_client_plan_or_command_override(tmp_path):
    gateway, _, manager, _, _ = _gateway(tmp_path)
    preview = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        operator=DEFAULT_LOCAL_OPERATOR,
    )
    payload = {
        **_confirmation(preview),
        "command": "改成去灭火",
        "plan": {"subtasks": []},
    }

    with pytest.raises(PlanArtifactError) as caught:
        gateway.confirm_plan(payload, operator=DEFAULT_LOCAL_OPERATOR)

    assert caught.value.code == "plan_confirmation_payload_invalid"
    assert manager.calls == []


def test_real_mode_preview_is_visible_but_confirmation_never_dispatches(tmp_path):
    gateway, planner, manager, _, _ = _gateway(tmp_path, mode="real")
    preview = gateway.plan_mission(
        "前往坐标 (2.0, 1.5) 搜索受困人员",
        operator=DEFAULT_LOCAL_OPERATOR,
    )

    with pytest.raises(PlanArtifactError) as caught:
        gateway.confirm_plan(_confirmation(preview), operator=DEFAULT_LOCAL_OPERATOR)

    assert caught.value.code == "real_dispatch_not_authorized"
    assert planner.calls == 1
    assert manager.calls == []
    assert gateway.plan_artifact_store.get(preview["artifact_id"]).status == "invalidated"


def test_http_contract_requires_preview_then_one_time_confirm(tmp_path):
    gateway, planner, manager, _, _ = _gateway(tmp_path)
    gateway.start()
    try:
        preview_status, preview = _json_request(
            gateway.base_url,
            "/plan-mission",
            {
                "task_description": "前往坐标 (2.0, 1.5) 搜索受困人员",
                "target_robot": "robot-1",
            },
        )
        confirm_status, confirmed = _json_request(
            gateway.base_url,
            "/plan-mission/confirm",
            _confirmation(preview),
        )
        replay_status, replay = _json_request(
            gateway.base_url,
            "/plan-mission/confirm",
            _confirmation(preview),
        )
        direct_status, direct = _json_request(
            gateway.base_url,
            "/tasks",
            {"command": "前往坐标 (9.0, 9.0) 搜索受困人员"},
        )
    finally:
        gateway.stop()

    assert preview_status == 200
    assert confirm_status == 202
    assert confirmed["plan_source"] == "sealed_plan_artifact"
    assert replay_status == 409
    assert replay["error_code"] == "plan_token_replayed"
    assert direct_status == 428
    assert direct["error_code"] == "plan_confirmation_required"
    assert planner.calls == 1
    assert len(manager.calls) == 1


def test_http_streams_live_planning_events_before_final_preview(tmp_path):
    gateway, planner, manager, _, _ = _gateway(tmp_path)
    gateway.start()
    try:
        client = MissionGatewayClient(gateway.base_url)
        events = list(client.stream_preview_mission(
            "前往坐标 (2.0, 1.5) 搜索受困人员",
            target_robot="robot-1",
        ))
    finally:
        gateway.stop()

    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "planning.started"
    assert "mission_agent.turn.started" in event_types
    assert "mission_agent.operation.started" in event_types
    assert "mission_agent.attempt.completed" in event_types
    assert event_types[-1] == "planning.result"
    assert [event["sequence"] for event in events] == sorted(
        event["sequence"] for event in events
    )
    assert events[-1]["payload"]["status"] == "preview_ready"
    assert events[-1]["payload"]["dropped_progress_events"] == 0
    assert planner.calls == 1
    assert manager.calls == []


def test_http_contract_continues_gateway_owned_clarification_session(tmp_path):
    planner = _ClarifyingPlanner()
    gateway, _, manager, _, _ = _gateway(tmp_path, planner=planner)
    gateway.start()
    try:
        question_status, question = _json_request(
            gateway.base_url,
            "/plan-mission",
            {
                "command": "随便往前走到一个没有障碍物的地方",
                "target_robot": "robot-1",
            },
        )
        assert manager.calls == []
        preview_status, preview = _json_request(
            gateway.base_url,
            "/plan-mission/clarification",
            {
                "planning_session_id": question["planning_session_id"],
                "answer": "使用 map 坐标 (1.8, -0.1)，yaw=-2.34。",
            },
        )
    finally:
        gateway.stop()

    assert question_status == 200
    assert question["status"] == "clarification_required"
    assert preview_status == 200
    assert preview["status"] == "preview_ready"
    assert preview["session_id"] == question["planning_session_id"]
    assert preview["clarification_rounds"] == 1
    assert manager.calls == []
