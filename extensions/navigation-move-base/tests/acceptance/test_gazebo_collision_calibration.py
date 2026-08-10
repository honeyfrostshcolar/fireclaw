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
from .ros_harness import assert_collision_detected
from .scenario import CollisionCalibrationScenario, Pose2D


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


def test_gazebo_contact_positive_control(
    repo_root: Path,
    acceptance_scenario,
    artifact_bundle: ArtifactBundle,
    ros_harness,
) -> None:
    scenario = acceptance_scenario
    if not isinstance(scenario, CollisionCalibrationScenario):
        pytest.skip("selected acceptance scenario is not collision calibration")
    calibration = scenario.calibration
    probe_path = scenario.assets.collision_probe
    assert probe_path is not None
    bundle = artifact_bundle
    manifest: dict[str, Any] = {
        "schema_version": "fireclaw.gazebo-collision-calibration-proof/v1",
        "run_id": bundle.run_id,
        "scenario": scenario.to_manifest(),
        "status": "running",
        "started_at": _utc_now(),
        "repository": repository_snapshot(repo_root),
        "execution": {
            "simulation_only": True,
            "excluded_from_task_metrics": True,
            "task_dispatch": False,
            "mission_created": False,
            "move_base_goal_sent": False,
            "calibration_method": (
                "gazebo_spawn_sdf_model_static_overlap"
            ),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "ros_distro": os.getenv("ROS_DISTRO"),
            "ros_master_uri": os.getenv("ROS_MASTER_URI"),
            "gazebo_master_uri": os.getenv("GAZEBO_MASTER_URI"),
        },
        "asset_hashes": asset_hashes({
            name: Path(path)
            for name, path in scenario.assets.to_dict().items()
        }),
    }
    bundle.write_json("run-manifest.json", manifest)

    recorder = ros_harness.recorder()
    spawn_evidence: dict[str, Any] = {}
    detection_evidence: dict[str, Any] = {}
    cleanup_evidence: dict[str, Any] = {}
    collision_evidence: dict[str, Any] = {}
    pose_evidence: dict[str, Any] = {}
    navigation_parameters: dict[str, Any] = {}
    map_evidence: dict[str, Any] = {}
    ros_graph: dict[str, Any] = {}
    spawned = False
    stopped: dict[str, Any] | None = None
    failure: BaseException | None = None

    try:
        readiness = ros_harness.wait_until_ready()
        readiness["collision_instrumentation"] = (
            recorder.wait_for_collision_instrumentation(
                timeout_seconds=scenario.ros_readiness_seconds,
            )
        )
        bundle.write_json("readiness.json", readiness)
        navigation_parameters = ros_harness.navigation_parameters()
        map_evidence = ros_harness.map_bounds()
        initial_pose = ros_harness.current_pose()
        initial_position_error, initial_yaw_error = (
            scenario.initial_pose_error(initial_pose)
        )
        assert (
            initial_position_error
            <= scenario.initial_position_tolerance_m
        )
        assert initial_yaw_error <= scenario.initial_yaw_tolerance_rad
        pre_injection_stopped = ros_harness.wait_until_stopped()

        spawn_evidence = ros_harness.spawn_collision_calibration_probe(
            calibration,
            probe_path,
        )
        spawned = True
        detection_evidence = recorder.wait_for_prohibited_collision(
            other_model_name=calibration.model_name,
            minimum_state_count=(
                calibration.minimum_prohibited_contact_states
            ),
            minimum_episode_count=calibration.minimum_collision_episodes,
            timeout_seconds=calibration.detection_timeout_seconds,
        )
        cleanup_evidence = ros_harness.delete_collision_calibration_probe(
            calibration.model_name,
            timeout_seconds=calibration.detection_timeout_seconds,
        )
        spawned = False

        stopped = ros_harness.wait_until_stopped()
        final_pose = ros_harness.current_pose()
        displacement = hypot(
            final_pose.x - initial_pose.x,
            final_pose.y - initial_pose.y,
        )
        records = recorder.records()
        goals = [item for item in records if item.get("kind") == "goal"]
        cmd_vel = [
            item for item in records if item.get("kind") == "cmd_vel"
        ]
        nonzero_cmd_vel = [
            item
            for item in cmd_vel
            if abs(float(item.get("linear_x_mps") or 0.0)) > 1e-9
            or abs(float(item.get("angular_z_rps") or 0.0)) > 1e-9
        ]
        pose_evidence = {
            "initial_pose": _pose_dict(initial_pose),
            "configured_initial_pose": _pose_dict(scenario.initial_pose),
            "initial_position_error_m": initial_position_error,
            "initial_yaw_error_rad": initial_yaw_error,
            "pre_injection_stopped": pre_injection_stopped,
            "final_pose": _pose_dict(final_pose),
            "actual_displacement_m": displacement,
            "maximum_robot_displacement_m": (
                calibration.maximum_robot_displacement_m
            ),
            "stopped": stopped,
            "move_base_goal_count": len(goals),
            "cmd_vel_record_count": len(cmd_vel),
            "nonzero_cmd_vel_count": len(nonzero_cmd_vel),
        }
        assert displacement <= calibration.maximum_robot_displacement_m
        assert not goals, "calibration must not send a /move_base goal"
        assert not nonzero_cmd_vel, (
            "calibration must not command robot motion: "
            f"{nonzero_cmd_vel[-3:]}"
        )

        collision_evidence = recorder.finalize_collision_evidence(
            terminal_stop=stopped,
        )
        assert_collision_detected(
            collision_evidence,
            expected_other_model=calibration.model_name,
            minimum_state_count=(
                calibration.minimum_prohibited_contact_states
            ),
            minimum_episode_count=calibration.minimum_collision_episodes,
        )
        manifest["status"] = "passed"
    except BaseException as exc:
        failure = exc
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if spawned:
            try:
                cleanup_evidence = (
                    ros_harness.delete_collision_calibration_probe(
                        calibration.model_name,
                        timeout_seconds=calibration.detection_timeout_seconds,
                    )
                )
            except BaseException as cleanup_error:
                cleanup_evidence = {
                    "status": "cleanup_failed",
                    "error": (
                        f"{type(cleanup_error).__name__}: {cleanup_error}"
                    ),
                }
        if stopped is None:
            try:
                stopped = ros_harness.wait_until_stopped()
            except BaseException as stop_error:
                stopped = {
                    "status": "stop_proof_failed",
                    "error": f"{type(stop_error).__name__}: {stop_error}",
                }
        if not collision_evidence:
            collision_evidence = recorder.finalize_collision_evidence(
                terminal_stop=stopped,
            )
        collision_records = recorder.collision_records()
        all_records = recorder.records()
        recorder.close()
        ros_graph = ros_harness.graph_snapshot()
        injection_evidence = {
            "schema_version": (
                "fireclaw.gazebo-collision-injection-evidence/v1"
            ),
            "status": (
                "complete"
                if spawn_evidence.get("status") == "spawned"
                and detection_evidence.get("status") == "detected"
                and cleanup_evidence.get("status") == "deleted"
                else "incomplete"
            ),
            "simulation_only": True,
            "excluded_from_task_metrics": True,
            "spawn": spawn_evidence,
            "detection": detection_evidence,
            "cleanup": cleanup_evidence,
        }
        bundle.write_json(
            "collision-injection.json",
            injection_evidence,
        )
        bundle.write_json(
            "collision-evidence.json",
            collision_evidence,
        )
        bundle.write_jsonl(
            "collision-contact-stream.jsonl",
            collision_records,
        )
        bundle.write_jsonl("goal-and-feedback.jsonl", all_records)
        bundle.write_json("pose-evidence.json", pose_evidence)
        bundle.write_json(
            "navigation-parameters.json",
            navigation_parameters,
        )
        bundle.write_json("map-evidence.json", map_evidence)
        bundle.write_json("ros-graph.json", ros_graph)
        bundle.write_json("plugin-inventory.json", {
            "status": "not_applicable",
            "reason": (
                "measurement calibration invokes no FireClaw Agent Plugin"
            ),
        })
        bundle.write_json("tool-inventory.json", {
            "status": "not_applicable",
            "reason": "measurement calibration invokes no Agent Tool",
        })
        bundle.copy_file(
            "collision-monitor-plugin.so",
            recorder.collision_monitor_library_path(),
        )
        manifest["completed_at"] = _utc_now()
        manifest["artifacts"] = bundle.manifest_files()
        manifest["failure_recorded"] = failure is not None
        bundle.write_json("run-manifest.json", manifest)
