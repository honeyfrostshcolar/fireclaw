"""Tests for LifecycleMaintenanceRunner."""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.lifecycle_maintenance import LifecycleMaintenanceRunner
from fireclaw_core.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task_registry import JsonlTaskRegistryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(tmp_path: Path) -> tuple[LifecycleMaintenanceRunner, JsonlTaskRegistryStore, JsonlSubagentRegistry]:
    tasks = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    subagents = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    runner = LifecycleMaintenanceRunner(
        task_registry=tasks,
        subagent_registry=subagents,
    )
    return runner, tasks, subagents


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_returns_ok_when_registries_empty(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path)

    report = runner.run(now="2026-06-10T00:00:00+00:00")

    assert report["status"] == "ok"
    assert report["orphaned_subagents"] == []
    assert report["stale_tasks"] == []
    assert report["reconciled_count"] == 0
    assert report["diagnostics"] == []
    assert report["checked_at"] == "2026-06-10T00:00:00+00:00"


def test_run_returns_ok_when_all_healthy(tmp_path: Path) -> None:
    """Subagent with matching task and fresh timestamps → ok."""
    runner, tasks, subagents = _make_runner(tmp_path)

    tasks.create(
        task_id="child-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
        created_at="2026-06-10T00:00:00+00:00",
    )
    subagents.create(
        parent_mission_id="mission-1",
        robot_id="robot-1",
        child_task_id="child-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = runner.run(now="2026-06-10T00:01:00+00:00", stale_threshold_seconds=300)

    assert report["status"] == "ok"
    assert report["orphaned_subagents"] == []
    assert report["stale_tasks"] == []


# ---------------------------------------------------------------------------
# Warn status
# ---------------------------------------------------------------------------


def test_run_returns_warn_when_orphans_detected(tmp_path: Path) -> None:
    runner, _, subagents = _make_runner(tmp_path)

    subagents.create(
        parent_mission_id="mission-1",
        robot_id="robot-1",
        child_task_id="ghost-child",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = runner.run(now="2026-06-10T00:01:00+00:00")

    assert report["status"] == "warn"
    assert "ghost-child" in report["orphaned_subagents"]


def test_run_returns_warn_when_stale_tasks_detected(tmp_path: Path) -> None:
    runner, tasks, _ = _make_runner(tmp_path)

    tasks.create(
        task_id="stale-1",
        runtime="robot_gateway",
        requester_session_id="mission-1",
        owner_id="robot-1",
        scope_kind="mission",
        command="search",
        created_at="2026-06-10T00:00:00+00:00",
    )

    # 10 minutes later with 60s threshold → stale
    report = runner.run(now="2026-06-10T00:10:00+00:00", stale_threshold_seconds=60)

    assert report["status"] == "warn"
    assert len(report["stale_tasks"]) == 1
    assert report["stale_tasks"][0]["task_id"] == "stale-1"


# ---------------------------------------------------------------------------
# checked_at auto-generation
# ---------------------------------------------------------------------------


def test_run_auto_generates_checked_at_when_now_is_none(tmp_path: Path) -> None:
    runner, _, _ = _make_runner(tmp_path)

    report = runner.run()  # now=None

    # checked_at should be a non-empty ISO timestamp
    assert report["checked_at"]
    assert "T" in report["checked_at"]


# ---------------------------------------------------------------------------
# Delegated fields from reconciler
# ---------------------------------------------------------------------------


def test_run_passes_stale_threshold_to_reconciler(tmp_path: Path) -> None:
    """A task created 5 minutes ago is stale at 300s but not at 600s."""
    runner, tasks, _ = _make_runner(tmp_path)

    tasks.create(
        task_id="t1",
        runtime="cli",
        requester_session_id="s1",
        owner_id="u1",
        scope_kind="session",
        command="ping",
        created_at="2026-06-10T00:00:00+00:00",
    )

    now = "2026-06-10T00:05:00+00:00"  # 300s later

    report_tight = runner.run(now=now, stale_threshold_seconds=300)
    assert len(report_tight["stale_tasks"]) == 1

    report_loose = runner.run(now=now, stale_threshold_seconds=600)
    assert len(report_loose["stale_tasks"]) == 0


def test_run_includes_reconciled_count(tmp_path: Path) -> None:
    runner, _, subagents = _make_runner(tmp_path)

    subagents.create(
        parent_mission_id="m1",
        robot_id="r1",
        child_task_id="c1",
        created_at="2026-06-10T00:00:00+00:00",
    )
    subagents.create(
        parent_mission_id="m1",
        robot_id="r1",
        child_task_id="c2",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = runner.run(now="2026-06-10T00:01:00+00:00")

    assert report["reconciled_count"] == 2


def test_run_includes_diagnostics_from_reconciler(tmp_path: Path) -> None:
    runner, _, subagents = _make_runner(tmp_path)

    subagents.create(
        parent_mission_id="m1",
        robot_id="r1",
        child_task_id="orphan-1",
        created_at="2026-06-10T00:00:00+00:00",
    )

    report = runner.run(now="2026-06-10T00:01:00+00:00")

    # Should have at least one orphan diagnostic
    orphan_diags = [d for d in report["diagnostics"] if d["type"] == "orphan"]
    assert len(orphan_diags) == 1
    assert orphan_diags[0]["child_task_id"] == "orphan-1"
