"""Normalize live ROS/Gazebo acceptance proofs into evaluation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
import mimetypes
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from fireclaw_core.evaluation.artifacts import (
    canonical_json_sha256,
    sha256_file,
)
from fireclaw_core.evaluation.contracts import (
    DATASET_SPLITS,
    EVALUATION_RUN_SCHEMA_VERSION,
)
from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_STATUSES,
    normalize_robot_task_terminal_status,
)


ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION = (
    "fireclaw.evaluation.ros-gazebo-system.v1"
)
GAZEBO_ACCEPTANCE_PROOF_SCHEMA_VERSION = (
    "fireclaw.gazebo-acceptance-proof/v1"
)
GAZEBO_ACCEPTANCE_SCENARIO_SCHEMA_VERSION = (
    "fireclaw.gazebo-acceptance/v1"
)
GAZEBO_COLLISION_EVIDENCE_SCHEMA_VERSION = (
    "fireclaw.gazebo-collision-evidence/v1"
)
SYSTEM_SCENARIO_TYPES = frozenset({
    "success",
    "cancel",
    "timeout",
    "abort",
    "stall_recover",
    "stall_escalate",
})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUIRED_JSON = (
    "readiness.json",
    "plugin-inventory.json",
    "robot-task-trace.json",
    "authorization-task-trace.json",
    "mission-trace.json",
    "final-report.json",
    "pose-evidence.json",
    "ros-graph.json",
)
_REQUIRED_JSONL = (
    "goal-and-feedback.jsonl",
    "robot-events.jsonl",
    "authorization-events.jsonl",
    "mission-events.jsonl",
    "fireclaw/missions.dispatch.jsonl",
)
_OPTIONAL_JSONL = ("collision-contact-stream.jsonl",)
_OPTIONAL_JSON = (
    "mission-run.json",
    "system-versions.json",
    "navigation-parameters.json",
    "navigation-parameters-before.json",
    "navigation-parameters-after.json",
    "navigation-diagnostics.json",
    "cancellation-evidence.json",
    "timeout-evidence.json",
    "stall-evidence.json",
    "abort-evidence.json",
    "map-evidence.json",
    "collision-evidence.json",
)
_BASE_ASSET_LABELS = frozenset({
    "launch",
    "world",
    "map",
    "map_image",
    "ros1_config",
})
_OPTIONAL_ASSET_LABELS = frozenset({
    "collision_probe",
    "collision_monitor",
    "robot_description",
})
_COLLISION_SUPPORT_LINKS = frozenset({
    "caster_back_link",
    "wheel_left_link",
    "wheel_right_link",
})
_COLLISION_ROBOT_LINKS = (
    "base_link",
    "wheel_left_link",
    "wheel_right_link",
    "caster_back_link",
    "base_scan",
)
_COLLISION_TOPIC = "/fireclaw/acceptance/contacts"
_COLLISION_POLICY_ID = "fireclaw.acceptance.prohibited-contact/v1"
_COLLISION_EPISODE_GAP_SECONDS = 0.25
_TASK_TERMINAL_EVENT = {
    "blocked": "task.blocked",
    "cancelled": "task.cancelled",
    "completed": "task.completed",
    "escalated": "task.escalated",
    "failed": "task.failed",
    "lost": "task.lost",
    "timed_out": "task.timed_out",
}


@dataclass(frozen=True)
class SystemCaseResult:
    """One normalized live-system case and its content-addressed evidence."""

    source_dir: Path
    case_id: str
    record: dict[str, Any]
    normalized_scenario: dict[str, Any]
    score: dict[str, Any]
    evidence_summary: dict[str, Any]
    source_manifest: dict[str, Any]
    source_inventory: dict[str, Any]
    asset_inventory: dict[str, Any]
    plugin_inventory: dict[str, Any]
    tool_inventory: dict[str, Any]
    source_versions: dict[str, Any]
    source_files: tuple[tuple[str, Path], ...]
    asset_files: tuple[tuple[str, Path], ...]


def collect_ros_gazebo_case(
    source_proof_dir: str | Path,
    *,
    split: str = "development",
    repeat_index: int = 0,
    exclude_paths: Iterable[str | Path] = (),
    max_source_bytes: int = 512 * 1024 * 1024,
) -> SystemCaseResult:
    """Validate one Plugin-owned live proof without invoking ROS/Gazebo."""

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
    manifest = _read_required_json(source / "run-manifest.json")
    if manifest.get("schema_version") != GAZEBO_ACCEPTANCE_PROOF_SCHEMA_VERSION:
        raise ValueError("unsupported Gazebo acceptance proof schema")
    scenario = manifest.get("scenario")
    if not isinstance(scenario, dict):
        raise ValueError("Gazebo proof manifest requires a scenario object")
    if scenario.get("schema_version") != GAZEBO_ACCEPTANCE_SCENARIO_SCHEMA_VERSION:
        raise ValueError("unsupported Gazebo acceptance scenario schema")
    scenario_type = _required_string(scenario, "scenario_type")
    if scenario_type not in SYSTEM_SCENARIO_TYPES:
        raise ValueError(
            f"scenario_type must be one of {sorted(SYSTEM_SCENARIO_TYPES)}"
        )
    scenario_id = _safe_id(
        _required_string(scenario, "scenario_id"),
        "scenario_id",
    )
    source_run_id = _safe_id(
        _required_string(manifest, "run_id"),
        "source run_id",
    )
    seed = scenario.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Gazebo scenario seed must be a non-negative integer")
    case_id = _case_id(
        scenario_id,
        seed=seed,
        repeat_index=repeat_index,
        source_run_id=source_run_id,
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
    missing_documents: list[str] = []
    for name in (*_REQUIRED_JSON, *_OPTIONAL_JSON):
        value = _read_optional_json(source / name)
        if value is None:
            if name in _REQUIRED_JSON:
                missing_documents.append(name)
            documents[name] = {}
        else:
            documents[name] = value

    streams: dict[str, list[dict[str, Any]]] = {}
    for name in (*_REQUIRED_JSONL, *_OPTIONAL_JSONL):
        value = _read_optional_jsonl(source / name)
        if value is None:
            if name in _REQUIRED_JSONL:
                missing_documents.append(name)
            streams[name] = []
        else:
            streams[name] = value

    asset_inventory, asset_files = validate_source_assets(manifest)
    normalized_scenario = _normalize_scenario(
        scenario,
        source_run_id=source_run_id,
        split=split,
        repeat_index=repeat_index,
    )
    score, evidence_summary = score_ros_gazebo_case(
        manifest,
        normalized_scenario,
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
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "case_id": case_id,
        "scenario_id": scenario_id,
        "scenario_version": str(scenario.get("scenario_version") or "1.0.0"),
        "suite_id": "fireclaw-gazebo-navigation-acceptance",
        "suite_version": "1.0.0",
        "lane": "ros_gazebo_system",
        "split": split,
        "seed": seed,
        "repeat_index": repeat_index,
        "target_type": "point",
        "target": normalized_scenario["target"],
        "task_type": "navigation",
        "scenario_type": scenario_type,
        "source_run_id": source_run_id,
        "source_repository": manifest.get("repository"),
        "expected_terminal_outcome": score["expected"]["terminal_outcome"],
        "observed_terminal_outcome": score["observed"]["terminal_outcome"],
        **metric_values,
        "missing_data": score["missing_data"],
        "source_proof_sha256": source_inventory["inventory_sha256"],
        "asset_inventory_sha256": asset_inventory["inventory_sha256"],
        "error": None,
    }
    source_plugin_inventory = documents["plugin-inventory.json"]
    plugin_inventory = source_plugin_inventory.get(
        "reproducibility_plugin_inventory"
    )
    if not isinstance(plugin_inventory, dict):
        plugin_inventory = {
            "status": "legacy_summary_only",
            "summary": source_plugin_inventory,
            "inventory_sha256": canonical_json_sha256(
                source_plugin_inventory
            ),
        }
    tool_inventory = source_plugin_inventory.get("tool_inventory")
    if not isinstance(tool_inventory, dict):
        extension_report = source_plugin_inventory.get("extension_report")
        tool_ids = (
            extension_report.get("tool_ids", [])
            if isinstance(extension_report, dict)
            else []
        )
        tool_inventory = {
            "status": "legacy_ids_only",
            "tool_ids": tool_ids,
            "schemas_recorded": False,
            "inventory_sha256": canonical_json_sha256(tool_ids),
        }
    source_versions = documents["system-versions.json"] or {
        "status": "missing",
        "legacy_environment": manifest.get("environment", {}),
    }
    return SystemCaseResult(
        source_dir=source,
        case_id=case_id,
        record=record,
        normalized_scenario=normalized_scenario,
        score=score,
        evidence_summary=evidence_summary,
        source_manifest=manifest,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        plugin_inventory=plugin_inventory,
        tool_inventory=tool_inventory,
        source_versions=source_versions,
        source_files=source_files,
        asset_files=asset_files,
    )


def score_ros_gazebo_case(
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
    scenario_type = str(scenario["scenario_type"])
    source_scenario = manifest["scenario"]
    expected_raw = source_scenario.get("expected", {}).get("terminal_status")
    expected_terminal = normalize_robot_task_terminal_status(expected_raw)
    if expected_terminal not in ROBOT_TASK_TERMINAL_STATUSES:
        expected_terminal = None

    robot_trace = documents["robot-task-trace.json"]
    authorization_trace = documents["authorization-task-trace.json"]
    mission_trace = documents["mission-trace.json"]
    mission_run = documents["mission-run.json"]
    final_report = documents["final-report.json"]
    readiness = documents["readiness.json"]
    pose = documents["pose-evidence.json"]
    plugin = documents["plugin-inventory.json"]
    goal_stream = streams["goal-and-feedback.jsonl"]
    robot_events = streams["robot-events.jsonl"]
    authorization_events = streams["authorization-events.jsonl"]
    mission_events = streams["mission-events.jsonl"]
    dispatch_records = streams["fireclaw/missions.dispatch.jsonl"]
    collision_stream = streams["collision-contact-stream.jsonl"]

    terminal_sources = {
        "robot_task": normalize_robot_task_terminal_status(
            robot_trace.get("status")
        ),
        "mission_run": normalize_robot_task_terminal_status(
            mission_run.get("run_status", mission_run.get("status"))
        ),
        "final_report": normalize_robot_task_terminal_status(
            final_report.get("status")
        ),
        "compatibility_mission_trace": normalize_robot_task_terminal_status(
            mission_trace.get("status")
        ),
    }
    observed_terminal = (
        terminal_sources["mission_run"]
        or terminal_sources["final_report"]
        or terminal_sources["robot_task"]
    )
    authoritative_values = [
        terminal_sources[name]
        for name in ("robot_task", "final_report")
        if terminal_sources[name] is not None
    ]
    if terminal_sources["mission_run"] is not None:
        authoritative_values.append(terminal_sources["mission_run"])
    terminal_match = (
        expected_terminal is not None
        and len(authoritative_values) >= 2
        and all(item == expected_terminal for item in authoritative_values)
    )

    task_ids = _task_identity_values(
        manifest,
        robot_trace,
        authorization_trace,
        mission_trace,
    )
    same_task_resume = (
        len(task_ids) >= 4 and len(set(task_ids.values())) == 1
    )
    task_id = str(manifest.get("task_id") or "")
    mission_id = str(manifest.get("mission_id") or "")

    goal_records = [item for item in goal_stream if item.get("kind") == "goal"]
    feedback_records = [
        item for item in goal_stream if item.get("kind") == "feedback"
    ]
    actionlib_statuses = {
        int(status["status"])
        for item in goal_stream
        if item.get("kind") == "status"
        for status in item.get("statuses", [])
        if isinstance(status, dict)
        and isinstance(status.get("status"), int)
        and not isinstance(status.get("status"), bool)
    }
    all_robot_events = [*authorization_events, *robot_events]
    robot_event_types = {
        str(item.get("type")) for item in all_robot_events
    }
    mission_event_types = {
        str(item.get("type")) for item in mission_events
    }
    required_authorization_events = {
        "authorization.requested",
        "confirmation.pending",
        "task.awaiting_confirmation",
        "authorization.approved",
        "confirmation.confirmed",
    }
    expected_task_event = (
        _TASK_TERMINAL_EVENT.get(expected_terminal)
        if expected_terminal is not None
        else None
    )
    event_reconstructable = (
        bool(task_id)
        and bool(mission_id)
        and same_task_resume
        and required_authorization_events.issubset(robot_event_types)
        and expected_task_event in robot_event_types
        and (
            "mission.report_ready" in mission_event_types
            or bool(final_report)
        )
        and all(
            item.get("task_id") in {None, task_id}
            for item in robot_events
        )
    )
    scheduler_evidence_present = bool(dispatch_records) and any(
        item.get("mission_id") == mission_id for item in dispatch_records
    )

    expected_plugin = source_scenario.get("expected", {})
    mission_spec = source_scenario.get("mission", {})
    plugin_checks = {
        "owner": plugin.get("owner_plugin_id")
        == expected_plugin.get("plugin_owner"),
        "tool": plugin.get("contribution_id")
        == mission_spec.get("required_tool"),
        "backend": plugin.get("backend_class")
        == expected_plugin.get("backend_class"),
        "action": plugin.get("action_name")
        == expected_plugin.get("action_name"),
    }
    plugin_contract_met = all(plugin_checks.values())
    adapter_fallback_free = manifest.get("adapter_trap_call_count") == 0
    readiness_met = (
        readiness.get("status") == "ready"
        and readiness.get("action_server")
        == expected_plugin.get("action_name")
    )
    stopped = (
        isinstance(pose.get("stopped"), dict)
        and pose["stopped"].get("status") == "stopped"
    )
    frame_contract = (
        scenario.get("target", {}).get("frame_id") == "map"
        and isinstance(scenario.get("target", {}).get("pose"), dict)
    )

    scenario_checks, scenario_metrics = _scenario_specific_checks(
        scenario_type,
        source_scenario,
        documents=documents,
        feedback_count=len(feedback_records),
        goal_count=len(goal_records),
        actionlib_statuses=actionlib_statuses,
        observed_terminal=observed_terminal,
    )
    plugin_inventory_complete = _plugin_inventory_complete(plugin)
    tool_inventory_complete = _tool_inventory_complete(plugin, mission_spec)
    version_evidence_complete = _version_evidence_complete(
        documents["system-versions.json"]
    )
    navigation_parameters_recorded = any(
        bool(documents[name])
        for name in (
            "navigation-parameters.json",
            "navigation-parameters-before.json",
        )
    )
    execution_metadata_explicit = (
        isinstance(manifest.get("execution"), dict)
        and manifest["execution"].get("use_scheduler") is True
        and manifest["execution"].get("background") is True
    )
    provenance_checks = {
        "source_inventory": (
            bool(source_inventory.get("declared_artifacts"))
            and not source_inventory.get("declared_missing")
        ),
        "asset_integrity": asset_inventory.get("complete") is True,
        "plugin_inventory": plugin_inventory_complete,
        "tool_inventory": tool_inventory_complete,
        "system_versions": version_evidence_complete,
        "navigation_parameters": navigation_parameters_recorded,
        "fault_metadata": bool(scenario.get("fault_injection")),
        "execution_metadata": execution_metadata_explicit,
        "mission_run_snapshot": bool(mission_run),
    }
    provenance_complete = all(provenance_checks.values())
    collision = documents["collision-evidence.json"]
    collision_validation = _validate_collision_evidence(
        collision,
        collision_stream=collision_stream,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        expected_robot=source_scenario.get("robot", {}),
    )
    collision_metric_available = collision_validation["available"]
    collision_count = (
        collision_validation["collision_count"]
        if collision_metric_available
        else None
    )
    source_repository = manifest.get("repository", {})
    paper_evidence_complete = (
        provenance_complete
        and collision_metric_available
        and split in {"validation", "test"}
        and isinstance(source_repository, dict)
        and source_repository.get("dirty") is False
    )

    common_checks = {
        "source_acceptance_passed": manifest.get("status") == "passed",
        "canonical_expected_terminal": expected_terminal is not None,
        "canonical_terminal_match": terminal_match,
        "final_report": bool(final_report),
        "scheduler_evidence": scheduler_evidence_present,
        "same_task_resume": same_task_resume,
        "plugin_contract": plugin_contract_met,
        "adapter_fallback_not_used": adapter_fallback_free,
        "readiness": readiness_met,
        "event_reconstructable": event_reconstructable,
        "asset_integrity": asset_inventory.get("complete") is True,
        "point_map_target": frame_contract,
        "robot_stopped": stopped,
        "required_documents": not missing_documents,
    }
    contract_passed = all(common_checks.values()) and all(
        scenario_checks.values()
    )
    safe_stop_applicable = scenario_type in {
        "cancel",
        "timeout",
        "stall_recover",
        "stall_escalate",
    }
    diagnostics_applicable = scenario_type in {
        "stall_recover",
        "stall_escalate",
    }
    recovery_applicable = scenario_type == "stall_recover"
    escalation_applicable = scenario_type == "stall_escalate"
    system_latency_ms = _manifest_latency_ms(manifest)
    goal_position_error = _first_number(
        pose,
        "position_error_m",
        "goal_position_error_m",
    )
    metric_values = {
        "contract_passed": contract_passed,
        "task_success": (
            observed_terminal == "completed"
            and terminal_sources["robot_task"] == "completed"
            and terminal_sources["final_report"] == "completed"
        ),
        "terminal_match": terminal_match,
        "final_report_present": bool(final_report),
        "scheduler_evidence_present": scheduler_evidence_present,
        "same_task_resume": same_task_resume,
        "plugin_contract_met": plugin_contract_met,
        "adapter_fallback_free": adapter_fallback_free,
        "event_reconstructable": event_reconstructable,
        "asset_integrity": asset_inventory.get("complete") is True,
        "provenance_complete": provenance_complete,
        "paper_evidence_complete": paper_evidence_complete,
        "safe_stop_applicable": safe_stop_applicable,
        "safe_stop_met": scenario_metrics["safe_stop_met"],
        "diagnostics_applicable": diagnostics_applicable,
        "diagnostics_evidence_met": scenario_metrics[
            "diagnostics_evidence_met"
        ],
        "recovery_applicable": recovery_applicable,
        "recovery_success": scenario_metrics["recovery_success"],
        "escalation_applicable": escalation_applicable,
        "escalation_correct": scenario_metrics["escalation_correct"],
        "collision_metric_available": collision_metric_available,
        "collision_free": (
            collision_count == 0 if collision_metric_available else None
        ),
        "collision_count": (
            collision_count
        ),
        "system_latency_ms": system_latency_ms,
        "feedback_count": (
            len(feedback_records) if goal_stream else None
        ),
        "goal_position_error_m": goal_position_error,
        "safe_stop_latency_ms": scenario_metrics["safe_stop_latency_ms"],
        "source_proof_bytes": source_inventory.get("total_bytes"),
        "recovery_attempt_count": scenario_metrics[
            "recovery_attempt_count"
        ],
    }
    missing_data = sorted(set([
        *missing_documents,
        *(
            [] if plugin_inventory_complete else ["full_plugin_inventory"]
        ),
        *([] if tool_inventory_complete else ["full_tool_inventory"]),
        *([] if version_evidence_complete else ["exact_ros_gazebo_versions"]),
        *(
            []
            if navigation_parameters_recorded
            else ["navigation_parameters"]
        ),
        *([] if mission_run else ["mission_run_snapshot"]),
        *(
            []
            if execution_metadata_explicit
            else ["explicit_background_scheduler_metadata"]
        ),
        *(
            [] if collision_metric_available else ["collision_evidence"]
        ),
    ]))
    score = {
        "contract_passed": contract_passed,
        "checks": common_checks,
        "scenario_checks": scenario_checks,
        "plugin_checks": plugin_checks,
        "provenance_checks": provenance_checks,
        "metric_values": metric_values,
        "expected": {
            "terminal_outcome": expected_terminal,
            "plugin_owner": expected_plugin.get("plugin_owner"),
            "tool": mission_spec.get("required_tool"),
            "backend": expected_plugin.get("backend_class"),
            "action_name": expected_plugin.get("action_name"),
        },
        "observed": {
            "terminal_outcome": observed_terminal,
            "terminal_sources": terminal_sources,
            "task_ids": task_ids,
            "plugin": {
                "owner": plugin.get("owner_plugin_id"),
                "tool": plugin.get("contribution_id"),
                "backend": plugin.get("backend_class"),
                "action_name": plugin.get("action_name"),
            },
            "actionlib_statuses": sorted(actionlib_statuses),
        },
        "missing_data": missing_data,
    }
    evidence_summary = {
        "task_id": task_id,
        "mission_id": mission_id,
        "task_id_sources": task_ids,
        "terminal_sources": terminal_sources,
        "goal_count": len(goal_records),
        "feedback_count": len(feedback_records),
        "actionlib_statuses": sorted(actionlib_statuses),
        "robot_event_count": len(robot_events),
        "authorization_event_count": len(authorization_events),
        "mission_event_count": len(mission_events),
        "robot_event_types": sorted(robot_event_types),
        "mission_event_types": sorted(mission_event_types),
        "scheduler_dispatch_record_count": len(dispatch_records),
        "compatibility_trace_exact": (
            terminal_sources["compatibility_mission_trace"]
            == expected_terminal
        ),
        "collision_metric_available": collision_metric_available,
        "collision_count": (
            collision_count
        ),
        "collision_validation_checks": collision_validation["checks"],
    }
    return score, evidence_summary


def inventory_source_proof(
    source_dir: Path,
    *,
    declared_artifacts: Any,
    exclude_paths: tuple[Path, ...] = (),
    max_source_bytes: int = 512 * 1024 * 1024,
) -> tuple[dict[str, Any], tuple[tuple[str, Path], ...]]:
    files: list[dict[str, Any]] = []
    copy_sources: list[tuple[str, Path]] = []
    total_bytes = 0
    for path in sorted(source_dir.rglob("*")):
        resolved = path.resolve(strict=False)
        if any(_is_within(resolved, excluded) for excluded in exclude_paths):
            continue
        if path.is_symlink():
            if not _is_within(resolved, source_dir):
                raise ValueError(
                    f"source proof symlink escapes its root: {path}"
                )
            relative = path.relative_to(source_dir).as_posix()
            link_target = path.readlink().as_posix()
            files.append({
                "path": relative,
                "bytes": 0,
                "sha256": canonical_json_sha256({
                    "kind": "symlink",
                    "target": link_target,
                }),
                "media_type": "inode/symlink",
                "kind": "symlink",
                "target": link_target,
                "embedded_as": "source-artifact-inventory.json",
            })
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir).as_posix()
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > max_source_bytes:
            raise ValueError(
                "source proof exceeds max_source_bytes: "
                f"{total_bytes} > {max_source_bytes}"
            )
        files.append({
            "path": relative,
            "bytes": size,
            "sha256": sha256_file(path),
            "media_type": (
                mimetypes.guess_type(path.name)[0]
                or "application/octet-stream"
            ),
        })
        copy_sources.append((relative, path))
    actual = {item["path"] for item in files}
    declared = (
        {
            str(item)
            for item in declared_artifacts
            if isinstance(item, str) and item
        }
        if isinstance(declared_artifacts, list)
        else set()
    )
    stable = {
        "files": files,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "declared_artifacts": sorted(declared),
        "declared_missing": sorted(declared - actual),
        "undeclared_files": sorted(actual - declared),
    }
    stable["inventory_sha256"] = canonical_json_sha256(stable)
    return stable, tuple(copy_sources)


def validate_source_assets(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[tuple[str, Path], ...]]:
    raw = manifest.get("asset_hashes")
    entries: dict[str, Any] = {}
    copy_sources: list[tuple[str, Path]] = []
    if not isinstance(raw, dict):
        raw = {}
    labels = _BASE_ASSET_LABELS | {
        label for label in _OPTIONAL_ASSET_LABELS if label in raw
    }
    for label in sorted(labels):
        item = raw.get(label)
        path: Path | None = None
        expected_hash: str | None = None
        if isinstance(item, dict):
            if isinstance(item.get("path"), str):
                path = Path(item["path"]).resolve(strict=False)
            if isinstance(item.get("sha256"), str):
                expected_hash = item["sha256"]
        actual_hash = (
            sha256_file(path)
            if path is not None and path.is_file() and not path.is_symlink()
            else None
        )
        matched = (
            expected_hash is not None
            and len(expected_hash) == 64
            and actual_hash == expected_hash
        )
        entries[label] = {
            "source_path": str(path) if path is not None else None,
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
            "matched": matched,
        }
        if matched and path is not None:
            copy_sources.append((f"{label}-{path.name}", path))
    stable = {
        "assets": entries,
        "complete": (
            _BASE_ASSET_LABELS.issubset(entries)
            and all(item["matched"] for item in entries.values())
        ),
    }
    stable["inventory_sha256"] = canonical_json_sha256(stable)
    return stable, tuple(copy_sources)


def _validate_collision_evidence(
    evidence: Mapping[str, Any],
    *,
    collision_stream: list[dict[str, Any]],
    source_inventory: Mapping[str, Any],
    asset_inventory: Mapping[str, Any],
    expected_robot: Mapping[str, Any],
) -> dict[str, Any]:
    instrumentation = evidence.get("instrumentation")
    if not isinstance(instrumentation, dict):
        instrumentation = {}
    observation_window = evidence.get("observation_window")
    if not isinstance(observation_window, dict):
        observation_window = {}
    raw_stream = evidence.get("raw_stream")
    if not isinstance(raw_stream, dict):
        raw_stream = {}
    library = instrumentation.get("library")
    if not isinstance(library, dict):
        library = {}
    topics = instrumentation.get("topics")
    if not isinstance(topics, list):
        topics = []
    episodes = evidence.get("collision_episodes")
    if not isinstance(episodes, list):
        episodes = []
    robot = evidence.get("robot")
    if not isinstance(robot, dict):
        robot = {}
    filter_policy = evidence.get("filter_policy")
    if not isinstance(filter_policy, dict):
        filter_policy = {}
    collision_count = evidence.get("collision_count")
    collision_count_valid = (
        isinstance(collision_count, int)
        and not isinstance(collision_count, bool)
        and collision_count >= 0
    )
    expected_topics = {_COLLISION_TOPIC}
    observed_topics = {
        str(item.get("topic"))
        for item in topics
        if isinstance(item, dict)
    }
    topic_counters_valid = (
        observed_topics == expected_topics
        and all(
            isinstance(item, dict)
            and isinstance(item.get("message_count"), int)
            and not isinstance(item.get("message_count"), bool)
            and item["message_count"] > 0
            and isinstance(item.get("contact_state_count"), int)
            and not isinstance(item.get("contact_state_count"), bool)
            and item["contact_state_count"] >= 0
            and item.get("produced_messages") is True
            and isinstance(
                item.get("publisher_connections_at_finalize"),
                int,
            )
            and item["publisher_connections_at_finalize"] > 0
            for item in topics
        )
    )
    topic_contact_state_total = (
        sum(int(item["contact_state_count"]) for item in topics)
        if topic_counters_valid
        else None
    )
    expected_model = str(expected_robot.get("model") or "")
    expected_model_name = f"turtlebot3_{expected_model}"
    stream_classifications_valid = all(
        _collision_stream_record_valid(
            item,
            robot_model_name=expected_model_name,
        )
        for item in collision_stream
    )
    prohibited_states = sum(
        item.get("classification") == "prohibited_collision"
        for item in collision_stream
    )
    allowed_states = sum(
        item.get("classification") == "allowed_support_contact"
        for item in collision_stream
    )
    episode_ids = {
        str(item.get("episode_id"))
        for item in episodes
        if isinstance(item, dict) and item.get("episode_id")
    }
    episode_records: dict[str, list[dict[str, Any]]] = {
        episode_id: [] for episode_id in episode_ids
    }
    for item in collision_stream:
        if item.get("classification") != "prohibited_collision":
            continue
        episode_id = str(item.get("episode_id") or "")
        if episode_id in episode_records:
            episode_records[episode_id].append(item)
    episode_links_valid = (
        len(episode_ids) == len(episodes)
        and all(
            (
                item.get("classification") == "prohibited_collision"
                and str(item.get("episode_id")) in episode_ids
            )
            or (
                item.get("classification") == "allowed_support_contact"
                and item.get("episode_id") is None
            )
            for item in collision_stream
        )
        and all(
            isinstance(item, dict)
            and _collision_episode_valid(
                item,
                records=episode_records.get(
                    str(item.get("episode_id")),
                    [],
                ),
            )
            for item in episodes
        )
    )
    inventory_paths = {
        str(item.get("path"))
        for item in source_inventory.get("files", [])
        if isinstance(item, dict)
    }
    inventory_files = {
        str(item.get("path")): item
        for item in source_inventory.get("files", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    embedded_library = library.get("embedded_artifact")
    embedded_library_entry = (
        inventory_files.get(embedded_library)
        if isinstance(embedded_library, str)
        else None
    )
    assets = asset_inventory.get("assets", {})
    robot_description_asset = assets.get("robot_description", {})
    collision_monitor_asset = assets.get("collision_monitor", {})
    checks = {
        "schema": (
            evidence.get("schema_version")
            == GAZEBO_COLLISION_EVIDENCE_SCHEMA_VERSION
        ),
        "captured": evidence.get("status") == "captured",
        "instrumentation_complete": (
            instrumentation.get("status") == "complete"
            and instrumentation.get("source")
            == "gazebo.physics.ContactManager"
            and instrumentation.get("sensor_plugin")
            == "libfireclaw_gazebo_contact_monitor.so"
            and instrumentation.get("message_type")
            == "gazebo_msgs/ContactsState"
        ),
        "topics_complete": topic_counters_valid,
        "robot_scope": (
            bool(expected_model)
            and robot.get("id") == expected_robot.get("id")
            and robot.get("model") == expected_model
            and robot.get("gazebo_model_name") == expected_model_name
        ),
        "filter_policy": (
            filter_policy.get("policy_id") == _COLLISION_POLICY_ID
            and filter_policy.get("allowed_support_links")
            == sorted(_COLLISION_SUPPORT_LINKS)
            and filter_policy.get("allowed_other_model") == "ground_plane"
            and isinstance(
                filter_policy.get("episode_gap_seconds"),
                (int, float),
            )
            and not isinstance(filter_policy.get("episode_gap_seconds"), bool)
            and filter_policy["episode_gap_seconds"]
            == _COLLISION_EPISODE_GAP_SECONDS
        ),
        "monitor_binary": (
            embedded_library == "collision-monitor-plugin.so"
            and isinstance(library.get("sha256"), str)
            and len(library["sha256"]) == 64
            and isinstance(embedded_library_entry, dict)
            and embedded_library_entry.get("sha256")
            == library.get("sha256")
        ),
        "window_complete": (
            observation_window.get("started_before_first_goal") is True
            and observation_window.get("ended_after_terminal_stop") is True
        ),
        "stream_declared": (
            raw_stream.get("artifact")
            == "collision-contact-stream.jsonl"
            and "collision-contact-stream.jsonl" in inventory_paths
        ),
        "stream_complete": (
            raw_stream.get("truncated") is False
            and raw_stream.get("record_count") == len(collision_stream)
            and evidence.get("contact_state_count")
            == len(collision_stream)
            and topic_contact_state_total == len(collision_stream)
        ),
        "stream_classifications": stream_classifications_valid,
        "classification_totals": (
            evidence.get("prohibited_contact_state_count")
            == prohibited_states
            and evidence.get("allowed_support_contact_state_count")
            == allowed_states
        ),
        "episodes_consistent": (
            collision_count_valid
            and collision_count == len(episodes)
            and episode_links_valid
            and evidence.get("collision_free") is (collision_count == 0)
        ),
        "robot_description_asset": (
            isinstance(robot_description_asset, dict)
            and robot_description_asset.get("matched") is True
        ),
        "collision_monitor_asset": (
            isinstance(collision_monitor_asset, dict)
            and collision_monitor_asset.get("matched") is True
        ),
    }
    return {
        "available": all(checks.values()),
        "collision_count": collision_count if collision_count_valid else None,
        "checks": checks,
    }


def validate_collision_evidence(
    evidence: Mapping[str, Any],
    *,
    collision_stream: list[dict[str, Any]],
    source_inventory: Mapping[str, Any],
    asset_inventory: Mapping[str, Any],
    expected_robot: Mapping[str, Any],
) -> dict[str, Any]:
    """Public offline verifier for raw ContactManager evidence."""

    return _validate_collision_evidence(
        evidence,
        collision_stream=collision_stream,
        source_inventory=source_inventory,
        asset_inventory=asset_inventory,
        expected_robot=expected_robot,
    )


def _collision_stream_record_valid(
    record: Mapping[str, Any],
    *,
    robot_model_name: str,
) -> bool:
    collision1 = record.get("collision1_name")
    collision2 = record.get("collision2_name")
    if not isinstance(collision1, str) or not collision1:
        return False
    if not isinstance(collision2, str) or not collision2:
        return False
    classification, reason, link_name = _classify_collision_pair(
        robot_model_name,
        collision1,
        collision2,
    )
    return (
        record.get("sensor_topic") == _COLLISION_TOPIC
        and record.get("classification") == classification
        and record.get("classification_reason") == reason
        and record.get("sensor_link") == link_name
    )


def _collision_episode_valid(
    episode: Mapping[str, Any],
    *,
    records: list[dict[str, Any]],
) -> bool:
    pair = episode.get("collision_pair")
    sample_count = episode.get("sample_count")
    sensor_topics = episode.get("sensor_topics")
    if (
        not isinstance(pair, list)
        or len(pair) != 2
        or not all(isinstance(item, str) and item for item in pair)
        or not isinstance(sample_count, int)
        or isinstance(sample_count, bool)
        or sample_count <= 0
        or sensor_topics != [_COLLISION_TOPIC]
        or sample_count != len(records)
    ):
        return False
    expected_pair = sorted(pair)
    return all(
        sorted([
            str(record.get("collision1_name") or ""),
            str(record.get("collision2_name") or ""),
        ])
        == expected_pair
        for record in records
    )


def _classify_collision_pair(
    robot_model_name: str,
    collision1: str,
    collision2: str,
) -> tuple[str, str, str]:
    model_prefix = f"{robot_model_name}::"
    first_matches = collision1.startswith(model_prefix)
    second_matches = collision2.startswith(model_prefix)
    if first_matches and not second_matches:
        robot_collision = collision1
        other = collision2
    elif second_matches and not first_matches:
        robot_collision = collision2
        other = collision1
    elif first_matches and second_matches:
        return "prohibited_collision", "robot_self_contact", (
            _collision_link_name(collision1)
        )
    else:
        return "prohibited_collision", "unexpected_sensor_contact_pair", "unknown"
    link_name = _collision_link_name(robot_collision)
    other_model = other.split("::", 1)[0]
    if link_name in _COLLISION_SUPPORT_LINKS and other_model == "ground_plane":
        return "allowed_support_contact", "support_link_on_ground_plane", link_name
    return (
        "prohibited_collision",
        "robot_contact_with_non_support_surface",
        link_name,
    )


def _collision_link_name(collision_name: str) -> str:
    for link_name in _COLLISION_ROBOT_LINKS:
        if f"{link_name}_collision" in collision_name:
            return link_name
    return "unknown"


def _normalize_scenario(
    scenario: Mapping[str, Any],
    *,
    source_run_id: str,
    split: str,
    repeat_index: int,
) -> dict[str, Any]:
    goal = scenario.get("goal")
    if not isinstance(goal, dict):
        goal = {}
    target = {
        "frame_id": goal.get("frame_id"),
        "pose": {
            "x": goal.get("x"),
            "y": goal.get("y"),
            "yaw": goal.get("yaw", 0.0),
        },
    }
    scenario_type = str(scenario.get("scenario_type"))
    return {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "protocol_version": ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION,
        "suite_id": "fireclaw-gazebo-navigation-acceptance",
        "suite_version": "1.0.0",
        "scenario_id": scenario.get("scenario_id"),
        "scenario_version": str(scenario.get("scenario_version") or "1.0.0"),
        "lane": "ros_gazebo_system",
        "split": split,
        "seed": scenario.get("seed", 0),
        "repeat_index": repeat_index,
        "source_run_id": source_run_id,
        "scenario_type": scenario_type,
        "command": scenario.get("mission", {}).get("command"),
        "target_type": "point",
        "target": target,
        "expected_capability": scenario.get("mission", {}).get("capability"),
        "expected_tool": scenario.get("mission", {}).get("required_tool"),
        "expected_terminal_outcome": scenario.get("expected", {}).get(
            "terminal_status"
        ),
        "robot": scenario.get("robot"),
        "assertions": scenario.get("assertions"),
        "timeouts": scenario.get("timeouts"),
        "fault_injection": _fault_metadata(scenario_type, scenario),
    }


def _scenario_specific_checks(
    scenario_type: str,
    scenario: Mapping[str, Any],
    *,
    documents: Mapping[str, dict[str, Any]],
    feedback_count: int,
    goal_count: int,
    actionlib_statuses: set[int],
    observed_terminal: str | None,
) -> tuple[dict[str, bool], dict[str, Any]]:
    checks: dict[str, bool] = {}
    safe_stop_met = False
    diagnostics_met = False
    recovery_success = False
    escalation_correct = False
    safe_stop_latency_ms: float | None = None
    recovery_attempt_count = 0
    pose = documents["pose-evidence.json"]
    assertions = scenario.get("assertions", {})
    expected = scenario.get("expected", {})
    expected_actionlib = {
        int(item)
        for item in expected.get("actionlib_terminal_statuses", [])
        if isinstance(item, int) and not isinstance(item, bool)
    }
    checks["feedback"] = (
        feedback_count > 0
        if assertions.get("require_feedback") is True
        else True
    )
    checks["actionlib_terminal"] = bool(
        expected_actionlib.intersection(actionlib_statuses)
    )

    if scenario_type == "success":
        position_error = _first_number(pose, "position_error_m")
        yaw_error = _first_number(pose, "yaw_error_rad")
        displacement = _first_number(pose, "actual_displacement_m")
        checks.update({
            "completed": observed_terminal == "completed",
            "position_tolerance": _at_most(
                position_error,
                assertions.get("position_tolerance_m"),
            ),
            "yaw_tolerance": _at_most(
                yaw_error,
                assertions.get("yaw_tolerance_rad"),
            ),
            "minimum_displacement": _at_least(
                displacement,
                assertions.get("minimum_displacement_m"),
            ),
        })
    elif scenario_type in {"cancel", "timeout"}:
        key = (
            "cancellation-evidence.json"
            if scenario_type == "cancel"
            else "timeout-evidence.json"
        )
        expectation_key = "cancellation" if scenario_type == "cancel" else "timeout"
        evidence = documents[key]
        expectation = scenario.get(expectation_key, {})
        output = evidence.get("action_terminal_output", {})
        safe_stop_met = (
            output.get("cancellation_acknowledged") is True
            and output.get("runtime_stopped") is True
            and output.get("resource_release_safe") is True
        )
        stop_latency = _first_number(evidence, "stop_latency_seconds")
        safe_stop_latency_ms = (
            stop_latency * 1000.0 if stop_latency is not None else None
        )
        trigger = evidence.get("trigger", {})
        displacement_key = (
            "post_cancel_displacement_m"
            if scenario_type == "cancel"
            else "post_timeout_displacement_m"
        )
        maximum_key = (
            "maximum_post_cancel_displacement_m"
            if scenario_type == "cancel"
            else "maximum_post_timeout_displacement_m"
        )
        checks.update({
            "canonical_outcome": observed_terminal
            == ("cancelled" if scenario_type == "cancel" else "timed_out"),
            "safe_stop": safe_stop_met,
            "minimum_progress": _at_least(
                _first_number(trigger, "displacement_m"),
                expectation.get("minimum_displacement_m"),
            ),
            "bounded_post_stop_motion": _at_most(
                _first_number(evidence, displacement_key),
                expectation.get(maximum_key),
            ),
        })
        if scenario_type == "timeout":
            checks["deadline_reason"] = (
                output.get("cancellation_reason") == "deadline_exceeded"
            )
        else:
            checks["operator_cancel_reason"] = (
                output.get("cancellation_reason") == "operator_cancelled"
            )
    elif scenario_type in {"stall_recover", "stall_escalate"}:
        stall = scenario.get("stall", {})
        evidence = documents["stall-evidence.json"]
        diagnostic = documents["navigation-diagnostics.json"]
        first_terminal = evidence.get("action_terminals", [])
        first_output = (
            first_terminal[0].get("payload", {}).get("output", {})
            if isinstance(first_terminal, list)
            and first_terminal
            and isinstance(first_terminal[0], dict)
            else {}
        )
        safe_stop_met = (
            first_output.get("cancellation_acknowledged") is True
            and first_output.get("runtime_stopped") is True
            and first_output.get("resource_release_safe") is True
        )
        finding_codes = {
            str(item)
            for item in evidence.get("diagnostic_finding_codes", [])
            if isinstance(item, str)
        }
        required_codes = {
            str(item)
            for item in stall.get("required_diagnostic_finding_codes", [])
            if isinstance(item, str)
        }
        evidence_id = evidence.get("diagnostic_evidence_id")
        diagnostics_met = (
            isinstance(evidence_id, str)
            and bool(evidence_id)
            and required_codes.issubset(finding_codes)
            and diagnostic.get("evidence_id") == evidence_id
        )
        attempts = evidence.get("recovery_attempt_count")
        recovery_attempt_count = (
            attempts
            if isinstance(attempts, int) and not isinstance(attempts, bool)
            else 0
        )
        first_feedback = evidence.get("first_goal_feedback_count")
        checks.update({
            "safe_stop": safe_stop_met,
            "diagnostics_first": diagnostics_met,
            "stall_displacement": _at_most(
                _first_number(evidence, "first_attempt_max_displacement_m"),
                stall.get("maximum_stall_displacement_m"),
            ),
            "stall_feedback": _at_least(
                float(first_feedback)
                if isinstance(first_feedback, int)
                and not isinstance(first_feedback, bool)
                else None,
                stall.get("minimum_feedback_count"),
            ),
        })
        if scenario_type == "stall_recover":
            recovery_success = (
                observed_terminal == "completed"
                and recovery_attempt_count
                == stall.get("maximum_recovery_attempts")
                and goal_count == 2
                and 3 in actionlib_statuses
            )
            checks.update({
                "one_bounded_recovery": recovery_success,
                "parameters_recovered": (
                    evidence.get("navigation_parameters_after", {}).get("dwa", {}).get(
                        "max_vel_x"
                    )
                    == stall.get("recovery_parameters", {}).get("max_vel_x")
                ),
            })
        else:
            reason = stall.get("escalation_reason_code")
            escalation_correct = (
                observed_terminal == "escalated"
                and recovery_attempt_count == 0
                and goal_count == 1
                and _contains_scalar(
                    (
                        documents["robot-task-trace.json"],
                        documents["mission-trace.json"],
                        documents["final-report.json"],
                    ),
                    reason,
                )
                and _contains_scalar(
                    (
                        documents["robot-task-trace.json"],
                        documents["mission-trace.json"],
                        documents["final-report.json"],
                    ),
                    evidence_id,
                )
            )
            checks.update({
                "evidence_backed_escalation": escalation_correct,
                "no_mutation": _without_capture_time(
                    evidence.get("navigation_parameters_before")
                )
                == _without_capture_time(
                    evidence.get("navigation_parameters_after")
                ),
            })
    elif scenario_type == "abort":
        abort = scenario.get("abort", {})
        evidence = documents["abort-evidence.json"]
        output = evidence.get("action_terminal_output", {})
        checks.update({
            "canonical_failure": observed_terminal == "failed",
            "move_base_aborted": (
                output.get("error_code") == "move_base_aborted"
                and output.get("goal_state") == 4
                and output.get("runtime_stopped") is True
            ),
            "bounded_displacement": _at_most(
                _first_number(evidence, "robot_displacement_m"),
                abort.get("maximum_displacement_m"),
            ),
            "goal_outside_map": evidence.get("map", {}).get(
                "goal_inside_axis_aligned_bounds"
            )
            is False,
        })

    return checks, {
        "safe_stop_met": safe_stop_met,
        "safe_stop_latency_ms": safe_stop_latency_ms,
        "diagnostics_evidence_met": diagnostics_met,
        "recovery_success": recovery_success,
        "escalation_correct": escalation_correct,
        "recovery_attempt_count": recovery_attempt_count,
    }


def _fault_metadata(
    scenario_type: str,
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    if scenario_type == "success":
        return {"kind": "none", "injected": False}
    if scenario_type == "cancel":
        return {"kind": "operator_cancel", "injected": False}
    if scenario_type == "timeout":
        return {
            "kind": "execution_deadline",
            "injected": False,
            "configuration": scenario.get("timeout"),
        }
    if scenario_type in {"stall_recover", "stall_escalate"}:
        return {
            "kind": "dwa_velocity_stall",
            "injected": True,
            "configuration": scenario.get("stall"),
        }
    return {
        "kind": "out_of_map_goal",
        "injected": False,
        "configuration": scenario.get("abort"),
    }


def _plugin_inventory_complete(plugin: Mapping[str, Any]) -> bool:
    inventory = plugin.get("reproducibility_plugin_inventory")
    if not isinstance(inventory, dict):
        return False
    digest = inventory.get("inventory_sha256")
    plugins = inventory.get("plugins")
    return (
        isinstance(digest, str)
        and len(digest) == 64
        and isinstance(plugins, list)
        and bool(plugins)
        and all(
            isinstance(item, dict)
            and isinstance(item.get("version"), str)
            and isinstance(item.get("source_files"), dict)
            and bool(item["source_files"])
            and all(
                isinstance(source, dict)
                and isinstance(source.get("path"), str)
                and isinstance(source.get("sha256"), str)
                and len(source["sha256"]) == 64
                for source in item["source_files"].values()
            )
            for item in plugins
        )
    )


def _tool_inventory_complete(
    plugin: Mapping[str, Any],
    mission: Mapping[str, Any],
) -> bool:
    inventory = plugin.get("tool_inventory")
    if not isinstance(inventory, dict):
        return False
    required_tool = mission.get("required_tool")
    physical = inventory.get("physical_tools")
    digest = inventory.get("inventory_sha256")
    return (
        isinstance(digest, str)
        and len(digest) == 64
        and isinstance(physical, list)
        and any(
            isinstance(item, dict)
            and (
                item.get("name") == required_tool
                or item.get("skill") == required_tool
                or item.get("tool_name") == required_tool
            )
            and isinstance(item.get("input_schema"), dict)
            for item in physical
        )
    )


def _version_evidence_complete(value: Mapping[str, Any]) -> bool:
    commands = value.get("commands")
    if not isinstance(commands, dict):
        return False
    ros_versions_complete = all(
        isinstance(commands.get(name), dict)
        and commands[name].get("available") is True
        and isinstance(commands[name].get("stdout"), str)
        and bool(commands[name]["stdout"])
        for name in (
            "ros_distro",
            "ros_core",
            "move_base",
            "gazebo_ros",
        )
    )
    gazebo_version_complete = any(
        isinstance(commands.get(name), dict)
        and commands[name].get("available") is True
        and isinstance(commands[name].get("stdout"), str)
        and bool(commands[name]["stdout"])
        for name in ("gazebo", "gazebo_library")
    )
    return ros_versions_complete and gazebo_version_complete


def version_evidence_complete(value: Mapping[str, Any]) -> bool:
    """Return whether exact ROS, move_base, and Gazebo versions are present."""

    return _version_evidence_complete(value)


def _task_identity_values(
    manifest: Mapping[str, Any],
    robot_trace: Mapping[str, Any],
    authorization_trace: Mapping[str, Any],
    mission_trace: Mapping[str, Any],
) -> dict[str, str]:
    candidates: dict[str, Any] = {
        "manifest.task_id": manifest.get("task_id"),
        "manifest.authorization_task_id": manifest.get(
            "authorization_task_id"
        ),
        "robot_task_trace.task_id": robot_trace.get("task_id"),
        "authorization_task_trace.task_id": authorization_trace.get("task_id"),
    }
    subtasks = mission_trace.get("subtasks")
    if isinstance(subtasks, list) and len(subtasks) == 1:
        candidates["mission_trace.subtasks[0].task_id"] = subtasks[0].get(
            "task_id"
        )
    return {
        key: value
        for key, value in candidates.items()
        if isinstance(value, str) and value
    }


def _manifest_latency_ms(manifest: Mapping[str, Any]) -> float | None:
    started = _timestamp(manifest.get("started_at"))
    completed = _timestamp(manifest.get("completed_at"))
    if started is None or completed is None:
        return None
    elapsed = (completed - started).total_seconds() * 1000.0
    return round(elapsed, 6) if elapsed >= 0.0 else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _read_required_json(path: Path) -> dict[str, Any]:
    value = _read_optional_json(path)
    if value is None:
        raise ValueError(f"required proof document is missing: {path.name}")
    return value


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"proof document must be an object: {path.name}")
    return value


def _read_optional_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.is_file() or path.is_symlink():
        return None
    values: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"proof JSONL row must be an object: {path.name}:{line_number}"
                )
            values.append(value)
    return values


def _case_id(
    scenario_id: str,
    *,
    seed: int,
    repeat_index: int,
    source_run_id: str,
) -> str:
    value = (
        f"{scenario_id}__seed-{seed}__repeat-{repeat_index:03d}__"
        f"source-{source_run_id}"
    )
    if len(value) > 128:
        value = (
            f"{scenario_id[:76]}__seed-{seed}__repeat-{repeat_index:03d}__"
            f"source-{canonical_json_sha256(source_run_id)[:12]}"
        )
    return _safe_id(value, "case_id")


def _safe_id(value: str, name: str) -> str:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{name} contains unsafe characters")
    return value


def _required_string(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return item.strip()


def _first_number(value: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        item = value.get(name)
        if (
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and isfinite(float(item))
            and float(item) >= 0.0
        ):
            return float(item)
    return None


def _at_most(actual: float | None, maximum: Any) -> bool:
    return (
        actual is not None
        and isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and isfinite(float(maximum))
        and actual <= float(maximum)
    )


def _at_least(actual: float | None, minimum: Any) -> bool:
    return (
        actual is not None
        and isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isfinite(float(minimum))
        and actual >= float(minimum)
    )


def _contains_scalar(values: Any, expected: Any) -> bool:
    if expected is None:
        return False
    if values == expected:
        return True
    if isinstance(values, Mapping):
        return any(_contains_scalar(item, expected) for item in values.values())
    if isinstance(values, (list, tuple)):
        return any(_contains_scalar(item, expected) for item in values)
    return False


def _without_capture_time(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {
        key: _without_capture_time(item)
        for key, item in value.items()
        if key != "captured_at"
    }


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


__all__ = [
    "GAZEBO_ACCEPTANCE_PROOF_SCHEMA_VERSION",
    "GAZEBO_ACCEPTANCE_SCENARIO_SCHEMA_VERSION",
    "ROS_GAZEBO_SYSTEM_PROTOCOL_VERSION",
    "SYSTEM_SCENARIO_TYPES",
    "SystemCaseResult",
    "collect_ros_gazebo_case",
    "inventory_source_proof",
    "score_ros_gazebo_case",
    "validate_collision_evidence",
    "validate_source_assets",
    "version_evidence_complete",
]
