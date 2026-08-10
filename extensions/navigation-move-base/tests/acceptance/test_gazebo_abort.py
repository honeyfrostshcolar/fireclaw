from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import sys
from time import monotonic, sleep
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
from .ros_harness import assert_collision_free
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


def _wait_for_goal(recorder, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        goals = recorder.goals()
        if goals:
            return goals[-1]
        sleep(0.05)
    raise AssertionError("no /move_base/goal message was captured")


def test_plugin_owned_navigation_abort(
    repo_root: Path,
    acceptance_scenario: AcceptanceScenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if scenario.scenario_type != "abort":
        pytest.skip("selected acceptance scenario is not the abort lane")
    abort = scenario.abort
    assert abort is not None
    bundle = artifact_bundle
    manifest: dict[str, Any] = {
        "schema_version": "fireclaw.gazebo-acceptance-proof/v1",
        "run_id": bundle.run_id,
        "scenario": scenario.to_manifest(),
        "status": "running",
        "started_at": _utc_now(),
        "repository": repository_snapshot(repo_root),
        "execution": {
            "mission_run_manager": "MissionRunManager",
            "use_scheduler": True,
            "background": True,
            "authorization_resume": "same_task_id",
        },
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
    mission_run: dict[str, Any] = {}
    final_report: dict[str, Any] = {}
    plugin_inventory: dict[str, Any] = {}
    pose_evidence: dict[str, Any] = {}
    map_evidence: dict[str, Any] = {}
    navigation_parameters: dict[str, Any] = {}
    abort_evidence: dict[str, Any] = {}
    ros_graph: dict[str, Any] = {}
    collision_evidence: dict[str, Any] = {}
    trap_calls: list[dict[str, Any]] = []
    failure: BaseException | None = None

    try:
        readiness = ros_harness.wait_until_ready()
        readiness["collision_instrumentation"] = (
            recorder.wait_for_collision_instrumentation(
                timeout_seconds=scenario.ros_readiness_seconds,
            )
        )
        bundle.write_json("readiness.json", readiness)
        initial_pose = ros_harness.current_pose()
        map_evidence = ros_harness.map_bounds()
        navigation_parameters = ros_harness.navigation_parameters()

        bounds = map_evidence["axis_aligned_bounds"]
        goal_inside_map_bounds = (
            bounds["min_x"] <= scenario.goal.x <= bounds["max_x"]
            and bounds["min_y"] <= scenario.goal.y <= bounds["max_y"]
        )
        map_evidence["goal"] = _pose_dict(scenario.goal)
        map_evidence["goal_inside_axis_aligned_bounds"] = (
            goal_inside_map_bounds
        )
        assert map_evidence["frame_id"] == "map"
        assert goal_inside_map_bounds is False
        assert navigation_parameters["planner_patience_seconds"] == (
            pytest.approx(abort.planner_patience_seconds)
        )
        assert navigation_parameters["recovery_behavior_enabled"] is (
            abort.recovery_behavior_enabled
        )

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
        assert inspection.timeout_seconds is not None
        assert inspection.timeout_seconds > abort.terminal_timeout_seconds

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
        abort_started_at = _utc_now()
        abort_started = monotonic()
        task_id = str(confirmed["task_id"])
        manifest["task_id"] = task_id
        assert task_id == authorization_task_id

        goal = _wait_for_goal(
            recorder,
            timeout_seconds=abort.terminal_timeout_seconds,
        )
        goal_id = str(goal["goal_id"])
        assert goal["frame_id"] == "map"
        assert goal["x"] == pytest.approx(scenario.goal.x, abs=1e-6)
        assert goal["y"] == pytest.approx(scenario.goal.y, abs=1e-6)

        robot_trace = wait_for_robot_task(
            mission_agent.subagent_client,
            entry,
            task_id,
            timeout_seconds=abort.terminal_timeout_seconds,
        )
        task_terminal_latency = monotonic() - abort_started
        robot_events = gateway.events.events_for_task(task_id)
        actionlib_terminal = recorder.wait_for_goal_status(
            goal_id,
            scenario.actionlib_terminal_statuses,
            timeout_seconds=abort.terminal_timeout_seconds,
        )
        stopped = ros_harness.wait_until_stopped()
        completed_run = wait_for_mission_run(
            mission_manager,
            mission_id,
            timeout_seconds=scenario.mission_seconds,
        )
        mission_run = dict(completed_run)
        mission_terminal_latency = monotonic() - abort_started
        mission_trace = mission_agent.mission_trace(mission_id)
        final_report = dict(completed_run.get("final_report") or {})

        final_pose = ros_harness.current_pose()
        initial_position_error, initial_yaw_error = (
            scenario.initial_pose_error(initial_pose)
        )
        displacement = (
            (final_pose.x - initial_pose.x) ** 2
            + (final_pose.y - initial_pose.y) ** 2
        ) ** 0.5
        feedback = [
            item
            for item in recorder.feedback()
            if str(item.get("goal_id")) == goal_id
        ]
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "initial_position_error_m": initial_position_error,
            "initial_yaw_error_rad": initial_yaw_error,
            "goal": _pose_dict(scenario.goal),
            "final_pose": _pose_dict(final_pose),
            "displacement_m": displacement,
            "stopped": stopped,
        }

        assert robot_trace.get("status") == scenario.terminal_status
        robot_result = robot_trace.get("result", {})
        assert robot_result.get("status") == scenario.terminal_status
        assert robot_result.get("terminal_outcome", {}).get("status") == (
            "failed"
        )
        assert robot_result.get("terminal_outcome", {}).get("raw_status") == (
            "failed"
        )
        assert completed_run.get("status") == scenario.terminal_status
        assert completed_run.get("terminal") is True
        assert completed_run.get("result", {}).get("status") == (
            scenario.terminal_status
        )
        assert final_report.get("status") == scenario.terminal_status
        assert mission_trace.get("status") == scenario.terminal_status
        mission_subtasks = mission_trace.get("subtasks", [])
        assert len(mission_subtasks) == 1
        assert mission_subtasks[0].get("task_id") == task_id
        assert mission_subtasks[0].get("status") == scenario.terminal_status
        assert initial_position_error <= scenario.initial_position_tolerance_m
        assert initial_yaw_error <= scenario.initial_yaw_tolerance_rad
        assert len(feedback) >= abort.minimum_feedback_count
        assert displacement <= abort.maximum_displacement_m
        assert task_terminal_latency <= abort.terminal_timeout_seconds
        assert not trap_calls

        event_types = [str(event.get("type")) for event in robot_events]
        for expected_event in (
            "authorization.approved",
            "confirmation.confirmed",
            "action.requested",
            "action.started",
            "action.feedback",
            "action.failed",
            "task.failed",
        ):
            assert expected_event in event_types
        for forbidden_event in (
            "action.cancel_requested",
            "action.cancelled",
            "action.timed_out",
            "action.succeeded",
            "action.lost",
            "task.cancel_requested",
            "task.cancelled",
            "task.timed_out",
            "task.completed",
            "task.lost",
        ):
            assert forbidden_event not in event_types

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
        action_terminal = action_terminals[0]
        assert action_terminal["type"] == "action.failed"
        assert action_terminal["payload"]["status"] == "aborted"
        action_output = action_terminal["payload"]["output"]
        assert action_output["error_code"] == "move_base_aborted"
        assert action_output["goal_state"] == 4
        assert action_output["goal_state_name"] == "aborted"
        assert action_output["runtime_stopped"] is True
        assert action_output["resource_release_safe"] is True
        assert abort.required_status_text_substring.lower() in (
            action_output["goal_status_text"].lower()
        )
        assert abort.required_status_text_substring.lower() in (
            str(actionlib_terminal.get("text") or "").lower()
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
        task_terminal = task_terminals[0]
        assert task_terminal["type"] == "task.failed"
        assert task_terminal["payload"]["status"] == "failed"
        # The Robot Agent projects a failed skill to its canonical task status.
        # The native move_base cause remains authoritative in action.failed.
        assert task_terminal["payload"]["raw_status"] == "failed"
        assert task_terminal["payload"]["terminal_outcome"]["status"] == (
            "failed"
        )
        assert task_terminal["payload"]["terminal_outcome"][
            "raw_status"
        ] == "failed"
        assert event_types.index("action.failed") < event_types.index(
            "task.failed"
        )
        assert any(
            event.get("type") == "plugin.extensions_loaded"
            and any(
                item.get("plugin_id") == scenario.plugin_owner
                for item in event.get("payload", {}).get("loaded", [])
            )
            for event in robot_events
        )

        mission_result = completed_run.get("result", {})
        assert mission_result.get("failure_decisions") == [
            {
                "robot_id": scenario.robot_id,
                "task_id": task_id,
                "status": "failed",
                "decision": "abort",
                "requested_decision": "reassign",
                "reason": "no_alternative_robot",
            }
        ]
        group_results = mission_result.get("group_results", [])
        assert len(group_results) == 1
        assert len(group_results[0].get("subtask_results", [])) == 1
        assert len(group_results[0].get("terminal_states", [])) == 1

        mission_event_types = [
            str(event.get("type")) for event in mission_events
        ]
        assert "mission.report_ready" in mission_event_types
        assert "mission.failed" in mission_event_types
        for forbidden_event in (
            "mission.cancel_requested",
            "mission.cancelled",
            "mission.completed",
            "mission.timed_out",
        ):
            assert forbidden_event not in mission_event_types

        abort_evidence = {
            "abort_started_at": abort_started_at,
            "task_terminal_latency_seconds": task_terminal_latency,
            "mission_terminal_latency_seconds": mission_terminal_latency,
            "goal_id": goal_id,
            "feedback_count": len(feedback),
            "actionlib_terminal": actionlib_terminal,
            "action_id": action_id,
            "action_terminal_output": action_output,
            "map": map_evidence,
            "navigation_parameters": navigation_parameters,
            "robot_displacement_m": displacement,
        }
        bundle.write_json("abort-evidence.json", abort_evidence)
        collision_evidence = recorder.finalize_collision_evidence(
            terminal_stop=pose_evidence.get("stopped"),
        )
        assert_collision_free(collision_evidence)
        manifest["status"] = "passed"
        manifest["action_id"] = action_id
        manifest["abort_started_at"] = abort_started_at
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
        if not collision_evidence:
            collision_evidence = recorder.finalize_collision_evidence(
                terminal_stop=pose_evidence.get("stopped"),
            )
        collision_records = recorder.collision_records()
        recorder.close()
        ros_graph = ros_harness.graph_snapshot()
        bundle.write_jsonl("goal-and-feedback.jsonl", recorder.records())
        bundle.write_jsonl(
            "collision-contact-stream.jsonl",
            collision_records,
        )
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
        bundle.write_json("mission-run.json", mission_run)
        bundle.write_json("final-report.json", final_report)
        bundle.write_json("pose-evidence.json", pose_evidence)
        bundle.write_json("map-evidence.json", map_evidence)
        bundle.write_json(
            "navigation-parameters.json",
            navigation_parameters,
        )
        bundle.write_json("abort-evidence.json", abort_evidence)
        bundle.write_json("ros-graph.json", ros_graph)
        bundle.write_json("collision-evidence.json", collision_evidence)
        bundle.copy_file(
            "collision-monitor-plugin.so",
            recorder.collision_monitor_library_path(),
        )
        bundle.write_json(
            "navigation-diagnostics.json",
            {
                "status": "not_applicable",
                "reason": (
                    "unreachable-goal abort lane preserves the native "
                    "move_base failure; diagnostics and recovery belong to "
                    "the separate stall/blocked lane"
                ),
            },
        )
        if not plugin_inventory:
            bundle.write_json("plugin-inventory.json", {})
        manifest["completed_at"] = _utc_now()
        manifest["adapter_trap_call_count"] = len(trap_calls)
        manifest["artifacts"] = bundle.manifest_files()
        bundle.write_json("run-manifest.json", manifest)
