from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from fireclaw_core.devtools.ros_gazebo_system_eval import (
    run_ros_gazebo_system_eval,
)
from fireclaw_core.evaluation.artifacts import EvaluationRunBundle
from fireclaw_core.evaluation.system import collect_ros_gazebo_case


_OUTCOMES = {
    "success": "completed",
    "cancel": "cancelled",
    "timeout": "timed_out",
    "abort": "failed",
    "stall_recover": "completed",
    "stall_escalate": "escalated",
}
_ACTIONLIB = {
    "success": [3],
    "cancel": [2, 8],
    "timeout": [2, 8],
    "abort": [4],
    "stall_recover": [2, 3, 8],
    "stall_escalate": [2, 8],
}


def test_ros_gazebo_system_lane_normalizes_all_live_outcomes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "fireclaw_core.devtools.ros_gazebo_system_eval.repository_snapshot",
        lambda _root: {
            "commit": "c" * 40,
            "branch": "test",
            "dirty": False,
        },
    )
    proofs = [
        _write_proof(tmp_path, scenario_type)
        for scenario_type in _OUTCOMES
    ]
    output = tmp_path / "system-bundle"

    result = run_ros_gazebo_system_eval(
        source_proof_dirs=proofs,
        output_dir=output,
        split="test",
        repeat_indices=[0, 0, 0, 0, 0, 0],
        run_id="system-test-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["status"] == "pass"
    assert result["paper_evidence_complete"] is True
    assert result["scenario_count"] == 6
    assert result["metrics"]["contract_pass_rate"] == 1.0
    assert result["metrics"]["terminal_match_rate"] == 1.0
    assert result["metrics"]["task_success_rate"] == 0.333333
    assert result["metrics"]["safe_stop_rate"] == 1.0
    assert result["metrics"]["diagnostics_evidence_rate"] == 1.0
    assert result["metrics"]["recovery_success_rate"] == 1.0
    assert result["metrics"]["escalation_correctness_rate"] == 1.0
    assert result["metrics"]["collision_free_rate"] == 1.0
    assert result["outcome_counts"] == {
        "blocked": 0,
        "cancelled": 1,
        "completed": 2,
        "escalated": 1,
        "failed": 1,
        "lost": 0,
        "timed_out": 1,
        "missing": 0,
    }
    assert (output / "artifact-manifest.json").is_file()
    assert list(output.glob("cases/*/source-proof/robot-events.jsonl"))
    assert list(output.glob("cases/*/source-assets/map-map.yaml"))


def test_missing_collision_evidence_is_not_counted_as_collision_free(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success", collision=False)

    case = collect_ros_gazebo_case(proof, split="development")

    assert case.record["contract_passed"] is True
    assert case.record["collision_metric_available"] is False
    assert case.record["collision_free"] is None
    assert case.record["paper_evidence_complete"] is False
    assert "collision_evidence" in case.record["missing_data"]


def test_scalar_only_collision_claim_is_not_trusted(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    _write_json(proof / "collision-evidence.json", {
        "status": "captured",
        "collision_count": 0,
    })
    _refresh_manifest_artifacts(proof)

    case = collect_ros_gazebo_case(proof, split="test")

    assert case.record["contract_passed"] is True
    assert case.record["collision_metric_available"] is False
    assert case.record["collision_free"] is None
    assert case.record["paper_evidence_complete"] is False
    assert case.evidence_summary["collision_validation_checks"] == {
        "schema": False,
        "captured": True,
        "instrumentation_complete": False,
        "topics_complete": False,
        "robot_scope": False,
        "filter_policy": False,
        "monitor_binary": False,
        "window_complete": False,
        "stream_declared": False,
        "stream_complete": False,
        "stream_classifications": True,
        "classification_totals": False,
        "episodes_consistent": False,
        "robot_description_asset": True,
        "collision_monitor_asset": True,
    }


def test_valid_prohibited_contact_is_counted_as_collision(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    captured_at = "2026-08-10T00:00:01+00:00"
    collision_pair = [
        "turtlebot3_burger::wheel_left_link::wheel_left_link_collision",
        "turtlebot3_world::wall_1::collision",
    ]
    record = {
        "captured_at": captured_at,
        "sim_time_seconds": 1.0,
        "sensor_topic": "/fireclaw/acceptance/contacts",
        "sensor_link": "wheel_left_link",
        "collision1_name": collision_pair[0],
        "collision2_name": collision_pair[1],
        "classification": "prohibited_collision",
        "classification_reason": "robot_contact_with_non_support_surface",
        "episode_id": "collision-0001",
        "contact_point_count": 1,
        "contact_positions": [],
        "contact_normals": [],
        "depths_m": [],
        "max_depth_m": None,
    }
    _write_jsonl(proof / "collision-contact-stream.jsonl", [record])
    evidence_path = proof / "collision-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["instrumentation"]["topics"][0]["contact_state_count"] = 1
    evidence["raw_stream"]["record_count"] = 1
    evidence["contact_state_count"] = 1
    evidence["prohibited_contact_state_count"] = 1
    evidence["collision_episodes"] = [{
        "episode_id": "collision-0001",
        "collision_pair": sorted(collision_pair),
        "first_observed_at": captured_at,
        "last_observed_at": captured_at,
        "sample_count": 1,
        "sensor_topics": ["/fireclaw/acceptance/contacts"],
    }]
    evidence["collision_count"] = 1
    evidence["collision_free"] = False
    _write_json(evidence_path, evidence)
    _refresh_manifest_artifacts(proof)

    case = collect_ros_gazebo_case(proof, split="test")

    assert case.record["collision_metric_available"] is True
    assert case.record["collision_count"] == 1
    assert case.record["collision_free"] is False


def test_scorer_recomputes_contact_classification_from_raw_pair(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    record = {
        "captured_at": "2026-08-10T00:00:01+00:00",
        "sim_time_seconds": 1.0,
        "sensor_topic": "/fireclaw/acceptance/contacts",
        "sensor_link": "wheel_left_link",
        "collision1_name": (
            "turtlebot3_burger::wheel_left_link::wheel_left_link_collision"
        ),
        "collision2_name": "turtlebot3_world::wall_1::collision",
        "classification": "allowed_support_contact",
        "classification_reason": "support_link_on_ground_plane",
        "episode_id": None,
        "contact_point_count": 1,
        "contact_positions": [],
        "contact_normals": [],
        "depths_m": [],
        "max_depth_m": None,
    }
    _write_jsonl(proof / "collision-contact-stream.jsonl", [record])
    evidence_path = proof / "collision-evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["instrumentation"]["topics"][0]["contact_state_count"] = 1
    evidence["raw_stream"]["record_count"] = 1
    evidence["contact_state_count"] = 1
    evidence["allowed_support_contact_state_count"] = 1
    _write_json(evidence_path, evidence)
    _refresh_manifest_artifacts(proof)

    case = collect_ros_gazebo_case(proof, split="test")

    assert case.record["collision_metric_available"] is False
    assert case.record["collision_free"] is None
    assert (
        case.evidence_summary["collision_validation_checks"]
        ["stream_classifications"]
        is False
    )


def test_gazebo_library_version_is_valid_fallback_provenance(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    versions_path = proof / "system-versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    versions["commands"]["gazebo"] = {
        "available": False,
        "returncode": 255,
        "stdout": "Gazebo multi-robot simulator, version 11.15.1",
    }
    versions["commands"]["gazebo_library"] = {
        "available": True,
        "returncode": 0,
        "stdout": "11.15.1",
    }
    _write_json(versions_path, versions)
    _refresh_manifest_artifacts(proof)

    case = collect_ros_gazebo_case(proof, split="test")

    assert case.record["provenance_complete"] is True
    assert case.record["paper_evidence_complete"] is True


def test_system_lane_retains_terminal_mismatch_as_warn(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    final_report = proof / "final-report.json"
    value = json.loads(final_report.read_text(encoding="utf-8"))
    value["status"] = "failed"
    _write_json(final_report, value)
    _refresh_manifest_artifacts(proof)

    result = run_ros_gazebo_system_eval(
        source_proof_dirs=[proof],
        output_dir=tmp_path / "warn-bundle",
        split="test",
        run_id="system-warn-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["status"] == "warn"
    assert result["metrics"]["terminal_match_rate"] == 0.0
    record = json.loads(
        (tmp_path / "warn-bundle" / "scenarios.jsonl")
        .read_text(encoding="utf-8")
    )
    assert record["observed_terminal_outcome"] == "completed"
    assert record["contract_passed"] is False


def test_system_lane_can_embed_into_one_source_proof(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    output = proof / "evaluation"

    result = run_ros_gazebo_system_eval(
        source_proof_dirs=[proof],
        output_dir=output,
        split="test",
        run_id="nested-system-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert result["status"] == "pass"
    inventory = next(output.glob("cases/*/source-artifact-inventory.json"))
    value = json.loads(inventory.read_text(encoding="utf-8"))
    assert not any(
        item["path"].startswith("evaluation/") for item in value["files"]
    )

    recollected = collect_ros_gazebo_case(proof, split="test")
    assert not any(
        item["path"].startswith("evaluation/")
        for item in recollected.source_inventory["files"]
    )


def test_reference_only_bundle_is_never_marked_paper_complete(
    tmp_path: Path,
) -> None:
    proof = _write_proof(tmp_path, "success")
    output = tmp_path / "reference-only"

    result = run_ros_gazebo_system_eval(
        source_proof_dirs=[proof],
        output_dir=output,
        split="test",
        embed_source_proofs=False,
        run_id="reference-only-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    record = json.loads(
        (output / "scenarios.jsonl").read_text(encoding="utf-8")
    )
    assert result["paper_evidence_complete"] is False
    assert record["paper_evidence_complete"] is False
    assert "source_proof_not_embedded" in record["missing_data"]


def test_embedding_hash_mismatch_warns_without_relabeling_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    proof = _write_proof(tmp_path, "success")
    output = tmp_path / "corrupted-embedding"
    original_copy = EvaluationRunBundle.copy_file
    corrupted = False

    def corrupt_first_source_copy(self, name, source):
        nonlocal corrupted
        target = original_copy(self, name, source)
        if not corrupted and "source-proof" in Path(name).parts:
            target.write_bytes(b"corrupted after copy")
            corrupted = True
        return target

    monkeypatch.setattr(
        EvaluationRunBundle,
        "copy_file",
        corrupt_first_source_copy,
    )

    result = run_ros_gazebo_system_eval(
        source_proof_dirs=[proof],
        output_dir=output,
        split="test",
        run_id="corrupted-embedding-run",
        repo_root=Path(__file__).resolve().parents[1],
    )

    record = json.loads(
        (output / "scenarios.jsonl").read_text(encoding="utf-8")
    )
    assert result["status"] == "warn"
    assert result["error_count"] == 1
    assert result["metrics"]["contract_pass_rate"] == 1.0
    assert record["contract_passed"] is True
    assert record["paper_evidence_complete"] is False
    assert "source_proof_embedding_error" in record["missing_data"]


def _write_proof(
    root: Path,
    scenario_type: str,
    *,
    collision: bool = True,
) -> Path:
    proof = root / f"proof-{scenario_type}"
    proof.mkdir()
    assets_root = root / "assets"
    assets_root.mkdir(exist_ok=True)
    asset_paths: dict[str, Path] = {}
    for label, suffix in (
        ("launch", ".launch"),
        ("robot_description", ".xacro"),
        ("collision_monitor", ".cpp"),
        ("world", ".world"),
        ("map", ".yaml"),
        ("map_image", ".pgm"),
        ("ros1_config", ".yaml"),
    ):
        path = assets_root / f"{label}{suffix}"
        path.write_bytes(f"{label}-fixture".encode("utf-8"))
        asset_paths[label] = path

    task_id = f"task-{scenario_type}"
    mission_id = f"mission-{scenario_type}"
    terminal = _OUTCOMES[scenario_type]
    scenario = _scenario(scenario_type, terminal, asset_paths)
    reason = (
        "persistent_navigation_stall"
        if scenario_type == "stall_escalate"
        else None
    )
    evidence_id = "rosdiag-fixture"
    trace_extra = {
        "reason_code": reason,
        "evidence_ids": [evidence_id] if reason else [],
    }
    _write_json(proof / "readiness.json", {
        "status": "ready",
        "action_server": "/move_base",
    })
    _write_json(proof / "plugin-inventory.json", _plugin_inventory())
    _write_json(proof / "robot-task-trace.json", {
        "task_id": task_id,
        "status": terminal,
        "result": {"status": terminal, **trace_extra},
    })
    _write_json(proof / "authorization-task-trace.json", {
        "task_id": task_id,
        "status": "awaiting_confirmation",
    })
    _write_json(proof / "mission-run.json", {
        "mission_id": mission_id,
        "run_status": terminal,
        "status": terminal,
        "terminal": True,
        "use_scheduler": True,
    })
    _write_json(proof / "mission-trace.json", {
        "mission_id": mission_id,
        "status": terminal,
        "subtasks": [{"task_id": task_id, "status": terminal}],
        **trace_extra,
    })
    _write_json(proof / "final-report.json", {
        "mission_id": mission_id,
        "status": terminal,
        **trace_extra,
    })
    _write_json(proof / "pose-evidence.json", _pose_evidence(scenario_type))
    _write_json(proof / "ros-graph.json", {"status": "captured"})
    _write_json(proof / "system-versions.json", _system_versions())
    _write_json(proof / "navigation-parameters.json", {
        "captured_at": "2026-08-10T00:00:00+00:00",
        "dwa": {"max_vel_x": 0.22, "min_vel_x": 0.0},
    })
    _write_json(proof / "navigation-diagnostics.json", {
        "status": "ok" if "stall" in scenario_type else "not_applicable",
        "evidence_id": evidence_id if "stall" in scenario_type else None,
    })
    if collision:
        monitor_binary = b"fireclaw-contact-monitor-fixture"
        (proof / "collision-monitor-plugin.so").write_bytes(monitor_binary)
        _write_jsonl(proof / "collision-contact-stream.jsonl", [])
        _write_json(
            proof / "collision-evidence.json",
            _collision_evidence(
                sha256(monitor_binary).hexdigest(),
            ),
        )
    _write_scenario_evidence(proof, scenario_type, terminal, task_id)
    _write_jsonl(proof / "goal-and-feedback.jsonl", _goal_records(scenario_type))
    _write_jsonl(proof / "authorization-events.jsonl", [
        {"type": "authorization.requested", "task_id": task_id},
        {"type": "confirmation.pending", "task_id": task_id},
        {"type": "task.awaiting_confirmation", "task_id": task_id},
    ])
    _write_jsonl(proof / "robot-events.jsonl", [
        {"type": "authorization.approved", "task_id": task_id},
        {"type": "confirmation.confirmed", "task_id": task_id},
        {"type": f"task.{terminal}", "task_id": task_id},
    ])
    _write_jsonl(proof / "mission-events.jsonl", [
        {"type": "mission.run_accepted", "mission_id": mission_id},
        {"type": "mission.report_ready", "mission_id": mission_id},
    ])
    dispatch_path = proof / "fireclaw" / "missions.dispatch.jsonl"
    dispatch_path.parent.mkdir()
    _write_jsonl(dispatch_path, [{"mission_id": mission_id, "revision": 1}])
    started = datetime(2026, 8, 10, tzinfo=timezone.utc)
    manifest = {
        "schema_version": "fireclaw.gazebo-acceptance-proof/v1",
        "run_id": f"source-{scenario_type}",
        "scenario": scenario,
        "status": "passed",
        "started_at": started.isoformat(),
        "completed_at": (started + timedelta(seconds=2)).isoformat(),
        "repository": {
            "commit": "a" * 40,
            "branch": "test",
            "dirty": False,
        },
        "environment": {"ros_distro": "noetic"},
        "execution": {
            "use_scheduler": True,
            "background": True,
        },
        "asset_hashes": {
            label: {
                "path": str(path),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for label, path in asset_paths.items()
        },
        "mission_id": mission_id,
        "authorization_task_id": task_id,
        "task_id": task_id,
        "adapter_trap_call_count": 0,
    }
    _write_json(proof / "run-manifest.json", manifest)
    _refresh_manifest_artifacts(proof)
    return proof


def _collision_evidence(library_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "fireclaw.gazebo-collision-evidence/v1",
        "status": "captured",
        "robot": {
            "id": "gazebo-robot",
            "model": "burger",
            "gazebo_model_name": "turtlebot3_burger",
        },
        "instrumentation": {
            "status": "complete",
            "source": "gazebo.physics.ContactManager",
            "sensor_plugin": (
                "libfireclaw_gazebo_contact_monitor.so"
            ),
            "message_type": "gazebo_msgs/ContactsState",
            "library": {
                "source_path": "/fixture/contact-monitor.so",
                "sha256": library_sha256,
                "embedded_artifact": "collision-monitor-plugin.so",
            },
            "topics": [{
                "topic": "/fireclaw/acceptance/contacts",
                "message_count": 10,
                "contact_state_count": 0,
                "produced_messages": True,
                "publisher_connections_at_finalize": 1,
            }],
        },
        "observation_window": {
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
            "record_count": 0,
            "truncated": False,
        },
        "contact_state_count": 0,
        "allowed_support_contact_state_count": 0,
        "prohibited_contact_state_count": 0,
        "collision_episodes": [],
        "collision_count": 0,
        "collision_free": True,
    }


def _scenario(
    scenario_type: str,
    terminal: str,
    asset_paths: dict[str, Path],
) -> dict[str, Any]:
    scenario: dict[str, Any] = {
        "schema_version": "fireclaw.gazebo-acceptance/v1",
        "scenario_version": "1.0.0",
        "scenario_id": f"fixture-{scenario_type}",
        "scenario_type": scenario_type,
        "seed": 7,
        "robot": {
            "id": "gazebo-robot",
            "model": "burger",
            "initial_pose": {"frame_id": "map", "x": 0.0, "y": 0.0, "yaw": 0.0},
        },
        "mission": {
            "command": "Navigate in the fixture.",
            "capability": "patrol",
            "required_tool": "navigate_to_point",
        },
        "goal": {"frame_id": "map", "x": 1.0, "y": 0.0, "yaw": 0.0},
        "expected": {
            "plugin_owner": "fireclaw.navigation.move-base",
            "backend_class": "Ros1MoveBaseBackend",
            "action_name": "/move_base",
            "terminal_status": terminal,
            "actionlib_terminal_statuses": _ACTIONLIB[scenario_type],
        },
        "assertions": {
            "require_feedback": True,
            "minimum_displacement_m": 0.5,
            "position_tolerance_m": 0.25,
            "yaw_tolerance_rad": 0.35,
        },
        "timeouts": {"mission_seconds": 30.0},
        "assets": {key: str(value) for key, value in asset_paths.items()},
    }
    if scenario_type == "cancel":
        scenario["cancellation"] = {
            "minimum_feedback_count": 1,
            "minimum_displacement_m": 0.1,
            "maximum_post_cancel_displacement_m": 0.5,
        }
    if scenario_type == "timeout":
        scenario["timeout"] = {
            "execution_timeout_seconds": 3.0,
            "minimum_feedback_count": 1,
            "minimum_displacement_m": 0.1,
            "maximum_post_timeout_displacement_m": 0.5,
        }
    if scenario_type in {"stall_recover", "stall_escalate"}:
        recover = scenario_type == "stall_recover"
        scenario["stall"] = {
            "maximum_stall_displacement_m": 0.1,
            "minimum_feedback_count": 2,
            "maximum_recovery_attempts": 1 if recover else 0,
            "required_diagnostic_finding_codes": ["navigation_action_failed"],
            "recovery_parameters": (
                {"max_vel_x": 0.22, "min_vel_x": 0.0}
                if recover
                else None
            ),
            "escalation_reason_code": (
                None if recover else "persistent_navigation_stall"
            ),
        }
    if scenario_type == "abort":
        scenario["abort"] = {
            "maximum_displacement_m": 0.5,
            "minimum_feedback_count": 1,
        }
    return scenario


def _plugin_inventory() -> dict[str, Any]:
    source_hash = "b" * 64
    plugins = {
        "api_versions": ["1"],
        "plugins": [{
            "plugin_id": "fireclaw.navigation.move-base",
            "version": "0.1.0",
            "source_files": {
                "manifest": {"path": "plugin.json", "sha256": source_hash},
                "entrypoint": {"path": "entrypoint.py", "sha256": source_hash},
            },
        }],
        "contributions": [],
    }
    plugins["inventory_sha256"] = sha256(
        json.dumps(plugins, sort_keys=True).encode("utf-8")
    ).hexdigest()
    tools = {
        "physical_tools": [{
            "name": "navigate_to_point",
            "input_schema": {"type": "object"},
        }],
        "agent_tools": {"tools": []},
    }
    tools["inventory_sha256"] = sha256(
        json.dumps(tools, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "owner_plugin_id": "fireclaw.navigation.move-base",
        "contribution_id": "navigate_to_point",
        "backend_class": "Ros1MoveBaseBackend",
        "action_name": "/move_base",
        "reproducibility_plugin_inventory": plugins,
        "tool_inventory": tools,
    }


def _system_versions() -> dict[str, Any]:
    return {
        "commands": {
            name: {"available": True, "stdout": value}
            for name, value in {
                "ros_distro": "noetic",
                "ros_core": "1.15.15",
                "move_base": "1.17.3",
                "gazebo_ros": "2.9.2",
                "gazebo": "Gazebo multi-robot simulator, version 11.11.0",
            }.items()
        }
    }


def _pose_evidence(scenario_type: str) -> dict[str, Any]:
    result = {
        "stopped": {"status": "stopped"},
        "position_error_m": 0.05,
        "goal_position_error_m": 0.05,
        "yaw_error_rad": 0.02,
        "actual_displacement_m": 1.0,
    }
    if "stall" in scenario_type:
        result["first_attempt_max_displacement_m"] = 0.0
    return result


def _write_scenario_evidence(
    proof: Path,
    scenario_type: str,
    terminal: str,
    task_id: str,
) -> None:
    if scenario_type in {"cancel", "timeout"}:
        cancel = scenario_type == "cancel"
        _write_json(
            proof
            / ("cancellation-evidence.json" if cancel else "timeout-evidence.json"),
            {
                "action_terminal_output": {
                    "cancellation_acknowledged": True,
                    "runtime_stopped": True,
                    "resource_release_safe": True,
                    "cancellation_reason": (
                        "operator_cancelled" if cancel else "deadline_exceeded"
                    ),
                },
                "stop_latency_seconds": 0.2,
                "trigger": {"displacement_m": 0.2},
                (
                    "post_cancel_displacement_m"
                    if cancel
                    else "post_timeout_displacement_m"
                ): 0.05,
            },
        )
    elif "stall" in scenario_type:
        recover = scenario_type == "stall_recover"
        evidence_id = "rosdiag-fixture"
        before = {
            "captured_at": "2026-08-10T00:00:00+00:00",
            "dwa": {"max_vel_x": 0.0, "min_vel_x": 0.0},
        }
        after = {
            "captured_at": "2026-08-10T00:00:01+00:00",
            "dwa": {
                "max_vel_x": 0.22 if recover else 0.0,
                "min_vel_x": 0.0,
            },
        }
        action_terminals = [{
            "payload": {
                "output": {
                    "task_id": task_id,
                    "cancellation_acknowledged": True,
                    "runtime_stopped": True,
                    "resource_release_safe": True,
                }
            }
        }]
        _write_json(proof / "navigation-parameters-before.json", before)
        _write_json(proof / "navigation-parameters-after.json", after)
        _write_json(proof / "stall-evidence.json", {
            "mode": scenario_type,
            "action_terminals": action_terminals,
            "diagnostic_evidence_id": evidence_id,
            "diagnostic_finding_codes": ["navigation_action_failed"],
            "first_attempt_max_displacement_m": 0.0,
            "first_goal_feedback_count": 5,
            "goal_ids": ["goal-1", *( ["goal-2"] if recover else [])],
            "navigation_parameters_before": before,
            "navigation_parameters_after": after,
            "recovery_attempt_count": 1 if recover else 0,
        })
    elif scenario_type == "abort":
        _write_json(proof / "abort-evidence.json", {
            "action_terminal_output": {
                "error_code": "move_base_aborted",
                "goal_state": 4,
                "runtime_stopped": True,
            },
            "robot_displacement_m": 0.0,
            "map": {"goal_inside_axis_aligned_bounds": False},
            "terminal": terminal,
        })


def _goal_records(scenario_type: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = [{"kind": "goal", "goal_id": "goal-1"}]
    if scenario_type == "stall_recover":
        records.append({"kind": "goal", "goal_id": "goal-2"})
    records.extend({"kind": "feedback", "goal_id": "goal-1"} for _ in range(5))
    statuses = [
        3 if scenario_type == "success" else 4 if scenario_type == "abort" else 2
    ]
    if scenario_type == "stall_recover":
        statuses.append(3)
    records.extend({
        "kind": "status",
        "statuses": [{"goal_id": f"goal-{index + 1}", "status": status}],
    } for index, status in enumerate(statuses))
    return records


def _refresh_manifest_artifacts(proof: Path) -> None:
    path = proof / "run-manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["artifacts"] = sorted(
        item.relative_to(proof).as_posix()
        for item in proof.rglob("*")
        if item.is_file()
    )
    _write_json(path, value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in values),
        encoding="utf-8",
    )
