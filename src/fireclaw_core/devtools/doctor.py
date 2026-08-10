from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from fireclaw_core.memory.memory_eval import evaluate_retrieval, load_eval_cases
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetriever
from fireclaw_core.ros.ros1_config import load_ros1_adapter_config
from fireclaw_core.execution.runtime_config import ADAPTER_CHOICES, create_robot_adapter
from fireclaw_core.task.task_queue import JsonlTaskQueue


STATUS_ORDER = {"pass": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def run_doctor(
    *,
    adapter: str = "dry-run",
    robot_id: str = "fireclaw-doctor",
    memory_path: str = "memory/fireclaw-runs.jsonl",
    event_path: str = "memory/fireclaw-events.jsonl",
    ros1_config_path: str | None = None,
    task_queue_path: str | None = None,
    memory_index_path: str | None = None,
    memory_eval_fixture: str | None = None,
    memory_eval_threshold: float = 0.5,
    memory_eval_mission_id: str = "doctor-default",
    plugin_dir: str | None = None,
    security_config_path: str | None = None,
    fix: bool = False,
) -> dict[str, Any]:
    checks: list[DoctorCheck] = []
    repairs: list[dict[str, Any]] = []
    robot = None
    try:
        robot = create_robot_adapter(adapter, robot_id, config_path=ros1_config_path)
        checks.append(_adapter_check(adapter, robot))
    except Exception as exc:
        checks.append(
            DoctorCheck(
                name="adapter",
                status="fail",
                message=f"Failed to create adapter: {exc}",
                details={"adapter": adapter, "robot_id": robot_id},
            )
        )

    checks.append(_path_check("memory_path", memory_path))
    checks.append(_path_check("event_path", event_path))
    if adapter == "ros1":
        checks.append(_ros1_config_check(ros1_config_path))

    if robot is None:
        checks.append(
            DoctorCheck(
                name="emergency_stop_hook",
                status="fail",
                message="Cannot inspect emergency_stop hook without a robot adapter.",
            )
        )
    else:
        checks.append(_emergency_stop_hook_check(robot))

    # --- repair-flow checks ---
    # Memory index and plugin descriptors are always report-only.
    checks.append(_memory_index_check(memory_index_path))
    checks.append(_memory_eval_check(
        memory_index_path, memory_eval_fixture, memory_eval_threshold,
        mission_id=memory_eval_mission_id,
    ))
    checks.append(_plugin_descriptor_check(plugin_dir))
    checks.append(
        _security_audit_check(
            security_config_path,
            plugin_dir=plugin_dir,
        )
    )

    # --- repair actions (only when fix=True) ---
    if fix:
        repairs.extend(_repair_stale_task_queue(task_queue_path))
        # Memory index missing and invalid plugins are report-only;
        # they are not auto-repaired.

    # Stale task queue check runs AFTER potential repairs so fix=True
    # can show the repaired state.
    checks.append(_stale_task_queue_check(task_queue_path))

    return {
        "status": _overall_status(checks),
        "checks": [asdict(check) for check in checks],
        "repairs": repairs,
        "fixed": len(repairs),
    }


def _adapter_check(adapter: str, robot: Any) -> DoctorCheck:
    mode = getattr(robot, "mode", "unknown")
    details = {
        "adapter": adapter,
        "mode": mode,
        "robot_id": getattr(robot, "robot_id", "unknown"),
        "dry_run": getattr(robot, "dry_run", None),
    }
    if adapter in {"mock-ros1", "mock-ros2"}:
        return DoctorCheck(
            name="adapter",
            status="warn",
            message=f"{adapter} is a test double and does not connect to a live ROS1 robot.",
            details=details,
        )
    if adapter == "dry-run":
        return DoctorCheck(
            name="adapter",
            status="warn",
            message="dry-run adapter cannot control a robot.",
            details=details,
        )
    if adapter == "simulator":
        return DoctorCheck(
            name="adapter",
            status="warn",
            message="simulator adapter is deterministic local simulation, not a live robot.",
            details=details,
        )
    if adapter == "ros1":
        config = getattr(robot, "config", None)
        config_loaded = config is not None
        estop_configured = (
            config.emergency_stop is not None if config and hasattr(config, "emergency_stop") else False
        )
        details["ros1_config_loaded"] = config_loaded
        details["emergency_stop_configured"] = estop_configured
        warnings = []
        if not estop_configured:
            warnings.append("emergency_stop endpoint missing")
        if warnings:
            return DoctorCheck(
                name="adapter",
                status="warn",
                message="ROS1 adapter readiness: " + "; ".join(warnings) + ".",
                details=details,
            )
        return DoctorCheck(
            name="adapter",
            status="pass",
            message="ROS1 adapter readiness: config and emergency stop verified.",
            details=details,
        )
    return DoctorCheck(
        name="adapter",
        status="pass",
        message="Adapter created.",
        details=details,
    )


def _path_check(name: str, path: str) -> DoctorCheck:
    target = Path(path)
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / f".{target.name}.doctor-check"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        return DoctorCheck(
            name=name,
            status="fail",
            message=f"Path parent is not writable: {exc}",
            details={"path": str(target), "parent": str(parent)},
        )
    return DoctorCheck(
        name=name,
        status="pass",
        message="Path parent is writable.",
        details={"path": str(target), "parent": str(parent)},
    )


def _ros1_config_check(ros1_config_path: str | None) -> DoctorCheck:
    if ros1_config_path is None:
        return DoctorCheck(
            name="ros1_config",
            status="fail",
            message="ros1 adapter requires --ros1-config.",
            details={"ros1_config_path": None},
        )
    try:
        config = load_ros1_adapter_config(ros1_config_path)
    except Exception as exc:
        return DoctorCheck(
            name="ros1_config",
            status="fail",
            message=f"Failed to load ROS1 config: {exc}",
            details={"ros1_config_path": ros1_config_path},
        )
    details = {
        "ros1_config_path": ros1_config_path,
        "robot_id": config.robot_id,
        "namespace": config.namespace,
        "emergency_stop_configured": config.emergency_stop is not None,
        "diagnostics_enabled": config.diagnostics.enabled,
    }
    warnings = []
    if config.emergency_stop is None:
        warnings.append("emergency_stop endpoint is missing")
    if warnings:
        return DoctorCheck(
            name="ros1_config",
            status="warn",
            message="ROS1 config loaded with warnings: " + "; ".join(warnings) + ".",
            details=details,
        )
    return DoctorCheck(
        name="ros1_config",
        status="pass",
        message="ROS1 core adapter config is valid; domain endpoints remain Plugin-owned.",
        details=details,
    )


def _emergency_stop_hook_check(robot: Any) -> DoctorCheck:
    if callable(getattr(robot, "emergency_stop", None)):
        return DoctorCheck(
            name="emergency_stop_hook",
            status="pass",
            message="Robot adapter exposes emergency_stop(reason=None).",
            details={"mode": getattr(robot, "mode", "unknown")},
        )
    return DoctorCheck(
        name="emergency_stop_hook",
        status="fail",
        message="Robot adapter does not expose emergency_stop(reason=None).",
        details={"mode": getattr(robot, "mode", "unknown")},
    )


def _stale_task_queue_check(task_queue_path: str | None) -> DoctorCheck:
    if task_queue_path is None:
        return DoctorCheck(
            name="stale_task_queue",
            status="pass",
            message="No task queue path configured.",
            details={"stale_count": 0, "stale_task_ids": []},
        )
    queue = JsonlTaskQueue(task_queue_path)
    records = queue.list_records()
    stale = [r for r in records if not r.is_terminal]
    if stale:
        return DoctorCheck(
            name="stale_task_queue",
            status="warn",
            message=f"{len(stale)} non-terminal task queue record(s) found.",
            details={"stale_count": len(stale), "stale_task_ids": [r.task_id for r in stale]},
        )
    return DoctorCheck(
        name="stale_task_queue",
        status="pass",
        message="No stale task queue records.",
        details={"stale_count": 0, "stale_task_ids": []},
    )


def _memory_index_check(memory_index_path: str | None) -> DoctorCheck:
    if memory_index_path is None:
        return DoctorCheck(
            name="memory_index",
            status="pass",
            message="No memory index path configured.",
            details={"path": None},
        )
    target = Path(memory_index_path)
    if not target.exists():
        return DoctorCheck(
            name="memory_index",
            status="warn",
            message=f"Memory index not found: {target}",
            details={"path": str(target), "exists": False},
        )
    return DoctorCheck(
        name="memory_index",
        status="pass",
        message="Memory index file exists.",
        details={"path": str(target), "exists": True},
    )


def _memory_eval_check(
    memory_index_path: str | None,
    memory_eval_fixture: str | None,
    threshold: float,
    *,
    mission_id: str = "doctor-default",
) -> DoctorCheck:
    """Run retrieval evaluation when both index and fixture are provided."""
    if memory_index_path is None or memory_eval_fixture is None:
        return DoctorCheck(
            name="memory_eval",
            status="pass",
            message="Memory retrieval evaluation skipped (no index or fixture path).",
            details={"memory_index_path": memory_index_path, "memory_eval_fixture": memory_eval_fixture},
        )
    index_path = Path(memory_index_path)
    if not index_path.exists():
        return DoctorCheck(
            name="memory_eval",
            status="warn",
            message=f"Memory index not found at {index_path}; cannot run evaluation.",
            details={"memory_index_path": str(index_path), "exists": False},
        )
    fixture_path = Path(memory_eval_fixture)
    if not fixture_path.exists():
        return DoctorCheck(
            name="memory_eval",
            status="warn",
            message=f"Eval fixture not found at {fixture_path}.",
            details={"memory_eval_fixture": str(fixture_path), "exists": False},
        )
    try:
        from fireclaw_core.memory.memory_retrieval import MemoryRetrievalScope

        index = SqliteMemoryIndex(str(index_path))
        retriever = MemoryRetriever(index=index)
        cases = load_eval_cases(fixture_path)
        scope = MemoryRetrievalScope(
            mission_ids=(mission_id,),
            runtime_modes=("real", "simulation", "replay"),
            allowed_sensitivities=("standard", "restricted"),
        )
        report = evaluate_retrieval(retriever, cases, scope=scope)
    except Exception as exc:
        return DoctorCheck(
            name="memory_eval",
            status="fail",
            message=f"Memory retrieval evaluation failed: {exc}",
            details={"error": str(exc)},
        )
    meets = report.meets_threshold(threshold)
    details: dict[str, Any] = {
        "total": report.total,
        "passed": report.passed,
        "failed": report.failed,
        "hit_rate": round(report.hit_rate, 3),
        "threshold": threshold,
        "meets_threshold": meets,
        "missing_cases": report.missing_cases,
    }
    if meets:
        return DoctorCheck(
            name="memory_eval",
            status="pass",
            message=f"Memory retrieval hit_rate {report.hit_rate:.1%} meets threshold {threshold:.1%}.",
            details=details,
        )
    return DoctorCheck(
        name="memory_eval",
        status="warn",
        message=f"Memory retrieval hit_rate {report.hit_rate:.1%} below threshold {threshold:.1%}.",
        details=details,
    )


def _plugin_descriptor_check(plugin_dir: str | None) -> DoctorCheck:
    if plugin_dir is None:
        return DoctorCheck(
            name="plugin_descriptors",
            status="pass",
            message="No plugin directory configured.",
            details={"plugin_dir": None, "invalid_count": 0, "invalid_files": []},
        )
    dir_path = Path(plugin_dir)
    if not dir_path.exists():
        return DoctorCheck(
            name="plugin_descriptors",
            status="pass",
            message="Plugin directory does not exist; skipping.",
            details={"plugin_dir": str(dir_path), "invalid_count": 0, "invalid_files": []},
        )
    invalid: list[str] = []
    for manifest_file in sorted(dir_path.glob("*.plugin.json")):
        try:
            json.loads(manifest_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            invalid.append(manifest_file.name)
    if invalid:
        return DoctorCheck(
            name="plugin_descriptors",
            status="warn",
            message=f"{len(invalid)} invalid plugin descriptor file(s).",
            details={"plugin_dir": str(dir_path), "invalid_count": len(invalid), "invalid_files": invalid},
        )
    return DoctorCheck(
        name="plugin_descriptors",
        status="pass",
        message="All plugin descriptors are valid JSON.",
        details={"plugin_dir": str(dir_path), "invalid_count": 0, "invalid_files": []},
    )


def _security_audit_check(
    config_path: str | None,
    *,
    plugin_dir: str | None,
) -> DoctorCheck:
    if config_path is None:
        return DoctorCheck(
            name="security_audit",
            status="pass",
            message=(
                "Deployment security audit skipped because no config path "
                "was supplied."
            ),
            details={
                "config_path": None,
                "summary": {
                    "critical": 0,
                    "warn": 0,
                    "info": 0,
                    "status": "not_run",
                },
            },
        )
    from fireclaw_core.security.audit import run_security_audit

    report = run_security_audit(
        config_path=config_path,
        plugin_dirs=(plugin_dir,) if plugin_dir is not None else (),
    )
    status = {
        "critical": "fail",
        "warn": "warn",
        "ok": "pass",
    }[report.summary.status]
    return DoctorCheck(
        name="security_audit",
        status=status,
        message=(
            "Deployment security audit completed with "
            f"{report.summary.critical} critical and "
            f"{report.summary.warn} warning finding(s)."
        ),
        details=report.to_dict(),
    )


def _repair_stale_task_queue(task_queue_path: str | None) -> list[dict[str, Any]]:
    if task_queue_path is None:
        return []
    queue = JsonlTaskQueue(task_queue_path)
    stale_before = [r for r in queue.list_records() if not r.is_terminal]
    if not stale_before:
        return []
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    lost = queue.mark_non_terminal_lost(
        ended_at=now,
        error="auto-repaired by doctor --fix: marked stale non-terminal record as lost",
    )
    return [
        {"action": "mark_stale_lost", "task_id": record.task_id, "old_status": stale_before[i].status}
        for i, record in enumerate(lost)
    ]


def _overall_status(checks: list[DoctorCheck]) -> str:
    return max(checks, key=lambda check: STATUS_ORDER[check.status]).status


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FireClaw local configuration diagnostics.")
    parser.add_argument("--adapter", choices=ADAPTER_CHOICES, default="dry-run")
    parser.add_argument("--robot-id", default="fireclaw-doctor")
    parser.add_argument("--memory-path", default="memory/fireclaw-runs.jsonl")
    parser.add_argument("--event-path", default="memory/fireclaw-events.jsonl")
    parser.add_argument("--ros1-config", default=None)
    parser.add_argument("--task-queue", default=None, help="Path to JSONL task queue file")
    parser.add_argument("--memory-index", default=None, help="Path to memory index SQLite file")
    parser.add_argument("--plugin-dir", default=None, help="Path to plugin descriptor directory")
    parser.add_argument(
        "--security-config",
        default=None,
        help="Path to fireclaw.toml for the read-only deployment security audit.",
    )
    parser.add_argument("--memory-eval-fixture", default=None, help="Path to memory retrieval eval fixture JSON")
    parser.add_argument("--memory-eval-threshold", type=float, default=0.5, help="Minimum hit_rate to pass memory eval (default: 0.5)")
    parser.add_argument("--fix", action="store_true", help="Attempt to repair detected issues")
    args = parser.parse_args()

    report = run_doctor(
        adapter=args.adapter,
        robot_id=args.robot_id,
        memory_path=args.memory_path,
        event_path=args.event_path,
        ros1_config_path=args.ros1_config,
        task_queue_path=args.task_queue,
        memory_index_path=args.memory_index,
        memory_eval_fixture=args.memory_eval_fixture,
        memory_eval_threshold=args.memory_eval_threshold,
        plugin_dir=args.plugin_dir,
        security_config_path=args.security_config,
        fix=args.fix,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
