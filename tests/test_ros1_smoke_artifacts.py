"""Tests for ROS1 smoke proof artifact schema and JSONL store."""
from __future__ import annotations

from pathlib import Path

import pytest

from fireclaw_core.ros1_smoke_artifacts import (
    JsonlRos1SmokeArtifactStore,
    Ros1SmokeArtifact,
)


def test_smoke_artifact_to_dict():
    artifact = Ros1SmokeArtifact(
        run_id="run-1",
        robot_id="fireclaw-01",
        environment="sim",
        command="去二楼救人",
        checks=("topic_publish", "service_call", "action_goal", "action_cancel"),
        passed=True,
        started_at="2026-06-10T00:00:00+08:00",
        finished_at="2026-06-10T00:00:10+08:00",
    )
    d = artifact.to_dict()
    assert d["run_id"] == "run-1"
    assert d["passed"] is True
    assert len(d["checks"]) == 4
    assert d["checks"] == ["topic_publish", "service_call", "action_goal", "action_cancel"]


def test_smoke_artifact_from_dict_roundtrip():
    artifact = Ros1SmokeArtifact(
        run_id="run-1",
        robot_id="fireclaw-01",
        environment="sim",
        command="test",
        checks=("topic_publish",),
        passed=True,
        started_at="2026-06-10T00:00:00+08:00",
        finished_at="2026-06-10T00:00:10+08:00",
        error_summary=None,
    )
    d = artifact.to_dict()
    restored = Ros1SmokeArtifact.from_dict(d)
    assert restored == artifact


def test_smoke_artifact_with_error_summary():
    artifact = Ros1SmokeArtifact(
        run_id="run-err",
        robot_id="fireclaw-01",
        environment="sim",
        command="test",
        checks=("topic_publish",),
        passed=False,
        started_at="2026-06-10T00:00:00+08:00",
        finished_at="2026-06-10T00:00:10+08:00",
        error_summary="roscore not reachable",
    )
    d = artifact.to_dict()
    assert d["passed"] is False
    assert d["error_summary"] == "roscore not reachable"


def test_artifact_store_append_and_list(tmp_path):
    store = JsonlRos1SmokeArtifactStore(str(tmp_path / "artifacts.jsonl"))
    artifact = Ros1SmokeArtifact(
        run_id="run-1",
        robot_id="fireclaw-01",
        environment="sim",
        command="test",
        checks=("topic_publish",),
        passed=True,
        started_at="2026-06-10T00:00:00+08:00",
        finished_at="2026-06-10T00:00:10+08:00",
    )
    store.append(artifact)
    recent = store.list_recent(limit=1)
    assert len(recent) == 1
    assert recent[0].run_id == "run-1"


def test_artifact_store_list_recent_ordering(tmp_path):
    store = JsonlRos1SmokeArtifactStore(str(tmp_path / "artifacts.jsonl"))
    for i in range(3):
        store.append(Ros1SmokeArtifact(
            run_id=f"run-{i}",
            robot_id="robot-1",
            environment="sim",
            command="test",
            checks=(),
            passed=True,
            started_at=f"2026-06-10T00:0{i}:00+08:00",
            finished_at=f"2026-06-10T00:0{i}:10+08:00",
        ))
    recent = store.list_recent(limit=2)
    assert len(recent) == 2
    assert recent[0].run_id == "run-2"  # most recent first


def test_artifact_store_list_recent_limit_exceeds_count(tmp_path):
    store = JsonlRos1SmokeArtifactStore(str(tmp_path / "artifacts.jsonl"))
    store.append(Ros1SmokeArtifact(
        run_id="only-one",
        robot_id="robot-1",
        environment="sim",
        command="test",
        checks=(),
        passed=True,
        started_at="2026-06-10T00:00:00+08:00",
        finished_at="2026-06-10T00:00:10+08:00",
    ))
    recent = store.list_recent(limit=10)
    assert len(recent) == 1


def test_artifact_store_list_recent_empty(tmp_path):
    store = JsonlRos1SmokeArtifactStore(str(tmp_path / "artifacts.jsonl"))
    recent = store.list_recent(limit=5)
    assert recent == []
