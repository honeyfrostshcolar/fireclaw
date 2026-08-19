"""Repeatable simulation/process fault-injection acceptance for FireClaw."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Optional, Sequence

from fireclaw_core.evaluation.artifacts import (
    EvaluationRunBundle,
    make_run_id,
    sha256_file,
)


FAULT_INJECTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FaultScenario:
    scenario_id: str
    description: str
    test_nodes: tuple[str, ...]
    assurance: str
    live_ros_test_nodes: tuple[str, ...] = ()


SCENARIOS: dict[str, FaultScenario] = {
    "network_disconnect": FaultScenario(
        "network_disconnect",
        "SSE interruption resumes from the last cursor without duplicate events.",
        (
            "tests/test_fault_injection_scenarios.py::test_network_disconnect_resumes_actual_sse_session",
        ),
        "loopback_transport",
    ),
    "gateway_crash": FaultScenario(
        "gateway_crash",
        "A crashed Gateway generation is detected, cleaned up, and restarted only in simulation.",
        (
            "tests/test_fault_injection_scenarios.py::test_supervisor_restarts_actual_crashed_gateway_process",
            "tests/test_runtime_supervisor.py::test_simulation_restarts_complete_generation_with_bound",
            "tests/test_runtime_supervisor.py::test_real_runtime_failure_never_restarts_and_persists_freeze",
        ),
        "supervisor_process_model",
    ),
    "ros_master_restart": FaultScenario(
        "ros_master_restart",
        "ROS graph loss is degraded, and an optional private roscore is killed and restarted.",
        (
            "tests/test_ros1_sensor_discovery.py::test_ros1_discovery_returns_degraded_when_graph_provider_fails",
        ),
        "deterministic_ros_boundary",
        (
            "tests/test_fault_injection_scenarios.py::test_private_roscore_restart_recovers_graph",
        ),
    ),
    "disk_full": FaultScenario(
        "disk_full",
        "SQLite full-disk admission fails closed without starting task execution.",
        (
            "tests/test_fault_injection_scenarios.py::test_runtime_storage_full_rejects_task_without_worker",
        ),
        "sqlite_real_full_condition",
    ),
    "database_lock": FaultScenario(
        "database_lock",
        "A locked authority returns a bounded unavailable result and recovers without half-commit.",
        (
            "tests/test_fault_injection_scenarios.py::test_database_lock_rejects_then_recovers_with_same_dedupe_key",
            "tests/test_fault_injection_scenarios.py::test_database_lock_http_returns_503_without_worker",
        ),
        "sqlite_real_lock",
    ),
    "sensor_failure": FaultScenario(
        "sensor_failure",
        "Invalid required lidar evidence blocks motion at the Safety Gate.",
        (
            "tests/test_safety.py::test_safety_blocks_navigation_when_lidar_health_is_invalid",
        ),
        "safety_gate",
    ),
    "duplicate_command": FaultScenario(
        "duplicate_command",
        "Concurrent submissions with one dedupe key create one task and one worker.",
        (
            "tests/test_fault_injection_scenarios.py::test_concurrent_duplicate_command_starts_one_worker",
        ),
        "gateway_concurrency",
    ),
}


CommandRunner = Callable[
    [Sequence[str], Path, Optional[Mapping[str, str]]],
    Any,
]


def run_fault_injection_suite(
    *,
    repository_root: str | Path,
    artifact_dir: str | Path,
    scenario_ids: Sequence[str] | None = None,
    live_ros: bool = False,
    command_runner: CommandRunner | None = None,
) -> dict[str, object]:
    root = Path(repository_root).resolve(strict=True)
    requested = SCENARIOS if scenario_ids is None else scenario_ids
    selected = tuple(dict.fromkeys(requested))
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"Unknown fault scenarios: {unknown}")
    if not selected:
        raise ValueError("At least one fault scenario is required")

    run_id = make_run_id("fault-injection")
    run_dir = Path(artifact_dir).resolve(strict=False) / run_id
    bundle = EvaluationRunBundle(run_dir, run_id=run_id)
    runner = command_runner or _run_command
    results: list[dict[str, object]] = []
    for scenario_id in selected:
        scenario = SCENARIOS[scenario_id]
        nodes = list(scenario.test_nodes)
        assurance = scenario.assurance
        if live_ros and scenario.live_ros_test_nodes:
            nodes.extend(scenario.live_ros_test_nodes)
            assurance = f"{assurance}+private_roscore_process"
        command = [sys.executable, "-m", "pytest", "-q", *nodes]
        started = time.monotonic()
        command_env = (
            {"FIRECLAW_RUN_LIVE_ROS_FAULT_TEST": "1"}
            if live_ros and scenario.live_ros_test_nodes
            else None
        )
        execution_error: str | None = None
        try:
            completed = runner(command, root, command_env)
            exit_code = completed.returncode
            stdout = _bounded_output(completed.stdout)
            stderr = _bounded_output(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            stdout = _bounded_output(exc.stdout)
            stderr = _bounded_output(exc.stderr)
            execution_error = "scenario_timeout"
        except OSError as exc:
            exit_code = None
            stdout = ""
            stderr = f"{type(exc).__name__}: {exc}"
            execution_error = "scenario_process_error"
        duration = time.monotonic() - started
        result = {
            "scenario_id": scenario_id,
            "description": scenario.description,
            "status": "passed" if exit_code == 0 else "failed",
            "assurance": assurance,
            "live_ros_exercised": bool(
                live_ros and scenario.live_ros_test_nodes
            ),
            "command": command,
            "exit_code": exit_code,
            "duration_seconds": round(duration, 6),
            "stdout": stdout,
            "stderr": stderr,
            "safety_invariants": [
                "fault_observed",
                "no_unsafe_execution_started",
                "no_duplicate_or_half_committed_task",
                "bounded_recovery_or_fail_closed",
                "durable_evidence_recorded",
            ],
        }
        if execution_error is not None:
            result["execution_error"] = execution_error
        bundle.write_json(f"scenarios/{scenario_id}.json", result)
        results.append(result)

    all_passed = all(item["status"] == "passed" for item in results)
    ros_selected = "ros_master_restart" in selected
    report_status = (
        "passed"
        if all_passed and (not ros_selected or live_ros)
        else "prepared"
        if all_passed
        else "failed"
    )
    report: dict[str, object] = {
        "schema_version": FAULT_INJECTION_SCHEMA_VERSION,
        "kind": "fireclaw_fault_injection_report",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": report_status,
        "scenario_count": len(results),
        "passed_count": sum(item["status"] == "passed" for item in results),
        "live_ros_requested": live_ros,
        "results": results,
        "remaining_limits": (
            []
            if not ros_selected or live_ros
            else [
                "ros_master_restart used the deterministic ROS boundary only; rerun with --live-ros for a private roscore process restart."
            ]
        ),
    }
    bundle.write_json("report.json", report)
    bundle.finalize_artifact_manifest()
    return {**report, "run_directory": str(run_dir)}


def verify_fault_injection_run(run_dir: str | Path) -> dict[str, object]:
    root = Path(run_dir).resolve(strict=True)
    manifest_path = root / "artifact-manifest.json"
    report_path = root / "report.json"
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "errors": [str(exc)]}
    files = manifest.get("files")
    if not isinstance(files, list):
        errors.append("manifest.files must be an array")
        files = []
    listed_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("manifest contains an invalid file entry")
            continue
        relative = Path(item["path"])
        normalized = relative.as_posix()
        if normalized in listed_paths:
            errors.append(f"duplicate manifest path: {normalized}")
            continue
        listed_paths.add(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe artifact path: {relative}")
            continue
        target = (root / relative).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError:
            errors.append(f"artifact escapes run directory: {relative}")
            continue
        if not target.is_file():
            errors.append(f"artifact missing: {relative}")
        elif sha256_file(target) != item.get("sha256"):
            errors.append(f"artifact digest mismatch: {relative}")
        elif target.stat().st_size != item.get("bytes"):
            errors.append(f"artifact size mismatch: {relative}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    for unexpected in sorted(actual_paths - listed_paths):
        errors.append(f"unlisted artifact: {unexpected}")
    if report.get("kind") != "fireclaw_fault_injection_report":
        errors.append("report kind is invalid")
    if report.get("schema_version") != FAULT_INJECTION_SCHEMA_VERSION:
        errors.append("report schema_version is invalid")
    if manifest.get("run_id") != report.get("run_id"):
        errors.append("manifest and report run_id differ")
    results = report.get("results")
    if not isinstance(results, list) or not results:
        errors.append("report results must be a non-empty array")
    else:
        scenario_ids: list[str] = []
        for item in results:
            scenario_id = (
                item.get("scenario_id") if isinstance(item, dict) else None
            )
            if not isinstance(scenario_id, str) or scenario_id not in SCENARIOS:
                continue
            scenario_ids.append(scenario_id)
        if len(scenario_ids) != len(results):
            errors.append("report contains an invalid scenario result")
        if len(scenario_ids) != len(set(scenario_ids)):
            errors.append("report contains duplicate scenario results")
        expected_scenario_paths = {
            f"scenarios/{scenario_id}.json" for scenario_id in scenario_ids
        }
        missing_scenarios = expected_scenario_paths - listed_paths
        for missing in sorted(missing_scenarios):
            errors.append(f"scenario artifact missing from manifest: {missing}")
        for item in results:
            if not isinstance(item, dict):
                continue
            scenario_id = item.get("scenario_id")
            if not isinstance(scenario_id, str) or scenario_id not in SCENARIOS:
                continue
            scenario_path = root / "scenarios" / f"{scenario_id}.json"
            try:
                scenario_result = json.loads(
                    scenario_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(
                    f"scenario artifact cannot be read: {scenario_id}: {exc}"
                )
                continue
            if scenario_result != item:
                errors.append(
                    f"scenario artifact differs from report: {scenario_id}"
                )
        passed_count = sum(
            isinstance(item, dict) and item.get("status") == "passed"
            for item in results
        )
        if report.get("scenario_count") != len(results):
            errors.append("report scenario_count is inconsistent")
        if report.get("passed_count") != passed_count:
            errors.append("report passed_count is inconsistent")
        ros_result = next(
            (
                item
                for item in results
                if isinstance(item, dict)
                and item.get("scenario_id") == "ros_master_restart"
            ),
            None,
        )
        expected_status = (
            "failed"
            if passed_count != len(results)
            else "prepared"
            if ros_result is not None
            and ros_result.get("live_ros_exercised") is not True
            else "passed"
        )
        if report.get("status") != expected_status:
            errors.append("report status is inconsistent with scenario results")
    return {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "run_id": report.get("run_id"),
        "suite_status": report.get("status"),
    }


def handle_fault_test(args: argparse.Namespace) -> int:
    if args.fault_test_command == "run":
        result = run_fault_injection_suite(
            repository_root=args.repository_root,
            artifact_dir=args.artifact_dir,
            scenario_ids=args.scenario,
            live_ros=args.live_ros,
        )
        _emit(result, as_json=args.json)
        return 0 if result["status"] == "passed" else 1
    if args.fault_test_command == "verify":
        result = verify_fault_injection_run(args.run_dir)
        _emit(result, as_json=args.json)
        return 0 if result["status"] == "valid" else 2
    raise ValueError(f"Unknown fault-test command: {args.fault_test_command}")


def _run_command(
    command: Sequence[str],
    cwd: Path,
    extra_env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    if extra_env is not None:
        environment.update(extra_env)
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )


def _emit(value: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(value, dict):
        print(f"status: {value.get('status')}")
        if value.get("run_directory"):
            print(f"run_directory: {value['run_directory']}")
        for item in value.get("results", []):
            print(
                f"- {item['scenario_id']}: {item['status']} "
                f"({item['assurance']})"
            )
        for limit in value.get("remaining_limits", []):
            print(f"remaining: {limit}")


def _bounded_output(value: object, *, limit: int = 20_000) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


__all__ = [
    "FAULT_INJECTION_SCHEMA_VERSION",
    "SCENARIOS",
    "handle_fault_test",
    "run_fault_injection_suite",
    "verify_fault_injection_run",
]
