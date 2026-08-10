from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pytest

from fireclaw_core.devtools.gazebo_collision_calibration_eval import (
    run_gazebo_collision_calibration_eval,
)
from fireclaw_core.evaluation.calibration import (
    collect_gazebo_collision_calibration_case,
)
from fireclaw_core.evaluation.system import collect_ros_gazebo_case


def test_positive_control_is_detected_but_excluded_from_task_metrics(
    tmp_path: Path,
) -> None:
    proof = _write_calibration_proof(tmp_path)

    case = collect_gazebo_collision_calibration_case(
        proof,
        split="test",
    )

    assert case.record["calibration_passed"] is True
    assert case.record["collision_detection_success"] is True
    assert case.record["collision_metric_available"] is True
    assert case.record["observed_collision_free"] is False
    assert case.record["collision_count"] == 1
    assert case.record["probe_contact_state_count"] == 3
    assert case.record["probe_collision_episode_count"] == 1
    assert case.record["no_task_dispatch"] is True
    assert case.record["task_metrics_applicable"] is False
    assert case.record["task_success"] is None
    assert case.record["paper_evidence_complete"] is True
    assert case.score["collision_validation_checks"][
        "stream_classifications"
    ] is True


def test_calibration_scorer_recomputes_raw_contact_classification(
    tmp_path: Path,
) -> None:
    proof = _write_calibration_proof(tmp_path)
    stream_path = proof / "collision-contact-stream.jsonl"
    rows = [
        json.loads(line)
        for line in stream_path.read_text(encoding="utf-8").splitlines()
    ]
    rows[1]["classification"] = "allowed_support_contact"
    rows[1]["classification_reason"] = "support_link_on_ground_plane"
    rows[1]["episode_id"] = None
    _write_jsonl(stream_path, rows)

    case = collect_gazebo_collision_calibration_case(proof, split="test")

    assert case.record["calibration_passed"] is False
    assert case.record["collision_detection_success"] is False
    assert case.record["collision_metric_available"] is False
    assert case.score["collision_validation_checks"][
        "stream_classifications"
    ] is False


def test_navigation_system_collector_rejects_calibration_proof(
    tmp_path: Path,
) -> None:
    proof = _write_calibration_proof(tmp_path)

    with pytest.raises(
        ValueError,
        match="unsupported Gazebo acceptance proof schema",
    ):
        collect_ros_gazebo_case(proof, split="test")


def test_calibration_devtool_writes_separate_immutable_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    proof = _write_calibration_proof(tmp_path)
    output = tmp_path / "calibration-evaluation"
    monkeypatch.setattr(
        "fireclaw_core.devtools.gazebo_collision_calibration_eval."
        "repository_snapshot",
        lambda _root: {
            "commit": "c" * 40,
            "branch": "test",
            "dirty": False,
        },
    )

    result = run_gazebo_collision_calibration_eval(
        source_proof_dirs=[proof],
        output_dir=output,
        split="test",
        run_id="collision-calibration-test",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["status"] == "pass"
    assert result["calibration_pass_rate"] == 1.0
    assert result["collision_detection_success_rate"] == 1.0
    assert result["no_task_dispatch_rate"] == 1.0
    assert result["excluded_from_task_metrics"] is True
    assert (output / "artifact-manifest.json").is_file()
    assert list(
        output.glob("cases/*/source-proof/collision-contact-stream.jsonl")
    )
    record = json.loads(
        (output / "calibrations.jsonl").read_text(encoding="utf-8")
    )
    assert record["task_metrics_applicable"] is False
    assert record["task_success"] is None


def _write_calibration_proof(root: Path) -> Path:
    proof = root / "calibration-proof"
    proof.mkdir()
    asset_root = root / "assets"
    asset_root.mkdir()
    asset_paths: dict[str, Path] = {}
    for label, suffix in (
        ("launch", ".launch"),
        ("world", ".world"),
        ("map", ".yaml"),
        ("map_image", ".pgm"),
        ("ros1_config", ".yaml"),
        ("collision_monitor", ".cpp"),
        ("robot_description", ".xacro"),
        ("collision_probe", ".sdf"),
    ):
        path = asset_root / f"{label}{suffix}"
        path.write_text(f"fixture:{label}\n", encoding="utf-8")
        asset_paths[label] = path

    monitor_binary = proof / "collision-monitor-plugin.so"
    monitor_binary.write_bytes(b"trusted-contact-monitor-fixture")
    monitor_hash = _sha256(monitor_binary)
    probe_hash = _sha256(asset_paths["collision_probe"])
    robot_collision = (
        "turtlebot3_burger::base_footprint::"
        "base_footprint_fixed_joint_lump__base_link_collision"
    )
    probe_collision = (
        "fireclaw_collision_calibration_probe::link::collision"
    )
    wheel_collision = (
        "turtlebot3_burger::wheel_left_link::wheel_left_link_collision"
    )
    ground_collision = "ground_plane::link::collision"
    rows = [{
        "captured_at": "2026-08-10T00:00:01+00:00",
        "sim_time_seconds": 1.0,
        "sensor_topic": "/fireclaw/acceptance/contacts",
        "sensor_link": "wheel_left_link",
        "collision1_name": wheel_collision,
        "collision2_name": ground_collision,
        "classification": "allowed_support_contact",
        "classification_reason": "support_link_on_ground_plane",
        "episode_id": None,
        "contact_point_count": 1,
        "max_depth_m": 0.001,
        "contact_positions": [],
        "contact_normals": [],
        "depths_m": [0.001],
    }]
    for index in range(3):
        rows.append({
            "captured_at": f"2026-08-10T00:00:0{2 + index}+00:00",
            "sim_time_seconds": 2.0 + index * 0.01,
            "sensor_topic": "/fireclaw/acceptance/contacts",
            "sensor_link": "base_link",
            "collision1_name": robot_collision,
            "collision2_name": probe_collision,
            "classification": "prohibited_collision",
            "classification_reason": (
                "robot_contact_with_non_support_surface"
            ),
            "episode_id": "collision-0001",
            "contact_point_count": 1,
            "max_depth_m": 0.008,
            "contact_positions": [],
            "contact_normals": [],
            "depths_m": [0.008],
        })
    _write_jsonl(proof / "collision-contact-stream.jsonl", rows)
    _write_jsonl(proof / "goal-and-feedback.jsonl", [{
        "captured_at": "2026-08-10T00:00:00+00:00",
        "kind": "cmd_vel",
        "linear_x_mps": 0.0,
        "angular_z_rps": 0.0,
    }])

    _write_json(proof / "readiness.json", {
        "status": "ready",
        "action_server": "/move_base",
        "collision_instrumentation": {
            "status": "ready",
            "ready_before_first_goal": True,
        },
    })
    _write_json(proof / "collision-injection.json", {
        "schema_version": (
            "fireclaw.gazebo-collision-injection-evidence/v1"
        ),
        "status": "complete",
        "simulation_only": True,
        "excluded_from_task_metrics": True,
        "spawn": {
            "schema_version": (
                "fireclaw.gazebo-collision-injection-evidence/v1"
            ),
            "status": "spawned",
            "method": "gazebo_spawn_sdf_model_static_overlap",
            "simulation_only": True,
            "model_name": "fireclaw_collision_calibration_probe",
            "reference_frame": "world",
            "spawn_service": "/gazebo/spawn_sdf_model",
            "state_service": "/gazebo/get_model_state",
            "requested_at": "2026-08-10T00:00:01+00:00",
            "completed_at": "2026-08-10T00:00:01.100000+00:00",
            "requested_pose": {
                "x": -1.95,
                "y": -0.5,
                "z": 0.08,
                "yaw": 0.0,
            },
            "sdf_asset": {
                "path": str(asset_paths["collision_probe"]),
                "sha256": probe_hash,
            },
            "response": {"success": True, "status_message": "ok"},
            "model_state": {
                "success": True,
                "status_message": "ok",
                "pose": {
                    "x": -1.95,
                    "y": -0.5,
                    "z": 0.08,
                    "yaw": 0.0,
                },
                "twist": {
                    "linear_x_mps": 0.0,
                    "linear_y_mps": 0.0,
                    "linear_z_mps": 0.0,
                    "angular_x_rps": 0.0,
                    "angular_y_rps": 0.0,
                    "angular_z_rps": 0.0,
                },
            },
        },
        "detection": {
            "status": "detected",
            "detected_at": "2026-08-10T00:00:02+00:00",
            "expected_other_model": (
                "fireclaw_collision_calibration_probe"
            ),
            "prohibited_contact_state_count": 3,
            "collision_episode_count": 1,
            "collision_episode_ids": ["collision-0001"],
            "matching_stream_record_indices": [1, 2, 3],
            "collision_pairs": [[robot_collision, probe_collision]],
            "first_observed_at": "2026-08-10T00:00:02+00:00",
            "last_observed_at": "2026-08-10T00:00:04+00:00",
        },
        "cleanup": {
            "status": "deleted",
            "simulation_only": True,
            "model_name": "fireclaw_collision_calibration_probe",
            "reference_frame": "world",
            "delete_service": "/gazebo/delete_model",
            "state_service": "/gazebo/get_model_state",
            "requested_at": "2026-08-10T00:00:05+00:00",
            "completed_at": "2026-08-10T00:00:05.100000+00:00",
            "response": {"success": True, "status_message": "ok"},
            "post_delete_model_present": False,
            "post_delete_status_message": "not found",
        },
    })
    _write_json(proof / "collision-evidence.json", {
        "schema_version": "fireclaw.gazebo-collision-evidence/v1",
        "status": "captured",
        "robot": {
            "id": "gazebo_turtlebot3",
            "model": "burger",
            "gazebo_model_name": "turtlebot3_burger",
        },
        "instrumentation": {
            "status": "complete",
            "source": "gazebo.physics.ContactManager",
            "sensor_plugin": "libfireclaw_gazebo_contact_monitor.so",
            "message_type": "gazebo_msgs/ContactsState",
            "library": {
                "sha256": monitor_hash,
                "embedded_artifact": "collision-monitor-plugin.so",
            },
            "ready_at": "2026-08-10T00:00:00+00:00",
            "topics": [{
                "topic": "/fireclaw/acceptance/contacts",
                "message_count": 4,
                "contact_state_count": 4,
                "produced_messages": True,
                "publisher_connections_at_finalize": 1,
            }],
        },
        "observation_window": {
            "started_at": "2026-08-10T00:00:00+00:00",
            "ended_at": "2026-08-10T00:00:06+00:00",
            "wall_duration_seconds": 6.0,
            "started_before_first_goal": True,
            "ended_after_terminal_stop": True,
        },
        "filter_policy": {
            "policy_id": "fireclaw.acceptance.prohibited-contact/v1",
            "allowed_support_links": [
                "caster_back_link",
                "wheel_left_link",
                "wheel_right_link",
            ],
            "allowed_other_model": "ground_plane",
            "episode_gap_seconds": 0.25,
        },
        "raw_stream": {
            "artifact": "collision-contact-stream.jsonl",
            "record_count": 4,
            "max_records": 100000,
            "truncated": False,
        },
        "contact_message_count": 4,
        "contact_state_count": 4,
        "allowed_support_contact_state_count": 1,
        "prohibited_contact_state_count": 3,
        "contact_pair_aggregates": [],
        "collision_episodes": [{
            "episode_id": "collision-0001",
            "collision_pair": sorted([robot_collision, probe_collision]),
            "first_observed_at": "2026-08-10T00:00:02+00:00",
            "last_observed_at": "2026-08-10T00:00:04+00:00",
            "sample_count": 3,
            "sensor_topics": ["/fireclaw/acceptance/contacts"],
        }],
        "collision_count": 1,
        "collision_free": False,
    })
    _write_json(proof / "pose-evidence.json", {
        "initial_pose": {"frame_id": "map", "x": -2.0, "y": -0.5, "yaw": 0.0},
        "configured_initial_pose": {
            "frame_id": "map",
            "x": -2.0,
            "y": -0.5,
            "yaw": 0.0,
        },
        "initial_position_error_m": 0.0,
        "initial_yaw_error_rad": 0.0,
        "pre_injection_stopped": {"status": "stopped"},
        "final_pose": {"frame_id": "map", "x": -2.001, "y": -0.5, "yaw": 0.0},
        "actual_displacement_m": 0.001,
        "maximum_robot_displacement_m": 0.15,
        "stopped": {"status": "stopped"},
        "move_base_goal_count": 0,
        "cmd_vel_record_count": 1,
        "nonzero_cmd_vel_count": 0,
    })
    _write_json(proof / "ros-graph.json", {"nodes": {"status": "succeeded"}})
    _write_json(proof / "navigation-parameters.json", {"dwa": {"max_vel_x": 0.22}})
    _write_json(proof / "map-evidence.json", {"frame_id": "map"})
    _write_json(proof / "plugin-inventory.json", {"status": "not_applicable"})
    _write_json(proof / "tool-inventory.json", {"status": "not_applicable"})
    _write_json(proof / "system-versions.json", {
        "commands": {
            name: {"available": True, "stdout": "1.0", "returncode": 0}
            for name in (
                "ros_distro",
                "ros_core",
                "move_base",
                "gazebo_ros",
                "gazebo",
            )
        },
    })

    scenario = {
        "schema_version": "fireclaw.gazebo-collision-calibration/v1",
        "scenario_id": "turtlebot3-contact-positive-control",
        "scenario_type": "collision_calibration",
        "simulation_only": True,
        "excluded_from_task_metrics": True,
        "description": "fixture",
        "seed": 0,
        "robot": {
            "id": "gazebo_turtlebot3",
            "model": "burger",
            "initial_pose": {
                "frame_id": "map",
                "x": -2.0,
                "y": -0.5,
                "yaw": 0.0,
            },
        },
        "readiness": {
            "action_server": "/move_base",
            "topics": ["/clock", "/scan", "/odom"],
            "transforms": [["map", "base_link"]],
        },
        "assertions": {
            "initial_position_tolerance_m": 0.2,
            "initial_yaw_tolerance_rad": 0.35,
            "stopped_linear_velocity_mps": 0.02,
            "stopped_angular_velocity_rps": 0.05,
        },
        "timeouts": {"ros_readiness_seconds": 90.0, "stopped_seconds": 5.0},
        "calibration": {
            "simulation_only": True,
            "excluded_from_task_metrics": True,
            "injection": {
                "method": "gazebo_spawn_sdf_model_static_overlap",
                "model_name": "fireclaw_collision_calibration_probe",
                "reference_frame": "world",
                "world_pose": {
                    "x": -1.95,
                    "y": -0.5,
                    "z": 0.08,
                    "yaw": 0.0,
                },
                "asset_label": "collision_probe",
            },
            "detection_timeout_seconds": 10.0,
            "minimum_prohibited_contact_states": 3,
            "minimum_collision_episodes": 1,
            "maximum_robot_displacement_m": 0.15,
        },
    }
    artifact_names = sorted([
        path.relative_to(proof).as_posix()
        for path in proof.rglob("*")
        if path.is_file()
    ] + ["run-manifest.json"])
    _write_json(proof / "run-manifest.json", {
        "schema_version": (
            "fireclaw.gazebo-collision-calibration-proof/v1"
        ),
        "run_id": "collision-positive-control-fixture",
        "scenario": scenario,
        "status": "passed",
        "started_at": "2026-08-10T00:00:00+00:00",
        "completed_at": "2026-08-10T00:00:06+00:00",
        "repository": {
            "commit": "c" * 40,
            "branch": "test",
            "dirty": False,
        },
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
        "asset_hashes": {
            label: {"path": str(path), "sha256": _sha256(path)}
            for label, path in asset_paths.items()
        },
        "artifacts": artifact_names,
    })
    return proof


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
