"""Lifecycle maintenance runner for FireClaw task and subagent registries.

Wraps ``LifecycleReconciler`` behind a single ``run()`` entry point that
returns a structured report suitable for operator dashboards, CLI output,
and automated health checks.

This module does **not** run any background threads or timers.  Callers
invoke ``run()`` explicitly whenever they want a maintenance pass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fireclaw_core.lifecycle_reconciler import LifecycleReconciler
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_registry import JsonlTaskRegistryStore


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
            This runner marks orphaned subagent records as terminal via
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
