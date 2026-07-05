"""Tests for lifecycle reconciler — orphan detection and terminal repair."""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.lifecycle import LifecycleReconciler
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore


def test_reconciler_marks_subtask_orphan_when_child_task_missing(tmp_path: Path) -> None:
    """Subagent run whose child_task_id has no matching TaskRecord → orphaned."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    subagents.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:01:00+00:00")

    assert report["orphaned_subagents"] == ["child-1"]
    assert subagents.get_by_child_task_id("child-1").status == "orphaned"


def test_reconciler_leaves_active_subagent_with_existing_task_alone(tmp_path: Path) -> None:
    """Subagent run whose child_task_id exists in TaskRegistry → not orphaned."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    # Create a task record
    tasks.create(
        task_id="child-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
    )

    # Create subagent run pointing to that task
    subagents.create(
        parent_mission_id="mission-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:01:00+00:00")

    assert report["orphaned_subagents"] == []
    assert subagents.get_by_child_task_id("child-1").status == "dispatched"


def test_reconciler_matches_mission_projected_subtask_by_child_session_id(tmp_path: Path) -> None:
    """MissionAgent projects subtasks as mission_id:child_task_id in TaskRegistry.

    Reconciliation must treat that projected record as a match for the
    SubagentRegistry child_task_id instead of marking a healthy run orphaned.
    """
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    tasks.project_task_state(
        task_id="mission-1:child-1",
        requester_session_id="mission-1",
        owner_id="robot-1",
        command="search",
        runtime="robot_gateway",
        scope_kind="subtask",
        status="accepted",
        delivery_status="delivered",
        notify_policy="state_changes",
        created_at="2026-06-10T00:00:00+00:00",
        parent_task_id="mission-1",
        child_session_id="child-1",
    )
    subagents.create(
        parent_mission_id="mission-1",
        parent_subtask_id="subtask-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:01:00+00:00")

    assert report["orphaned_subagents"] == []
    assert subagents.get_by_child_task_id("child-1").status == "dispatched"


def test_reconciler_skips_already_terminal_subagent(tmp_path: Path) -> None:
    """Terminal subagent runs are skipped — no orphan marking."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    record = subagents.create(
        parent_mission_id="mission-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )
    # Mark as completed
    subagents.mark_terminal(
        child_task_id="child-1",
        status="completed",
        updated_at="2026-06-10T00:00:30+00:00",
    )

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:01:00+00:00")

    assert report["orphaned_subagents"] == []
    # Status stays completed, not orphaned
    assert subagents.get_by_child_task_id("child-1").status == "completed"


def test_reconciler_report_includes_stale_active_tasks(tmp_path: Path) -> None:
    """Active tasks with no recent events are reported as stale."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    tasks.create(
        task_id="stale-task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:10:00+00:00", stale_threshold_seconds=60)

    assert len(report["stale_tasks"]) == 1
    assert report["stale_tasks"][0]["task_id"] == "stale-task-1"


def test_reconciler_empty_registries_produce_empty_report(tmp_path: Path) -> None:
    """Empty registries produce an empty report without errors."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:00:00+00:00")

    assert report["orphaned_subagents"] == []
    assert report["stale_tasks"] == []
    assert report["reconciled_count"] == 0


def test_reconciler_handles_malformed_task_timestamp_gracefully(tmp_path: Path) -> None:
    """A task with a garbage timestamp does not crash the reconciler."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    # Create a task with a valid timestamp
    tasks.create(
        task_id="task-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
        created_at="not-a-date",
    )

    # Should not raise — malformed timestamp is skipped
    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="2026-06-10T00:10:00+00:00", stale_threshold_seconds=60)

    # Task with bad timestamp is skipped, not reported as stale
    assert report["stale_tasks"] == []


def test_reconciler_handles_malformed_now_timestamp(tmp_path: Path) -> None:
    """A garbage `now` timestamp returns an error report without crashing."""
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")

    report = LifecycleReconciler(
        task_registry=tasks, subagent_registry=subagents
    ).reconcile(now="garbage")

    assert report["orphaned_subagents"] == []
    assert len(report["diagnostics"]) == 1
    assert report["diagnostics"][0]["type"] == "error"
