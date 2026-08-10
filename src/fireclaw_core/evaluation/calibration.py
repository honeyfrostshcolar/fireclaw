"""Independent scoring for the Gazebo collision positive control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from fireclaw_core.evaluation.artifacts import canonical_json_sha256
from fireclaw_core.evaluation.contracts import (
    DATASET_SPLITS,
    EVALUATION_RUN_SCHEMA_VERSION,
)
from fireclaw_core.evaluation.system import (
    inventory_source_proof,
    validate_collision_evidence,
    validate_source_assets,
    version_evidence_complete,
)


GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION = (
    "fireclaw.evaluation.gazebo-collision-calibration.v1"
)
GAZEBO_COLLISION_CALIBRATION_PROOF_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-calibration-proof/v1"
)
GAZEBO_COLLISION_CALIBRATION_SCENARIO_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-calibration/v1"
)
GAZEBO_COLLISION_INJECTION_EVIDENCE_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-injection-evidence/v1"
)
GAZEBO_COLLISION_CALIBRATION_METRIC_DEFINITIONS = {
    "calibration_pass_rate": {
        "kind": "rate",
        "numerator": "positive-control cases satisfying every calibration check",
        "denominator": "all supplied positive-control cases, including collection errors",
        "missing_policy": "collection errors and incomplete proofs count as false",
    },
    "collision_detection_success_rate": {
        "kind": "rate",
        "numerator": (
            "cases with independently validated raw prohibited contacts and "
            "episodes involving the fixed calibration probe"
        ),
        "denominator": "all supplied positive-control cases",
        "missing_policy": "missing or inconsistent raw evidence counts as false",
    },
    "no_task_dispatch_rate": {
        "kind": "rate",
        "numerator": (
            "cases with no Mission/task identity, move_base goal, feedback, "
            "or non-zero cmd_vel"
        ),
        "denominator": "all supplied positive-control cases",
        "missing_policy": "missing dispatch evidence counts as false",
    },
    "robot_remained_stopped_rate": {
        "kind": "rate",
        "numerator": (
            "cases stopped before injection and after cleanup with displacement "
            "within the configured bound"
        ),
        "denominator": "all supplied positive-control cases",
        "missing_policy": "missing pose or stop proof counts as false",
    },
    "probe_contact_state_count": {
        "kind": "count",
        "value": "independently classified raw prohibited states involving the probe",
        "missing_policy": "unavailable raw collision evidence is missing, never zero",
    },
    "probe_collision_episode_count": {
        "kind": "count",
        "value": "validated prohibited collision episodes involving the probe",
        "missing_policy": "unavailable raw collision evidence is missing, never zero",
    },
    "robot_displacement_m": {
        "kind": "continuous",
        "value": "map-frame displacement from pre-injection to post-cleanup pose",
        "missing_policy": "missing pose evidence is excluded and counted",
    },
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROBE_MODEL_NAME = "fireclaw_collision_calibration_probe"
_REQUIRED_JSON = (
    "readiness.json",
    "collision-injection.json",
    "collision-evidence.json",
    "pose-evidence.json",
    "ros-graph.json",
    "system-versions.json",
    "navigation-parameters.json",
    "map-evidence.json",
    "plugin-inventory.json",
    "tool-inventory.json",
)
_REQUIRED_JSONL = (
    "collision-contact-stream.jsonl",
    "goal-and-feedback.jsonl",
)


@dataclass(frozen=True)
class CollisionCalibrationCaseResult:
    source_dir: Path
    case_id: str
    record: dict[str, Any]
    scenario: dict[str, Any]
    score: dict[str, Any]
    evidence_summary: dict[str, Any]
    source_manifest: dict[str, Any]
    source_inventory: dict[str, Any]
    asset_inventory: dict[str, Any]
    source_versions: dict[str, Any]
    source_files: tuple[tuple[str, Path], ...]
    asset_files: tuple[tuple[str, Path], ...]


def collect_gazebo_collision_calibration_case(
    source_proof_dir: str | Path,
    *,
    split: str = "development",
    repeat_index: int = 0,
    exclude_paths: Iterable[str | Path] = (),
    max_source_bytes: int = 512 * 1024 * 1024,
) -> CollisionCalibrationCaseResult:
    """Verify one positive-control proof without invoking ROS or Gazebo."""

    if split not in DATASET_SPLITS:
        raise ValueError(f"split must be one of {sorted(DATASET_SPLITS)}")
    if (
        isinstance(repeat_index, bool)
        or not isinstance(repeat_index, int)
        or repeat_index < 0
    ):
        raise ValueError("repeat_index must be a non-negative integer")
    if (
        isinstance(max_source_bytes, bool)
        or not isinstance(max_source_bytes, int)
        or max_source_bytes <= 0
    ):
        raise ValueError("max_source_bytes must be a positive integer")

    source = Path(source_proof_dir).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("source_proof_dir must be a directory")
    manifest = _read_json(source / "run-manifest.json")
    if (
        manifest.get("schema_version")
        != GAZEBO_COLLISION_CALIBRATION_PROOF_SCHEMA_VERSION
    ):
        raise ValueError("unsupported collision calibration proof schema")
    scenario = manifest.get("scenario")
    if not isinstance(scenario, dict):
        raise ValueError("calibration proof requires a scenario object")
    if (
        scenario.get("schema_version")
        != GAZEBO_COLLISION_CALIBRATION_SCENARIO_SCHEMA_VERSION
    ):
        raise ValueError("unsupported collision calibration scenario schema")
    if scenario.get("scenario_type") != "collision_calibration":
        raise ValueError("calibration scenario_type must be collision_calibration")
    if scenario.get("simulation_only") is not True:
        raise ValueError("collision calibration must be simulation_only")
    if scenario.get("excluded_from_task_metrics") is not True:
        raise ValueError("collision calibration must be excluded from task metrics")

    scenario_id = _safe_id(scenario.get("scenario_id"), "scenario_id")
    source_run_id = _safe_id(manifest.get("run_id"), "source run_id")
    seed = scenario.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("calibration seed must be a non-negative integer")
    identity_hash = canonical_json_sha256({
        "scenario_id": scenario_id,
        "source_run_id": source_run_id,
        "seed": seed,
        "repeat_index": repeat_index,
    })[:12]
    case_id = (
        f"{scenario_id[:80]}-s{seed}-r{repeat_index}-{identity_hash}"
    )

    excluded = (
        *(Path(item).resolve(strict=False) for item in exclude_paths),
        (source / "evaluation").resolve(strict=False),
    )
    source_inventory, source_files = inventory_source_proof(
        source,
        declared_artifacts=manifest.get("artifacts"),
        exclude_paths=excluded,
        max_source_bytes=max_source_bytes,
    )
    documents: dict[str, dict[str, Any]] = {}
    streams: dict[str, list[dict[str, Any]]] = {}
    missing_documents: list[str] = []
    for name in _REQUIRED_JSON:
        path = source / name
        if path.is_file():
            documents[name] = _read_json(path)
        else:
            documents[name] = {}
            missing_documents.append(name)
    for name in _REQUIRED_JSONL:
        path = source / name
        if path.is_file():
            streams[name] = _read_jsonl(path)
        else:
            streams[name] = []
            missing_documents.append(name)

    asset_inventory, asset_files = validate_source_assets(manifest)
    score, evidence_summary = score_gazebo_collision_calibration_case(
        manifest,
        scenario,
        documents=documents,
        streams=streams,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        missing_documents=missing_documents,
        split=split,
    )
    metric_values = dict(score["metric_values"])
    record = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION,
        "case_id": case_id,
        "scenario_id": scenario_id,
        "scenario_version": "1.0.0",
        "suite_id": "fireclaw-gazebo-collision-calibration",
        "suite_version": "1.0.0",
        "lane": "gazebo_collision_calibration",
        "split": split,
        "seed": seed,
        "repeat_index": repeat_index,
        "scenario_type": "collision_calibration",
        "simulation_only": True,
        "excluded_from_task_metrics": True,
        "task_metrics_applicable": False,
        "task_success": None,
        "source_run_id": source_run_id,
        "source_repository": manifest.get("repository"),
        **metric_values,
        "missing_data": score["missing_data"],
        "source_proof_sha256": source_inventory["inventory_sha256"],
        "asset_inventory_sha256": asset_inventory["inventory_sha256"],
        "error": None,
    }
    return CollisionCalibrationCaseResult(
        source_dir=source,
        case_id=case_id,
        record=record,
        scenario=dict(scenario),
        score=score,
        evidence_summary=evidence_summary,
        source_manifest=manifest,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        source_versions=documents["system-versions.json"],
        source_files=source_files,
        asset_files=asset_files,
    )


def score_gazebo_collision_calibration_case(
    manifest: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    documents: Mapping[str, dict[str, Any]],
    streams: Mapping[str, list[dict[str, Any]]],
    source_inventory: Mapping[str, Any],
    asset_inventory: Mapping[str, Any],
    missing_documents: list[str],
    split: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    robot = scenario.get("robot")
    if not isinstance(robot, dict):
        robot = {}
    calibration = scenario.get("calibration")
    if not isinstance(calibration, dict):
        calibration = {}
    injection_spec = calibration.get("injection")
    if not isinstance(injection_spec, dict):
        injection_spec = {}
    assertions = scenario.get("assertions")
    if not isinstance(assertions, dict):
        assertions = {}

    readiness = documents["readiness.json"]
    injection = documents["collision-injection.json"]
    collision = documents["collision-evidence.json"]
    pose = documents["pose-evidence.json"]
    goal_stream = streams["goal-and-feedback.jsonl"]
    collision_stream = streams["collision-contact-stream.jsonl"]
    collision_validation = validate_collision_evidence(
        collision,
        collision_stream=collision_stream,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        expected_robot=robot,
    )

    model_name = injection_spec.get("model_name")
    if not isinstance(model_name, str):
        model_name = ""
    model_prefix = f"{model_name}::"
    prohibited_records = [
        item
        for item in collision_stream
        if item.get("classification") == "prohibited_collision"
    ]
    matching_prohibited = [
        item
        for item in prohibited_records
        if _record_involves_model(item, model_prefix)
    ]
    matching_episode_ids = sorted({
        str(item.get("episode_id"))
        for item in matching_prohibited
        if item.get("episode_id")
    })
    minimum_states = calibration.get("minimum_prohibited_contact_states")
    minimum_episodes = calibration.get("minimum_collision_episodes")

    spawn = injection.get("spawn")
    if not isinstance(spawn, dict):
        spawn = {}
    detection = injection.get("detection")
    if not isinstance(detection, dict):
        detection = {}
    cleanup = injection.get("cleanup")
    if not isinstance(cleanup, dict):
        cleanup = {}
    requested_pose = spawn.get("requested_pose")
    if not isinstance(requested_pose, dict):
        requested_pose = {}
    expected_pose = injection_spec.get("world_pose")
    if not isinstance(expected_pose, dict):
        expected_pose = {}
    model_state = spawn.get("model_state")
    if not isinstance(model_state, dict):
        model_state = {}
    actual_spawn_pose = model_state.get("pose")
    if not isinstance(actual_spawn_pose, dict):
        actual_spawn_pose = {}
    response = spawn.get("response")
    if not isinstance(response, dict):
        response = {}
    cleanup_response = cleanup.get("response")
    if not isinstance(cleanup_response, dict):
        cleanup_response = {}
    sdf_asset = spawn.get("sdf_asset")
    if not isinstance(sdf_asset, dict):
        sdf_asset = {}
    probe_asset = (
        asset_inventory.get("assets", {}).get("collision_probe", {})
        if isinstance(asset_inventory.get("assets"), dict)
        else {}
    )

    indices = detection.get("matching_stream_record_indices")
    indices_valid = (
        isinstance(indices, list)
        and len(indices) == len(set(indices))
        and all(
            isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(collision_stream)
            for index in indices
        )
    )
    detected_records = (
        [collision_stream[index] for index in indices]
        if indices_valid
        else []
    )
    detected_episode_ids = sorted({
        str(item.get("episode_id"))
        for item in detected_records
        if item.get("episode_id")
    })

    goal_records = [
        item for item in goal_stream if item.get("kind") == "goal"
    ]
    feedback_records = [
        item for item in goal_stream if item.get("kind") == "feedback"
    ]
    cmd_vel_records = [
        item for item in goal_stream if item.get("kind") == "cmd_vel"
    ]
    cmd_vel_zero = all(
        _number(item.get("linear_x_mps")) is not None
        and _number(item.get("angular_z_rps")) is not None
        and abs(float(item["linear_x_mps"])) <= 1e-9
        and abs(float(item["angular_z_rps"])) <= 1e-9
        for item in cmd_vel_records
    )
    execution = manifest.get("execution")
    if not isinstance(execution, dict):
        execution = {}

    instrumentation_ready = readiness.get("collision_instrumentation")
    if not isinstance(instrumentation_ready, dict):
        instrumentation_ready = {}
    pre_stopped = pose.get("pre_injection_stopped")
    if not isinstance(pre_stopped, dict):
        pre_stopped = {}
    post_stopped = pose.get("stopped")
    if not isinstance(post_stopped, dict):
        post_stopped = {}
    displacement = _number(pose.get("actual_displacement_m"))
    displacement_limit = _number(
        calibration.get("maximum_robot_displacement_m")
    )

    injection_checks = {
        "schema_and_status": (
            injection.get("schema_version")
            == GAZEBO_COLLISION_INJECTION_EVIDENCE_SCHEMA_VERSION
            and injection.get("status") == "complete"
            and injection.get("simulation_only") is True
            and injection.get("excluded_from_task_metrics") is True
        ),
        "spawn_contract": (
            spawn.get("schema_version")
            == GAZEBO_COLLISION_INJECTION_EVIDENCE_SCHEMA_VERSION
            and spawn.get("status") == "spawned"
            and spawn.get("method")
            == "gazebo_spawn_sdf_model_static_overlap"
            and spawn.get("simulation_only") is True
            and spawn.get("model_name") == _PROBE_MODEL_NAME == model_name
            and spawn.get("reference_frame") == "world"
            and spawn.get("spawn_service") == "/gazebo/spawn_sdf_model"
            and spawn.get("state_service") == "/gazebo/get_model_state"
            and response.get("success") is True
            and model_state.get("success") is True
        ),
        "spawn_pose": (
            _poses_match(requested_pose, expected_pose, tolerance=1e-9)
            and _poses_match(actual_spawn_pose, expected_pose, tolerance=1e-5)
        ),
        "probe_asset": (
            isinstance(probe_asset, dict)
            and probe_asset.get("matched") is True
            and sdf_asset.get("sha256")
            == probe_asset.get("actual_sha256")
        ),
        "detection_snapshot": (
            detection.get("status") == "detected"
            and detection.get("expected_other_model") == model_name
            and indices_valid
            and detection.get("prohibited_contact_state_count")
            == len(detected_records)
            and detection.get("collision_episode_ids")
            == detected_episode_ids
            and detection.get("collision_episode_count")
            == len(detected_episode_ids)
            and all(
                item.get("classification") == "prohibited_collision"
                and _record_involves_model(item, model_prefix)
                for item in detected_records
            )
            and _at_least(len(detected_records), minimum_states)
            and _at_least(len(detected_episode_ids), minimum_episodes)
        ),
        "cleanup": (
            cleanup.get("status") == "deleted"
            and cleanup.get("model_name") == model_name
            and cleanup.get("reference_frame") == "world"
            and cleanup.get("delete_service") == "/gazebo/delete_model"
            and cleanup.get("state_service") == "/gazebo/get_model_state"
            and cleanup_response.get("success") is True
            and cleanup.get("post_delete_model_present") is False
        ),
        "ordered": _timestamps_ordered(
            spawn.get("completed_at"),
            detection.get("detected_at"),
            cleanup.get("requested_at"),
            cleanup.get("completed_at"),
        ),
    }
    collision_checks = {
        "trusted_raw_evidence": collision_validation["available"] is True,
        "positive_count": (
            collision.get("collision_free") is False
            and _at_least(len(matching_prohibited), minimum_states)
            and _at_least(len(matching_episode_ids), minimum_episodes)
        ),
        "controlled_pair": (
            bool(prohibited_records)
            and len(prohibited_records) == len(matching_prohibited)
        ),
    }
    no_task_checks = {
        "execution_contract": (
            execution.get("simulation_only") is True
            and execution.get("excluded_from_task_metrics") is True
            and execution.get("task_dispatch") is False
            and execution.get("mission_created") is False
            and execution.get("move_base_goal_sent") is False
        ),
        "no_task_identity": not any(
            key in manifest for key in ("mission_id", "task_id")
        ),
        "no_goal_or_feedback": not goal_records and not feedback_records,
        "no_motion_command": cmd_vel_zero,
        "plugin_not_applicable": (
            documents["plugin-inventory.json"].get("status")
            == "not_applicable"
        ),
        "tool_not_applicable": (
            documents["tool-inventory.json"].get("status")
            == "not_applicable"
        ),
    }
    pose_checks = {
        "pre_injection_stopped": pre_stopped.get("status") == "stopped",
        "post_cleanup_stopped": post_stopped.get("status") == "stopped",
        "bounded_displacement": (
            displacement is not None
            and displacement_limit is not None
            and displacement <= displacement_limit
        ),
        "initial_pose": (
            _at_most(
                pose.get("initial_position_error_m"),
                assertions.get("initial_position_tolerance_m"),
            )
            and _at_most(
                pose.get("initial_yaw_error_rad"),
                assertions.get("initial_yaw_tolerance_rad"),
            )
        ),
    }
    common_checks = {
        "source_proof_passed": manifest.get("status") == "passed",
        "simulation_only": scenario.get("simulation_only") is True,
        "excluded_from_task_metrics": (
            scenario.get("excluded_from_task_metrics") is True
        ),
        "readiness": (
            readiness.get("status") == "ready"
            and instrumentation_ready.get("status") == "ready"
            and instrumentation_ready.get("ready_before_first_goal") is True
        ),
        "required_documents": not missing_documents,
        "source_inventory": (
            bool(source_inventory.get("declared_artifacts"))
            and not source_inventory.get("declared_missing")
        ),
        "asset_integrity": asset_inventory.get("complete") is True,
    }
    calibration_passed = all((
        *common_checks.values(),
        *injection_checks.values(),
        *collision_checks.values(),
        *no_task_checks.values(),
        *pose_checks.values(),
    ))

    provenance_checks = {
        "system_versions": version_evidence_complete(
            documents["system-versions.json"]
        ),
        "navigation_parameters": bool(
            documents["navigation-parameters.json"]
        ),
        "map_evidence": bool(documents["map-evidence.json"]),
        "ros_graph": bool(documents["ros-graph.json"]),
        "source_inventory": common_checks["source_inventory"],
        "asset_integrity": common_checks["asset_integrity"],
    }
    provenance_complete = all(provenance_checks.values())
    source_repository = manifest.get("repository")
    paper_evidence_complete = (
        calibration_passed
        and provenance_complete
        and split in {"validation", "test"}
        and isinstance(source_repository, dict)
        and source_repository.get("dirty") is False
    )
    missing_data = sorted(set([
        *missing_documents,
        *(key for key, passed in common_checks.items() if not passed),
        *(key for key, passed in provenance_checks.items() if not passed),
        *(key for key, passed in injection_checks.items() if not passed),
        *(key for key, passed in collision_checks.items() if not passed),
        *(key for key, passed in no_task_checks.items() if not passed),
        *(key for key, passed in pose_checks.items() if not passed),
    ]))
    metric_values = {
        "calibration_passed": calibration_passed,
        "collision_detection_success": all(collision_checks.values()),
        "collision_metric_available": collision_validation["available"],
        "observed_collision_free": (
            collision.get("collision_free")
            if collision_validation["available"]
            else None
        ),
        "collision_count": collision_validation.get("collision_count"),
        "prohibited_contact_state_count": len(prohibited_records),
        "probe_contact_state_count": len(matching_prohibited),
        "probe_collision_episode_count": len(matching_episode_ids),
        "no_task_dispatch": all(no_task_checks.values()),
        "robot_remained_stopped": all(pose_checks.values()),
        "robot_displacement_m": displacement,
        "asset_integrity": common_checks["asset_integrity"],
        "provenance_complete": provenance_complete,
        "paper_evidence_complete": paper_evidence_complete,
        "source_proof_bytes": source_inventory.get("total_bytes"),
    }
    score = {
        "calibration_passed": calibration_passed,
        "checks": common_checks,
        "injection_checks": injection_checks,
        "collision_checks": collision_checks,
        "no_task_checks": no_task_checks,
        "pose_checks": pose_checks,
        "provenance_checks": provenance_checks,
        "collision_validation_checks": collision_validation["checks"],
        "metric_values": metric_values,
        "missing_data": missing_data,
    }
    evidence_summary = {
        "probe_model_name": model_name,
        "goal_count": len(goal_records),
        "feedback_count": len(feedback_records),
        "cmd_vel_record_count": len(cmd_vel_records),
        "prohibited_contact_state_count": len(prohibited_records),
        "probe_contact_state_count": len(matching_prohibited),
        "probe_collision_episode_ids": matching_episode_ids,
        "detection_snapshot_record_count": len(detected_records),
        "detection_snapshot_episode_ids": detected_episode_ids,
        "collision_validation_checks": collision_validation["checks"],
    }
    return score, evidence_summary


def _record_involves_model(
    record: Mapping[str, Any],
    model_prefix: str,
) -> bool:
    return bool(model_prefix) and any(
        isinstance(record.get(key), str)
        and str(record[key]).startswith(model_prefix)
        for key in ("collision1_name", "collision2_name")
    )


def _poses_match(
    actual: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    tolerance: float,
) -> bool:
    return all(
        _numbers_close(actual.get(key), expected.get(key), tolerance)
        for key in ("x", "y", "z", "yaw")
    )


def _numbers_close(actual: Any, expected: Any, tolerance: float) -> bool:
    left = _number(actual)
    right = _number(expected)
    return (
        left is not None
        and right is not None
        and abs(left - right) <= tolerance
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if isfinite(result) else None


def _at_least(actual: Any, minimum: Any) -> bool:
    left = _number(actual)
    right = _number(minimum)
    return left is not None and right is not None and left >= right


def _at_most(actual: Any, maximum: Any) -> bool:
    left = _number(actual)
    right = _number(maximum)
    return left is not None and right is not None and left <= right


def _timestamps_ordered(*values: Any) -> bool:
    parsed: list[datetime] = []
    for value in values:
        if not isinstance(value, str):
            return False
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if timestamp.tzinfo is None:
            return False
        parsed.append(timestamp)
    return parsed == sorted(parsed)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(
                f"JSONL row must be an object: {path.name}:{line_number}"
            )
        result.append(value)
    return result


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsafe characters")
    return value


__all__ = [
    "CollisionCalibrationCaseResult",
    "GAZEBO_COLLISION_CALIBRATION_PROOF_SCHEMA_VERSION",
    "GAZEBO_COLLISION_CALIBRATION_METRIC_DEFINITIONS",
    "GAZEBO_COLLISION_CALIBRATION_PROTOCOL_VERSION",
    "GAZEBO_COLLISION_CALIBRATION_SCENARIO_SCHEMA_VERSION",
    "GAZEBO_COLLISION_INJECTION_EVIDENCE_SCHEMA_VERSION",
    "collect_gazebo_collision_calibration_case",
    "score_gazebo_collision_calibration_case",
]
