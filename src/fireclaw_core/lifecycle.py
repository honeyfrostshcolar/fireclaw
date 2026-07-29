"""Lifecycle maintenance for FireClaw task and Robot Agent run registries.

Wraps ``LifecycleReconciler`` behind a single ``run()`` entry point that
returns a structured report suitable for operator dashboards, CLI output,
and automated health checks.

Persisted field names containing ``subagent`` remain compatibility identifiers.

This module does **not** run any background threads or timers.  Callers
invoke ``run()`` explicitly whenever they want a maintenance pass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore


class LifecycleMaintenanceRunner:
    """Thin orchestrator around ``LifecycleReconciler``.

    Provides a single ``run()`` method that:

    1. Invokes ``LifecycleReconciler.reconcile()`` with the caller-supplied
       (or auto-generated) timestamp and stale threshold.
    2. Wraps the raw reconciliation report in a higher-level envelope that
       includes a ``status`` field (``"ok"`` when no issues found,
       ``"warn"`` when orphans or stale tasks are detected) and a
       ``checked_at`` timestamp.

    Example::

        runner = LifecycleMaintenanceRunner(
            task_registry=tasks,
            subagent_registry=subagents,
        )
        report = runner.run()
        if report["status"] == "warn":
            print(report)
    """

    def __init__(
        self,
        *,
        task_registry: JsonlTaskRegistryStore,
        subagent_registry: JsonlSubagentRegistry,
    ) -> None:
        self._tasks = task_registry
        self._subagents = subagent_registry

    def run(
        self,
        *,
        now: str | None = None,
        stale_threshold_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Run a lifecycle maintenance pass and return a structured report.

        Args:
            now: Current ISO-8601 timestamp.  When ``None`` the runner
                generates the current UTC time automatically.
            stale_threshold_seconds: Age (in seconds) after which an active
                task is considered stale.  Defaults to 300 (5 minutes).

        Returns:
            A dict with the following shape::

                {
                    "status": "ok" | "warn",
                    "orphaned_subagents": list[str],
                    "stale_tasks": list[dict],
                    "reconciled_count": int,
                    "diagnostics": list[dict],
                    "checked_at": str,
                }

        .. note::
            This runner marks orphaned Robot Agent run records as terminal via
            ``LifecycleReconciler``.  It does **not** replay or resume
            physical robot work.  Operators **must** inspect actual robot
            state before issuing new commands after a ``"warn"`` report.
        """
        checked_at = now or datetime.now(timezone.utc).isoformat()

        reconciler = LifecycleReconciler(
            task_registry=self._tasks,
            subagent_registry=self._subagents,
        )
        inner = reconciler.reconcile(
            now=checked_at,
            stale_threshold_seconds=stale_threshold_seconds,
        )

        has_issues = bool(inner["orphaned_subagents"]) or bool(inner["stale_tasks"])

        return {
            "status": "warn" if has_issues else "ok",
            "orphaned_subagents": inner["orphaned_subagents"],
            "stale_tasks": inner["stale_tasks"],
            "reconciled_count": inner["reconciled_count"],
            "diagnostics": inner["diagnostics"],
            "checked_at": checked_at,
        }
"""Lifecycle reconciler for FireClaw task and subagent registries.

Inspired by OpenClaw's task-registry.maintenance.ts -- detects orphaned
subagent runs (child task missing) and stale active tasks (no recent events).
"""


class LifecycleReconciler:
    """Detects orphaned subagent runs and stale active tasks.

    Mirrors OpenClaw's task-registry.maintenance.ts patterns:
    - Orphan: subagent run whose child_task_id has no matching TaskRecord
    - Stale: active task whose last_event_at (or created_at) is older than threshold
    """

    def __init__(
        self,
        *,
        task_registry: JsonlTaskRegistryStore,
        subagent_registry: JsonlSubagentRegistry,
    ) -> None:
        self._tasks = task_registry
        self._subagents = subagent_registry

    def reconcile(
        self,
        *,
        now: str,
        stale_threshold_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Run reconciliation and return a deterministic report.

        Args:
            now: Current ISO timestamp for age calculation.
            stale_threshold_seconds: How old an active task must be to be
                considered stale (default 5 minutes).

        Returns:
            {
                "orphaned_subagents": list[str],  # child_task_ids marked orphaned
                "stale_tasks": list[dict],         # stale active task diagnostics
                "reconciled_count": int,           # total subagent records examined
                "diagnostics": list[dict],         # detailed diagnostics
            }
        """
        now_dt = _parse_timestamp(now)
        if now_dt is None:
            return {
                "orphaned_subagents": [],
                "stale_tasks": [],
                "reconciled_count": 0,
                "diagnostics": [{"type": "error", "message": f"invalid now timestamp: {now}"}],
            }
        all_tasks = self._tasks.list_records()
        all_subagents = self._subagents.list_records()
        task_index = {t.task_id: t for t in all_tasks}
        child_session_index = {
            t.child_session_id: t
            for t in all_tasks
            if t.child_session_id
        }

        orphaned: list[str] = []
        diagnostics: list[dict[str, Any]] = []

        # Check subagent runs for orphans
        for run in all_subagents:
            if run.is_terminal:
                continue
            child_task_id = run.child_task_id
            mission_projected_task_id = (
                f"{run.parent_mission_id}:{child_task_id}"
                if run.parent_mission_id
                else child_task_id
            )
            has_task_projection = (
                child_task_id in task_index
                or mission_projected_task_id in task_index
                or child_task_id in child_session_index
            )
            if not has_task_projection:
                # Mark as orphaned
                self._subagents.mark_terminal(
                    child_task_id=child_task_id,
                    status="orphaned",
                    updated_at=now,
                    error=f"child task {child_task_id} not found in task registry",
                )
                orphaned.append(child_task_id)
                diagnostics.append({
                    "type": "orphan",
                    "child_task_id": child_task_id,
                    "run_id": run.run_id,
                    "robot_id": run.robot_id,
                    "parent_mission_id": run.parent_mission_id,
                })

        # Check active tasks for staleness
        stale: list[dict[str, Any]] = []
        for task in all_tasks:
            if task.is_terminal:
                continue
            ref_time = task.last_event_at or task.created_at
            if not ref_time:
                continue
            task_dt = _parse_timestamp(ref_time)
            if task_dt is None:
                continue
            age = (now_dt - task_dt).total_seconds()
            if age >= stale_threshold_seconds:
                diag: dict[str, Any] = {
                    "task_id": task.task_id,
                    "runtime": task.runtime,
                    "status": task.status,
                    "age_seconds": round(age, 1),
                    "last_event_at": task.last_event_at,
                }
                stale.append(diag)
                diagnostics.append({
                    "type": "stale_task",
                    **diag,
                })

        return {
            "orphaned_subagents": orphaned,
            "stale_tasks": stale,
            "reconciled_count": len(all_subagents),
            "diagnostics": diagnostics,
        }


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse an ISO timestamp string, defaulting to UTC.

    Returns None for malformed input instead of raising, so one bad
    timestamp does not crash the entire reconciliation cycle.
    """
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        pass
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, IndexError):
        return None
