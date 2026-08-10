from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import sys
from time import monotonic
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
    wait_for_robot_event,
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


def test_plugin_owned_navigation_timeout(
    repo_root: Path,
    acceptance_scenario: AcceptanceScenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if scenario.scenario_type != "timeout":
        pytest.skip("selected acceptance scenario is not the timeout lane")
    timeout = scenario.timeout
    assert timeout is not None
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
    robot_trace: dict[str, Any] = {}
    authorization_events: list[dict[str, Any]] = []
    authorization_trace: dict[str, Any] = {}
    mission_trace: dict[str, Any] = {}
    final_report: dict[str, Any] = {}
    plugin_inventory: dict[str, Any] = {}
    pose_evidence: dict[str, Any] = {}
    timeout_evidence: dict[str, Any] = {}
    ros_graph: dict[str, Any] = {}
    trap_calls: list[dict[str, Any]] = []
    failure: BaseException | None = None

    try:
        readiness = ros_harness.wait_until_ready()
        bundle.write_json("readiness.json", readiness)
        initial_pose = ros_harness.current_pose()

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
            timeout.execution_timeout_seconds
        )
        assert inspection.cancellation_ack_timeout_seconds == pytest.approx(
            timeout.acknowledgement_timeout_seconds
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
        assert run["authorization_status"] == (
            scenario.mission_authorization_status
        )
        assert authorization_trace.get("status") == (
            scenario.mission_authorization_status
        )
        assert authorization_trace.get("result", {}).get("status") == (
            scenario.mission_authorization_raw_status
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
        task_id = str(confirmed["task_id"])
        manifest["task_id"] = task_id
        assert task_id == authorization_task_id

        progress_pose, progress = ros_harness.wait_for_navigation_progress(
            recorder,
            initial_pose=initial_pose,
        )
        in_flight_events = gateway.events.events_for_task(task_id)
        in_flight_types = [
            str(event.get("type")) for event in in_flight_events
        ]
        assert "action.started" in in_flight_types
        assert "action.feedback" in in_flight_types
        assert "action.succeeded" not in in_flight_types

        ros_goals = recorder.goals()
        assert ros_goals, "no /move_base/goal message was captured"
        goal = ros_goals[-1]
        goal_id = str(goal["goal_id"])
        assert goal["frame_id"] == "map"
        assert goal["x"] == pytest.approx(scenario.goal.x, abs=1e-6)
        assert goal["y"] == pytest.approx(scenario.goal.y, abs=1e-6)
        assert progress["goal_id"] == goal_id

        timeout_requested_event = wait_for_robot_event(
            gateway,
            task_id,
            "action.cancel_requested",
            timeout_seconds=(
                timeout.execution_timeout_seconds
                + timeout.acknowledgement_timeout_seconds
            ),
        )
        timeout_observed_at = _utc_now()
        timeout_observed = monotonic()
        timeout_request_pose = ros_harness.current_pose()
        distance_to_goal_at_timeout = (
            (timeout_request_pose.x - scenario.goal.x) ** 2
            + (timeout_request_pose.y - scenario.goal.y) ** 2
        ) ** 0.5
        timeout_request_payload = timeout_requested_event.get("payload", {})
        assert timeout_request_payload.get("cancellation_reason") == (
            "deadline_exceeded"
        )
        assert timeout_request_payload.get("timeout_seconds") == pytest.approx(
            timeout.execution_timeout_seconds
        )

        robot_trace = wait_for_robot_task(
            mission_agent.subagent_client,
            entry,
            task_id,
            timeout_seconds=timeout.acknowledgement_timeout_seconds,
        )
        task_terminal_latency = monotonic() - timeout_observed
        robot_events = gateway.events.events_for_task(task_id)
        actionlib_terminal = recorder.wait_for_goal_status(
            goal_id,
            scenario.actionlib_terminal_statuses,
            timeout_seconds=timeout.acknowledgement_timeout_seconds,
        )
        stopped = ros_harness.wait_until_stopped()
        stop_latency = monotonic() - timeout_observed
        completed_run = wait_for_mission_run(
            mission_manager,
            mission_id,
            timeout_seconds=scenario.mission_seconds,
        )
        mission_terminal_latency = monotonic() - timeout_observed
        mission_trace = mission_agent.mission_trace(mission_id)
        final_report = dict(completed_run.get("final_report") or {})

        final_pose = ros_harness.current_pose()
        initial_position_error, initial_yaw_error = (
            scenario.initial_pose_error(initial_pose)
        )
        progress_displacement = (
            (progress_pose.x - initial_pose.x) ** 2
            + (progress_pose.y - initial_pose.y) ** 2
        ) ** 0.5
        post_timeout_displacement = (
            (final_pose.x - timeout_request_pose.x) ** 2
            + (final_pose.y - timeout_request_pose.y) ** 2
        ) ** 0.5
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "initial_position_error_m": initial_position_error,
            "initial_yaw_error_rad": initial_yaw_error,
            "goal": _pose_dict(scenario.goal),
            "progress_pose": _pose_dict(progress_pose),
            "timeout_request_pose": _pose_dict(timeout_request_pose),
            "final_pose": _pose_dict(final_pose),
            "progress_displacement_m": progress_displacement,
            "post_timeout_displacement_m": post_timeout_displacement,
            "distance_to_goal_at_timeout_m": distance_to_goal_at_timeout,
            "stopped": stopped,
        }

        assert robot_trace.get("status") == scenario.terminal_status
        assert robot_trace.get("result", {}).get("status") == (
            scenario.terminal_status
        )
        assert completed_run.get("status") == scenario.terminal_status
        assert completed_run.get("terminal") is True
        assert completed_run.get("result", {}).get("status") == (
            scenario.terminal_status
        )
        assert final_report.get("status") == scenario.terminal_status
        # Mission Registry retains its aggregate failure class while preserving
        # the canonical timed_out child and Mission Run/final-report outcome.
        assert mission_trace.get("status") == "failed"
        mission_subtasks = mission_trace.get("subtasks", [])
        assert len(mission_subtasks) == 1
        assert mission_subtasks[0].get("task_id") == task_id
        assert mission_subtasks[0].get("status") == scenario.terminal_status
        assert initial_position_error <= scenario.initial_position_tolerance_m
        assert initial_yaw_error <= scenario.initial_yaw_tolerance_rad
        assert progress["feedback_count"] >= timeout.minimum_feedback_count
        assert progress_displacement >= timeout.minimum_displacement_m
        assert distance_to_goal_at_timeout > scenario.position_tolerance_m
        assert post_timeout_displacement <= (
            timeout.maximum_post_timeout_displacement_m
        )
        assert task_terminal_latency <= timeout.acknowledgement_timeout_seconds
        assert stop_latency <= timeout.acknowledgement_timeout_seconds
        assert not trap_calls

        event_types = [str(event.get("type")) for event in robot_events]
        for expected_event in (
            "authorization.approved",
            "confirmation.confirmed",
            "action.requested",
            "action.started",
            "action.feedback",
            "action.cancel_requested",
            "action.timed_out",
            "task.timed_out",
        ):
            assert expected_event in event_types
        for forbidden_event in (
            "task.cancel_requested",
            "action.cancelled",
            "action.succeeded",
            "task.cancelled",
            "task.completed",
        ):
            assert forbidden_event not in event_types

        requested = next(
            event
            for event in robot_events
            if event.get("type") == "action.requested"
        )
        action_id = requested["payload"]["action_id"]
        assert requested["payload"]["timeout_seconds"] == pytest.approx(
            timeout.execution_timeout_seconds
        )
        action_events = [
            event
            for event in robot_events
            if event.get("payload", {}).get("action_id") == action_id
        ]
        action_terminal_types = {
            "action.succeeded",
            "action.failed",
            "action.cancelled",
            "action.timed_out",
            "action.lost",
            "action.escalated",
        }
        action_terminals = [
            event
            for event in action_events
            if event.get("type") in action_terminal_types
        ]
        assert len(action_terminals) == 1
        assert action_terminals[0]["type"] == "action.timed_out"
        action_output = action_terminals[0]["payload"]["output"]
        assert action_output["cancellation_reason"] == "deadline_exceeded"
        assert action_output["cancellation_acknowledged"] is True
        assert action_output["runtime_stopped"] is True
        assert action_output["resource_release_safe"] is True
        assert action_output["goal_state"] in (
            scenario.actionlib_terminal_statuses
        )
        assert action_output["elapsed_seconds"] >= (
            timeout.execution_timeout_seconds
        )
        assert action_output["elapsed_seconds"] <= (
            timeout.execution_timeout_seconds
            + timeout.acknowledgement_timeout_seconds
        )

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
        assert task_terminals[0]["type"] == "task.timed_out"
        assert task_terminals[0]["payload"]["terminal_outcome"]["status"] == (
            "timed_out"
        )
        assert event_types.index("action.cancel_requested") < (
            event_types.index("action.timed_out")
        )
        assert event_types.index("action.timed_out") < (
            event_types.index("task.timed_out")
        )
        assert any(
            event.get("type") == "plugin.extensions_loaded"
            and any(
                item.get("plugin_id") == scenario.plugin_owner
                for item in event.get("payload", {}).get("loaded", [])
            )
            for event in robot_events
        )

        failure_decisions = completed_run.get("result", {}).get(
            "failure_decisions",
            [],
        )
        assert failure_decisions == [
            {
                "robot_id": scenario.robot_id,
                "task_id": task_id,
                "status": "timed_out",
                "decision": "abort",
            }
        ]
        mission_event_types = [
            str(event.get("type")) for event in mission_events
        ]
        assert "mission.report_ready" in mission_event_types
        assert "mission.timed_out" in mission_event_types
        for forbidden_event in (
            "mission.cancel_requested",
            "mission.cancelled",
            "mission.completed",
        ):
            assert forbidden_event not in mission_event_types

        timeout_evidence = {
            "timeout_observed_at": timeout_observed_at,
            "timeout_requested_event": timeout_requested_event,
            "configured_execution_timeout_seconds": (
                timeout.execution_timeout_seconds
            ),
            "task_terminal_latency_seconds": task_terminal_latency,
            "mission_terminal_latency_seconds": mission_terminal_latency,
            "stop_latency_seconds": stop_latency,
            "post_timeout_displacement_m": post_timeout_displacement,
            "trigger": progress,
            "goal_id": goal_id,
            "actionlib_terminal": actionlib_terminal,
            "action_id": action_id,
            "action_terminal_output": action_output,
        }
        bundle.write_json("timeout-evidence.json", timeout_evidence)
        manifest["status"] = "passed"
        manifest["action_id"] = action_id
        manifest["timeout_observed_at"] = timeout_observed_at
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
        bundle.write_json("timeout-evidence.json", timeout_evidence)
        bundle.write_json("ros-graph.json", ros_graph)
        bundle.write_json(
            "navigation-diagnostics.json",
            {
                "status": "not_applicable",
                "reason": (
                    "deadline timeout lane does not invoke recovery "
                    "diagnostics"
                ),
            },
        )
        if not plugin_inventory:
            bundle.write_json("plugin-inventory.json", {})
        manifest["completed_at"] = _utc_now()
        manifest["adapter_trap_call_count"] = len(trap_calls)
        manifest["artifacts"] = bundle.manifest_files()
        bundle.write_json("run-manifest.json", manifest)
