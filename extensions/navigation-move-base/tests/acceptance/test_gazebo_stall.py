from __future__ import annotations

from datetime import datetime, timezone
from math import hypot
import os
from pathlib import Path
import platform
import sys
from typing import Any

import pytest

from .artifacts import ArtifactBundle, asset_hashes, repository_snapshot
from .fireclaw_harness import (
    build_mission_agent,
    confirm_pending_authorization,
    create_robot_gateway,
    inspect_navigation_plugin,
    install_adapter_navigation_trap,
    robot_task_id,
    run_mission_until_authorization,
    wait_for_mission_run,
    wait_for_robot_task,
)
from .scenario import AcceptanceScenario, Pose2D


pytestmark = pytest.mark.gazebo_acceptance


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pose_dict(pose: Pose2D) -> dict[str, Any]:
    return {
        "frame_id": pose.frame_id,
        "x": pose.x,
        "y": pose.y,
        "yaw": pose.yaw,
    }


def _event_index(
    events: list[dict[str, Any]],
    event_type: str,
    *,
    tool_name: str | None = None,
    after: int = -1,
) -> int:
    for index, event in enumerate(events):
        if index <= after or event.get("type") != event_type:
            continue
        payload = event.get("payload")
        if tool_name is not None and (
            not isinstance(payload, dict)
            or payload.get("tool_name") != tool_name
        ):
            continue
        return index
    raise AssertionError(
        f"missing event {event_type!r} after index {after} "
        f"for tool {tool_name!r}"
    )


def _diagnostic_output(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return {}
    output = payload.get("output")
    return dict(output) if isinstance(output, dict) else {}


def _action_terminals(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    terminal_types = {
        "action.succeeded",
        "action.failed",
        "action.cancelled",
        "action.timed_out",
        "action.lost",
        "action.escalated",
    }
    return [
        event for event in events if event.get("type") in terminal_types
    ]


def test_diagnostics_first_navigation_stall(
    repo_root: Path,
    acceptance_scenario: AcceptanceScenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if scenario.scenario_type not in {
        "stall_recover",
        "stall_escalate",
    }:
        pytest.skip("selected acceptance scenario is not a stall lane")
    stall = scenario.stall
    assert stall is not None
    recover = scenario.scenario_type == "stall_recover"
    bundle = artifact_bundle
    manifest: dict[str, Any] = {
        "schema_version": "fireclaw.gazebo-acceptance-proof/v1",
        "run_id": bundle.run_id,
        "scenario": scenario.to_manifest(),
        "status": "running",
        "started_at": _utc_now(),
        "repository": repository_snapshot(repo_root),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "ros_distro": os.getenv("ROS_DISTRO"),
            "ros_master_uri": os.getenv("ROS_MASTER_URI"),
            "gazebo_master_uri": os.getenv("GAZEBO_MASTER_URI"),
        },
        "asset_hashes": asset_hashes(
            {
                name: Path(path)
                for name, path in scenario.assets.to_dict().items()
            }
        ),
    }
    bundle.write_json("run-manifest.json", manifest)

    recorder = ros_harness.recorder()
    gateway = None
    mission_manager = None
    mission_agent = None
    mission_events: list[dict[str, Any]] = []
    robot_events: list[dict[str, Any]] = []
    authorization_events: list[dict[str, Any]] = []
    authorization_trace: dict[str, Any] = {}
    robot_trace: dict[str, Any] = {}
    mission_trace: dict[str, Any] = {}
    final_report: dict[str, Any] = {}
    plugin_inventory: dict[str, Any] = {}
    pose_evidence: dict[str, Any] = {}
    navigation_parameters_before: dict[str, Any] = {}
    navigation_parameters_after: dict[str, Any] = {}
    diagnostics_evidence: dict[str, Any] = {}
    stall_evidence: dict[str, Any] = {}
    ros_graph: dict[str, Any] = {}
    trap_calls: list[dict[str, Any]] = []
    failure: BaseException | None = None

    try:
        readiness = ros_harness.wait_until_ready()
        bundle.write_json("readiness.json", readiness)
        initial_pose = ros_harness.current_pose()
        navigation_parameters_before = ros_harness.navigation_parameters()
        assert navigation_parameters_before["dwa"] == {
            "namespace": "/move_base/DWAPlannerROS",
            **stall.stalled_parameters,
        }

        gateway = create_robot_gateway(
            scenario,
            bundle,
            repo_root=repo_root,
        )
        inspection = inspect_navigation_plugin(gateway, scenario)
        plugin_inventory = inspection.to_dict()
        bundle.write_json("plugin-inventory.json", plugin_inventory)
        assert inspection.owner_plugin_id == scenario.plugin_owner
        assert inspection.contribution_id == scenario.required_tool
        assert inspection.backend_class == scenario.backend_class
        assert inspection.action_name == scenario.action_name
        assert inspection.timeout_seconds == pytest.approx(
            stall.execution_timeout_seconds
        )

        trap_calls = install_adapter_navigation_trap(gateway)
        gateway.start()
        mission_agent = build_mission_agent(
            scenario,
            bundle,
            robot_gateway_base_url=gateway.base_url,
        )
        mission_manager, run = run_mission_until_authorization(
            mission_agent,
            scenario,
            event_sink=lambda event_type, mission_id, payload: (
                mission_events.append(
                    {
                        "captured_at": _utc_now(),
                        "type": event_type,
                        "mission_id": mission_id,
                        "payload": dict(payload),
                    }
                )
            ),
        )
        mission_id = str(run["mission_id"])
        authorization_task_id = robot_task_id(run)
        manifest["mission_id"] = mission_id
        manifest["authorization_task_id"] = authorization_task_id
        entry = mission_agent.registry.get(scenario.robot_id)
        assert entry is not None
        authorization_trace = mission_agent.subagent_client.get_task_trace(
            entry,
            authorization_task_id,
        )
        authorization_events = gateway.events.events_for_task(
            authorization_task_id
        )
        assert run["terminal"] is False
        assert authorization_trace.get("status") == (
            scenario.mission_authorization_status
        )
        authorization_event_types = [
            str(event.get("type")) for event in authorization_events
        ]
        for expected_event in (
            "authorization.requested",
            "confirmation.pending",
            "task.awaiting_confirmation",
        ):
            assert expected_event in authorization_event_types
        assert "task.escalated" not in authorization_event_types

        confirmed = confirm_pending_authorization(
            gateway.base_url,
            session_id=mission_id,
        )
        execution_started_at = _utc_now()
        task_id = str(confirmed["task_id"])
        manifest["task_id"] = task_id
        assert task_id == authorization_task_id

        robot_trace = wait_for_robot_task(
            mission_agent.subagent_client,
            entry,
            task_id,
            timeout_seconds=stall.terminal_timeout_seconds,
        )
        robot_events = gateway.events.events_for_task(task_id)
        completed_run = wait_for_mission_run(
            mission_manager,
            mission_id,
            timeout_seconds=scenario.mission_seconds,
        )
        mission_trace = mission_agent.mission_trace(mission_id)
        final_report = dict(completed_run.get("final_report") or {})
        stopped = ros_harness.wait_until_stopped()
        final_pose = ros_harness.current_pose()
        navigation_parameters_after = ros_harness.navigation_parameters()

        goals = recorder.goals()
        expected_goal_count = 2 if recover else 1
        assert len(goals) == expected_goal_count
        for goal in goals:
            assert goal["frame_id"] == "map"
            assert goal["x"] == pytest.approx(scenario.goal.x, abs=1e-6)
            assert goal["y"] == pytest.approx(scenario.goal.y, abs=1e-6)
        first_goal_id = str(goals[0]["goal_id"])
        first_terminal = recorder.wait_for_goal_status(
            first_goal_id,
            stall.first_actionlib_terminal_statuses,
            timeout_seconds=5.0,
        )
        first_feedback = [
            item
            for item in recorder.feedback()
            if str(item.get("goal_id")) == first_goal_id
        ]
        first_max_displacement = max(
            (
                hypot(
                    float(item["x"]) - initial_pose.x,
                    float(item["y"]) - initial_pose.y,
                )
                for item in first_feedback
            ),
            default=0.0,
        )
        assert len(first_feedback) >= stall.minimum_feedback_count
        assert first_max_displacement <= stall.maximum_stall_displacement_m

        action_terminals = _action_terminals(robot_events)
        assert len(action_terminals) == expected_goal_count
        assert action_terminals[0]["type"] == "action.timed_out"
        first_action_output = action_terminals[0]["payload"]["output"]
        assert first_action_output["goal_state"] in (
            stall.first_actionlib_terminal_statuses
        )
        assert first_action_output["runtime_stopped"] is True
        assert first_action_output["resource_release_safe"] is True

        diagnostic_events = [
            event
            for event in robot_events
            if event.get("type") == "agent_tool.execution"
            and event.get("payload", {}).get("tool_name")
            == "navigation_diagnostics"
        ]
        assert len(diagnostic_events) == 1
        diagnostic_event = diagnostic_events[0]
        assert diagnostic_event["payload"]["status"] == "executed"
        diagnostics_evidence = _diagnostic_output(diagnostic_event)
        finding_codes = {
            str(item.get("code"))
            for item in diagnostics_evidence.get("findings", [])
            if isinstance(item, dict) and item.get("code")
        }
        assert diagnostics_evidence.get("status") == "ok"
        assert set(stall.required_diagnostic_finding_codes).issubset(
            finding_codes
        )
        diagnostic_evidence_id = diagnostics_evidence.get("evidence_id")
        assert isinstance(diagnostic_evidence_id, str)
        assert diagnostic_evidence_id

        first_action_index = _event_index(
            robot_events,
            "action.timed_out",
        )
        diagnostic_index = _event_index(
            robot_events,
            "agent_tool.execution",
            tool_name="navigation_diagnostics",
            after=first_action_index,
        )
        task_terminal_event = f"task.{scenario.terminal_status}"

        deliberation = robot_trace.get("result", {}).get(
            "robot_agent_deliberation",
            {},
        )
        deliberation_result = deliberation.get("result", {})
        assert diagnostic_evidence_id in deliberation_result.get(
            "evidence_ids",
            [],
        )

        if recover:
            recovery_events = [
                event
                for event in robot_events
                if event.get("type") == "agent_tool.execution"
                and event.get("payload", {}).get("tool_name")
                == "move_base_set_parameters"
            ]
            assert len(recovery_events) == stall.maximum_recovery_attempts
            recovery_event = recovery_events[0]
            assert recovery_event["payload"]["status"] == "executed"
            assert _diagnostic_output(recovery_event)["status"] == (
                "succeeded"
            )
            recovery_index = _event_index(
                robot_events,
                "agent_tool.execution",
                tool_name="move_base_set_parameters",
                after=diagnostic_index,
            )
            second_action_index = _event_index(
                robot_events,
                "action.requested",
                after=recovery_index,
            )
            success_index = _event_index(
                robot_events,
                "action.succeeded",
                after=second_action_index,
            )
            task_index = _event_index(
                robot_events,
                task_terminal_event,
                after=success_index,
            )
            assert task_index > success_index
            assert action_terminals[1]["type"] == "action.succeeded"
            second_goal_id = str(goals[1]["goal_id"])
            second_terminal = recorder.wait_for_goal_status(
                second_goal_id,
                stall.final_actionlib_terminal_statuses,
                timeout_seconds=5.0,
            )
            assert second_terminal["status"] == 3
            assert navigation_parameters_after["dwa"] == {
                "namespace": "/move_base/DWAPlannerROS",
                **dict(stall.recovery_parameters or {}),
            }
            position_error, yaw_error = scenario.goal_error(final_pose)
            displacement = hypot(
                final_pose.x - initial_pose.x,
                final_pose.y - initial_pose.y,
            )
            assert position_error <= scenario.position_tolerance_m
            assert yaw_error <= scenario.yaw_tolerance_rad
            assert displacement >= scenario.minimum_displacement_m
            assert robot_trace.get("status") == "completed"
            assert completed_run.get("status") == "completed"
            # Mission Registry retains its compatibility aggregate success
            # label while Mission Run and Robot task use canonical completed.
            assert mission_trace.get("status") == "succeeded"
            assert final_report.get("status") == "completed"
            forbidden = {
                "task.escalated",
                "mission.escalated",
                "action.failed",
                "action.lost",
            }
            assert forbidden.isdisjoint(
                {str(event.get("type")) for event in robot_events}
                | {str(event.get("type")) for event in mission_events}
            )
        else:
            assert not [
                event
                for event in robot_events
                if event.get("type") == "agent_tool.execution"
                and event.get("payload", {}).get("tool_name")
                in {"move_base_set_parameters", "move_base_clear_costmaps"}
            ]
            assert len(
                [
                    event
                    for event in robot_events
                    if event.get("type") == "action.requested"
                ]
            ) == 1
            task_index = _event_index(
                robot_events,
                task_terminal_event,
                after=diagnostic_index,
            )
            assert task_index > diagnostic_index
            assert navigation_parameters_after["dwa"] == {
                "namespace": "/move_base/DWAPlannerROS",
                **stall.stalled_parameters,
            }
            displacement = hypot(
                final_pose.x - initial_pose.x,
                final_pose.y - initial_pose.y,
            )
            assert displacement <= stall.maximum_stall_displacement_m
            assert robot_trace.get("status") == "escalated"
            assert completed_run.get("status") == "escalated"
            assert mission_trace.get("status") == "escalated"
            assert final_report.get("status") == "escalated"
            assert deliberation.get("reason_code") == (
                stall.escalation_reason_code
            )
            assert deliberation_result.get("reason_code") == (
                stall.escalation_reason_code
            )
            mission_event_types = {
                str(event.get("type")) for event in mission_events
            }
            assert "mission.escalated" in mission_event_types
            assert "mission.completed" not in mission_event_types

        assert completed_run.get("terminal") is True
        assert robot_trace.get("result", {}).get("status") == (
            scenario.terminal_status
        )
        assert not trap_calls
        task_terminal_types = {
            "task.completed",
            "task.blocked",
            "task.escalated",
            "task.failed",
            "task.timed_out",
            "task.cancelled",
            "task.lost",
        }
        task_terminals = [
            event
            for event in robot_events
            if event.get("type") in task_terminal_types
        ]
        assert len(task_terminals) == 1
        assert task_terminals[0]["type"] == task_terminal_event
        assert len(
            {
                event.get("payload", {}).get("action_id")
                for event in action_terminals
            }
        ) == expected_goal_count

        position_error, yaw_error = scenario.goal_error(final_pose)
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "goal": _pose_dict(scenario.goal),
            "final_pose": _pose_dict(final_pose),
            "goal_position_error_m": position_error,
            "goal_yaw_error_rad": yaw_error,
            "first_attempt_max_displacement_m": first_max_displacement,
            "stopped": stopped,
        }
        stall_evidence = {
            "execution_started_at": execution_started_at,
            "mode": scenario.scenario_type,
            "goal_ids": [str(goal["goal_id"]) for goal in goals],
            "first_goal_terminal": first_terminal,
            "first_goal_feedback_count": len(first_feedback),
            "first_attempt_max_displacement_m": first_max_displacement,
            "action_terminals": action_terminals,
            "diagnostic_evidence_id": diagnostic_evidence_id,
            "diagnostic_finding_codes": sorted(finding_codes),
            "recovery_attempt_count": (
                stall.maximum_recovery_attempts if recover else 0
            ),
            "navigation_parameters_before": navigation_parameters_before,
            "navigation_parameters_after": navigation_parameters_after,
        }
        bundle.write_json("stall-evidence.json", stall_evidence)
        bundle.write_json(
            "navigation-diagnostics.json",
            diagnostics_evidence,
        )
        manifest["status"] = "passed"
    except BaseException as exc:
        failure = exc
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if (
            failure is not None
            and gateway is not None
            and mission_agent is not None
            and isinstance(manifest.get("task_id"), str)
        ):
            entry = mission_agent.registry.get(scenario.robot_id)
            if entry is not None:
                current_trace = mission_agent.subagent_client.get_task_trace(
                    entry,
                    str(manifest["task_id"]),
                )
                if current_trace.get("status") not in {
                    "blocked",
                    "cancelled",
                    "completed",
                    "escalated",
                    "failed",
                    "lost",
                    "timed_out",
                }:
                    mission_agent.subagent_client.cancel_task(
                        entry,
                        str(manifest["task_id"]),
                    )
        if mission_manager is not None:
            if failure is not None:
                current = mission_manager.get(
                    str(manifest.get("mission_id") or "")
                )
                if current.get("terminal") is False:
                    mission_manager.cancel(str(current["mission_id"]))
            mission_manager.shutdown(wait=True)
        if gateway is not None:
            gateway.stop()
        recorder.close()
        ros_graph = ros_harness.graph_snapshot()
        bundle.write_jsonl("goal-and-feedback.jsonl", recorder.records())
        bundle.write_jsonl("robot-events.jsonl", robot_events)
        bundle.write_jsonl(
            "authorization-events.jsonl",
            authorization_events,
        )
        bundle.write_jsonl("mission-events.jsonl", mission_events)
        bundle.write_json("robot-task-trace.json", robot_trace)
        bundle.write_json(
            "authorization-task-trace.json",
            authorization_trace,
        )
        bundle.write_json("mission-trace.json", mission_trace)
        bundle.write_json("final-report.json", final_report)
        bundle.write_json("pose-evidence.json", pose_evidence)
        bundle.write_json(
            "navigation-parameters-before.json",
            navigation_parameters_before,
        )
        bundle.write_json(
            "navigation-parameters-after.json",
            navigation_parameters_after,
        )
        bundle.write_json(
            "navigation-diagnostics.json",
            diagnostics_evidence,
        )
        bundle.write_json("stall-evidence.json", stall_evidence)
        bundle.write_json("ros-graph.json", ros_graph)
        if not plugin_inventory:
            bundle.write_json("plugin-inventory.json", {})
        manifest["completed_at"] = _utc_now()
        manifest["adapter_trap_call_count"] = len(trap_calls)
        manifest["artifacts"] = bundle.manifest_files()
        bundle.write_json("run-manifest.json", manifest)
