from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import sys
from time import monotonic
from typing import Any

import pytest

from .artifacts import (
    ArtifactBundle,
    asset_hashes,
    repository_snapshot,
)
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


def test_plugin_owned_navigation_success(
    repo_root: Path,
    acceptance_scenario: AcceptanceScenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if scenario.scenario_type != "success":
        pytest.skip("selected acceptance scenario is not the success lane")
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
        mission_trace = mission_agent.mission_trace(mission_id)
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
        assert (
            run["authorization_status"]
            == scenario.mission_authorization_status
        )
        assert authorization_trace.get("status") == (
            scenario.mission_authorization_status
        )
        assert (
            authorization_trace.get("result", {}).get("status")
            == scenario.mission_authorization_raw_status
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
        robot_trace = wait_for_robot_task(
            mission_agent.subagent_client,
            entry,
            task_id,
            timeout_seconds=scenario.navigation_seconds,
        )
        robot_events = gateway.events.events_for_task(task_id)
        completed_run = wait_for_mission_run(
            mission_manager,
            mission_id,
            timeout_seconds=scenario.mission_seconds,
        )
        mission_trace = mission_agent.mission_trace(mission_id)
        final_report = dict(completed_run.get("final_report") or {})

        final_pose = ros_harness.current_pose()
        stopped = ros_harness.wait_until_stopped()
        initial_position_error, initial_yaw_error = (
            scenario.initial_pose_error(initial_pose)
        )
        position_error, yaw_error = scenario.goal_error(final_pose)
        actual_displacement = (
            (final_pose.x - initial_pose.x) ** 2
            + (final_pose.y - initial_pose.y) ** 2
        ) ** 0.5
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "initial_position_error_m": initial_position_error,
            "initial_yaw_error_rad": initial_yaw_error,
            "goal": _pose_dict(scenario.goal),
            "final_pose": _pose_dict(final_pose),
            "position_error_m": position_error,
            "yaw_error_rad": yaw_error,
            "actual_displacement_m": actual_displacement,
            "stopped": stopped,
        }

        assert completed_run.get("status") == scenario.terminal_status
        assert final_report.get("status") == scenario.terminal_status
        assert mission_trace.get("status") == "succeeded"
        assert robot_trace.get("result") is not None
        assert robot_trace.get("status") == scenario.terminal_status
        assert robot_trace["result"].get("status") == scenario.terminal_status
        assert (
            initial_position_error
            <= scenario.initial_position_tolerance_m
        )
        assert initial_yaw_error <= scenario.initial_yaw_tolerance_rad
        assert position_error <= scenario.position_tolerance_m
        assert yaw_error <= scenario.yaw_tolerance_rad
        assert actual_displacement >= scenario.minimum_displacement_m
        assert not trap_calls

        ros_goals = recorder.goals()
        assert ros_goals, "no /move_base/goal message was captured"
        goal = ros_goals[-1]
        assert goal["frame_id"] == "map"
        assert goal["x"] == pytest.approx(scenario.goal.x, abs=1e-6)
        assert goal["y"] == pytest.approx(scenario.goal.y, abs=1e-6)
        assert any(
            status.get("goal_id") == goal["goal_id"]
            and status.get("status") in scenario.actionlib_terminal_statuses
            for status in recorder.statuses()
        ), "move_base did not publish a SUCCEEDED status for the captured goal"
        if scenario.require_feedback:
            assert recorder.feedback(), "no live /move_base feedback was captured"

        event_types = [str(event.get("type")) for event in robot_events]
        for expected_event in (
            "authorization.approved",
            "confirmation.confirmed",
            "action.requested",
            "action.started",
            "action.feedback",
            "action.succeeded",
        ):
            assert expected_event in event_types
        requested = next(
            event
            for event in robot_events
            if event.get("type") == "action.requested"
        )
        action_id = requested["payload"]["action_id"]
        action_events = [
            event
            for event in robot_events
            if event.get("payload", {}).get("action_id") == action_id
        ]
        assert sum(
            event.get("type") == "action.succeeded"
            for event in action_events
        ) == 1
        assert any(
            event.get("type") == "plugin.extensions_loaded"
            and any(
                item.get("plugin_id") == scenario.plugin_owner
                for item in event.get("payload", {}).get("loaded", [])
            )
            for event in robot_events
        )
        assert any(
            event.get("type") == "mission.report_ready"
            for event in mission_events
        )

        manifest["status"] = "passed"
        manifest["action_id"] = action_id
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
        bundle.write_json("ros-graph.json", ros_graph)
        bundle.write_json(
            "navigation-diagnostics.json",
            {
                "status": "not_applicable",
                "reason": "success baseline does not invoke recovery diagnostics",
            },
        )
        if not plugin_inventory:
            bundle.write_json("plugin-inventory.json", {})
        manifest["completed_at"] = _utc_now()
        manifest["adapter_trap_call_count"] = len(trap_calls)
        manifest["artifacts"] = bundle.manifest_files()
        bundle.write_json("run-manifest.json", manifest)


def test_plugin_owned_navigation_cancel(
    repo_root: Path,
    acceptance_scenario: AcceptanceScenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if scenario.scenario_type != "cancel":
        pytest.skip("selected acceptance scenario is not the cancel lane")
    cancellation = scenario.cancellation
    assert cancellation is not None
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
    cancellation_evidence: dict[str, Any] = {}
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

        cancel_pose = ros_harness.current_pose()
        distance_to_goal_at_request = (
            (cancel_pose.x - scenario.goal.x) ** 2
            + (cancel_pose.y - scenario.goal.y) ** 2
        ) ** 0.5
        cancel_requested_at = _utc_now()
        cancel_started = monotonic()
        cancel_response = mission_manager.cancel(
            mission_id,
            operator={
                "operator_id": "local-loopback-operator",
                "role": "operator",
                "source": "trusted_acceptance_harness",
            },
        )
        cancel_request_elapsed = monotonic() - cancel_started
        assert cancel_response.get("status") == "cancel_requested"
        assert cancel_response.get("cancellation", {}).get("status") == (
            "cancel_requested"
        )

        robot_trace = wait_for_robot_task(
            mission_agent.subagent_client,
            entry,
            task_id,
            timeout_seconds=cancellation.acknowledgement_timeout_seconds,
        )
        task_terminal_latency = monotonic() - cancel_started
        robot_events = gateway.events.events_for_task(task_id)
        actionlib_terminal = recorder.wait_for_goal_status(
            goal_id,
            scenario.actionlib_terminal_statuses,
            timeout_seconds=cancellation.acknowledgement_timeout_seconds,
        )
        stopped = ros_harness.wait_until_stopped()
        stop_latency = monotonic() - cancel_started
        completed_run = wait_for_mission_run(
            mission_manager,
            mission_id,
            timeout_seconds=scenario.mission_seconds,
        )
        mission_terminal_latency = monotonic() - cancel_started
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
        post_cancel_displacement = (
            (final_pose.x - cancel_pose.x) ** 2
            + (final_pose.y - cancel_pose.y) ** 2
        ) ** 0.5
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "initial_position_error_m": initial_position_error,
            "initial_yaw_error_rad": initial_yaw_error,
            "goal": _pose_dict(scenario.goal),
            "progress_pose": _pose_dict(progress_pose),
            "cancel_request_pose": _pose_dict(cancel_pose),
            "final_pose": _pose_dict(final_pose),
            "progress_displacement_m": progress_displacement,
            "post_cancel_displacement_m": post_cancel_displacement,
            "distance_to_goal_at_cancel_request_m": (
                distance_to_goal_at_request
            ),
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
        assert mission_trace.get("status") == scenario.terminal_status
        assert initial_position_error <= scenario.initial_position_tolerance_m
        assert initial_yaw_error <= scenario.initial_yaw_tolerance_rad
        assert progress["feedback_count"] >= (
            cancellation.minimum_feedback_count
        )
        assert progress_displacement >= cancellation.minimum_displacement_m
        assert distance_to_goal_at_request > scenario.position_tolerance_m
        assert post_cancel_displacement <= (
            cancellation.maximum_post_cancel_displacement_m
        )
        assert task_terminal_latency <= (
            cancellation.acknowledgement_timeout_seconds
        )
        assert stop_latency <= cancellation.acknowledgement_timeout_seconds
        assert not trap_calls

        event_types = [str(event.get("type")) for event in robot_events]
        for expected_event in (
            "authorization.approved",
            "confirmation.confirmed",
            "action.requested",
            "action.started",
            "action.feedback",
            "task.cancel_requested",
            "action.cancel_requested",
            "action.cancelled",
            "task.cancelled",
        ):
            assert expected_event in event_types
        assert "action.succeeded" not in event_types
        assert "task.completed" not in event_types

        requested = next(
            event
            for event in robot_events
            if event.get("type") == "action.requested"
        )
        action_id = requested["payload"]["action_id"]
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
        assert action_terminals[0]["type"] == "action.cancelled"
        action_output = action_terminals[0]["payload"]["output"]
        assert action_output["cancellation_reason"] == "operator_cancelled"
        assert action_output["cancellation_acknowledged"] is True
        assert action_output["runtime_stopped"] is True
        assert action_output["resource_release_safe"] is True
        assert action_output["goal_state"] in (
            scenario.actionlib_terminal_statuses
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
        assert task_terminals[0]["type"] == "task.cancelled"
        assert task_terminals[0]["payload"]["terminal_outcome"]["status"] == (
            "cancelled"
        )
        assert event_types.index("action.cancel_requested") < (
            event_types.index("action.cancelled")
        )
        assert event_types.index("action.cancelled") < (
            event_types.index("task.cancelled")
        )
        assert any(
            event.get("type") == "plugin.extensions_loaded"
            and any(
                item.get("plugin_id") == scenario.plugin_owner
                for item in event.get("payload", {}).get("loaded", [])
            )
            for event in robot_events
        )

        mission_event_types = [
            str(event.get("type")) for event in mission_events
        ]
        assert "mission.cancel_requested" in mission_event_types
        assert "mission.cancelled" in mission_event_types
        assert "mission.completed" not in mission_event_types

        cancellation_evidence = {
            "cancel_requested_at": cancel_requested_at,
            "cancel_response": cancel_response,
            "cancel_request_elapsed_seconds": cancel_request_elapsed,
            "task_terminal_latency_seconds": task_terminal_latency,
            "mission_terminal_latency_seconds": mission_terminal_latency,
            "stop_latency_seconds": stop_latency,
            "trigger": progress,
            "action_id": action_id,
            "goal_id": goal_id,
            "actionlib_terminal": actionlib_terminal,
            "action_terminal_output": action_output,
            "post_cancel_displacement_m": post_cancel_displacement,
        }
        manifest["status"] = "passed"
        manifest["action_id"] = action_id
        manifest["cancel_requested_at"] = cancel_requested_at
    except BaseException as exc:
        failure = exc
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        task_id_value = manifest.get("task_id")
        if gateway is not None and isinstance(task_id_value, str):
            robot_events = gateway.events.events_for_task(task_id_value)
        if (
            failure is not None
            and gateway is not None
            and mission_agent is not None
            and isinstance(task_id_value, str)
        ):
            entry = mission_agent.registry.get(scenario.robot_id)
            if entry is not None:
                current_trace = mission_agent.subagent_client.get_task_trace(
                    entry,
                    task_id_value,
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
                        task_id_value,
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
            "cancellation-evidence.json",
            cancellation_evidence,
        )
        bundle.write_json("ros-graph.json", ros_graph)
        bundle.write_json(
            "navigation-diagnostics.json",
            {
                "status": "not_applicable",
                "reason": "operator cancel lane does not invoke recovery diagnostics",
            },
        )
        if not plugin_inventory:
            bundle.write_json("plugin-inventory.json", {})
        manifest["completed_at"] = _utc_now()
        manifest["adapter_trap_call_count"] = len(trap_calls)
        manifest["artifacts"] = bundle.manifest_files()
        bundle.write_json("run-manifest.json", manifest)
