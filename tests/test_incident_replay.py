from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.monitoring.event_ledger import EventLedger
from fireclaw_core.monitoring.incident_replay import IncidentReplay
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_registry import (
    JsonlMissionRegistry,
    MissionRecord,
    MissionSubtaskRecord,
)


def _write_mission_registry(path: Path, *, mission_id: str = "m-1", command: str = "save people", created_at: str = "2026-06-08T10:00:00Z") -> None:
    with path.open("w", encoding="utf-8") as f:
        entry = {
            "type": "mission",
            "mission": {
                "mission_id": mission_id,
                "session_id": "s-1",
                "command": command,
                "status": "created",
                "created_at": created_at,
                "updated_at": created_at,
            },
        }
        f.write(json.dumps(entry) + "\n")


def _append_subtask(path: Path, *, mission_id: str = "m-1", robot_id: str = "r-1", task_id: str = "t-1", command: str = "go to floor 2", status: str = "submitted", created_at: str = "2026-06-08T10:01:00Z", updated_at: str | None = None) -> None:
    with path.open("a", encoding="utf-8") as f:
        entry = {
            "type": "subtask",
            "mission_id": mission_id,
            "subtask": {
                "robot_id": robot_id,
                "task_id": task_id,
                "command": command,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at or created_at,
            },
        }
        f.write(json.dumps(entry) + "\n")


def _write_memory_store(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


class TestReplayBasicInfo:
    def test_replay_returns_mission_basic_info(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1", command="rescue people on floor 2", created_at="2026-06-08T10:00:00Z")

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        assert result["mission_id"] == "m-1"
        assert result["command"] == "rescue people on floor 2"
        assert result["status"] == "created"
        assert result["created_at"] == "2026-06-08T10:00:00Z"
        assert result["updated_at"] == "2026-06-08T10:00:00Z"


class TestReplaySubtaskEvents:
    def test_replay_includes_subtask_events(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        timeline = result["timeline"]
        assert len(timeline) == 2

        submitted = timeline[0]
        assert submitted["event_type"] == "subtask.submitted"
        assert submitted["timestamp"] == "2026-06-08T10:01:00Z"
        assert submitted["robot_id"] == "r-1"
        assert submitted["task_id"] == "t-1"
        assert submitted["status"] is None
        assert submitted["content"] is None

        changed = timeline[1]
        assert changed["event_type"] == "subtask.status_changed"
        assert changed["timestamp"] == "2026-06-08T10:05:00Z"
        assert changed["robot_id"] == "r-1"
        assert changed["task_id"] == "t-1"
        assert changed["status"] == "completed"
        assert changed["content"] is None

    def test_submitted_only_subtask_has_no_status_changed_event(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="submitted",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:01:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        timeline = result["timeline"]
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "subtask.submitted"


class TestReplayMemoryRecords:
    def test_replay_includes_memory_records(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "rescued 3 people"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
            {
                "record_id": "rec-2",
                "mission_id": "m-1",
                "record_type": "correction",
                "content": {"note": "should check floor 3 first"},
                "robot_id": None,
                "subtask_id": None,
                "created_at": "2026-06-08T10:07:00Z",
            },
        ])

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
        result = replay.replay("m-1")

        timeline = result["timeline"]
        assert len(timeline) == 2

        outcome = timeline[0]
        assert outcome["event_type"] == "outcome"
        assert outcome["timestamp"] == "2026-06-08T10:06:00Z"
        assert outcome["robot_id"] == "r-1"
        assert outcome["task_id"] == "t-1"
        assert outcome["content"] == {"result": "rescued 3 people"}

        correction = timeline[1]
        assert correction["event_type"] == "correction"
        assert correction["timestamp"] == "2026-06-08T10:07:00Z"


class TestReplayTimelineSorting:
    def test_replay_timeline_sorted_by_timestamp(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:03:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "observation",
                "content": {"data": "smoke detected"},
                "robot_id": "r-1",
                "subtask_id": None,
                "created_at": "2026-06-08T10:02:00Z",
            },
            {
                "record_id": "rec-2",
                "mission_id": "m-1",
                "record_type": "lesson",
                "content": {"lesson": "always check smoke levels"},
                "robot_id": None,
                "subtask_id": None,
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
        result = replay.replay("m-1")

        timeline = result["timeline"]
        timestamps = [event["timestamp"] for event in timeline]
        assert timestamps == sorted(timestamps)
        assert len(timeline) == 4
        assert timeline[0]["event_type"] == "observation"
        assert timeline[1]["event_type"] == "subtask.submitted"
        assert timeline[2]["event_type"] == "subtask.status_changed"
        assert timeline[3]["event_type"] == "lesson"


class TestReplaySummary:
    def test_replay_summary_counts(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-2",
            task_id="t-2",
            command="check floor 3",
            status="failed",
            created_at="2026-06-08T10:01:30Z",
            updated_at="2026-06-08T10:04:00Z",
        )
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-3",
            task_id="t-3",
            command="standby",
            status="cancelled",
            created_at="2026-06-08T10:02:00Z",
            updated_at="2026-06-08T10:03:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "rescued 3 people"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
            {
                "record_id": "rec-2",
                "mission_id": "m-1",
                "record_type": "correction",
                "content": {"note": "check floor 3 first"},
                "robot_id": None,
                "subtask_id": None,
                "created_at": "2026-06-08T10:07:00Z",
            },
            {
                "record_id": "rec-3",
                "mission_id": "m-1",
                "record_type": "observation",
                "content": {"data": "smoke level high"},
                "robot_id": "r-1",
                "subtask_id": None,
                "created_at": "2026-06-08T10:02:30Z",
            },
        ])

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
        result = replay.replay("m-1")

        summary = result["summary"]
        assert summary["subtask_count"] == 3
        assert summary["completed_count"] == 1
        assert summary["failed_count"] == 1
        assert summary["cancelled_count"] == 1
        assert summary["memory_record_count"] == 3
        assert summary["correction_count"] == 1
        assert summary["duration_seconds"] is not None
        assert summary["duration_seconds"] > 0


class TestReplayNotFound:
    def test_replay_not_found_mission(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        registry_path.touch()

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        with pytest.raises(KeyError, match="not found"):
            replay.replay("nonexistent-mission")


class TestReplayWithoutMemoryStore:
    def test_replay_without_memory_store(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1", command="rescue", created_at="2026-06-08T10:00:00Z")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        assert result["mission_id"] == "m-1"
        assert len(result["timeline"]) == 2
        assert result["summary"]["memory_record_count"] == 0
        assert result["summary"]["correction_count"] == 0
        assert result["summary"]["subtask_count"] == 1
        assert result["summary"]["completed_count"] == 1


def _append_ledger_event(ledger: EventLedger, *, task_id: str = "t-1", session_id: str = "s-1", event_type: str = "skill.started", payload: dict | None = None, timestamp: str | None = None) -> dict:
    """Write an event to the ledger, overriding timestamp if provided."""
    event = ledger.append(
        task_id=task_id,
        session_id=session_id,
        type=event_type,
        payload=payload or {},
    )
    if timestamp is not None:
        # Rewrite file with corrected timestamp
        events = ledger.list_events()
        events[-1]["timestamp"] = timestamp
        with ledger.path.open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n")
        event = events[-1]
    return event


class TestReplayTimelineSchema:
    """Verify timeline entries include all StreamEvent-aligned fields."""

    EXPECTED_FIELDS = {"event_type", "timestamp", "source", "mission_id", "robot_id", "task_id", "payload"}

    def test_subtask_events_have_all_schema_fields(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        for entry in result["timeline"]:
            assert self.EXPECTED_FIELDS <= set(entry.keys()), f"Missing fields: {self.EXPECTED_FIELDS - set(entry.keys())}"

    def test_memory_events_have_all_schema_fields(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "rescued 3 people"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
        result = replay.replay("m-1")

        for entry in result["timeline"]:
            assert self.EXPECTED_FIELDS <= set(entry.keys()), f"Missing fields: {self.EXPECTED_FIELDS - set(entry.keys())}"

    def test_ledger_events_have_all_schema_fields(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={"skill": "navigate"}, timestamp="2026-06-08T10:01:30Z")

        registry = JsonlMissionRegistry(registry_path)
        replay = IncidentReplay(mission_registry=registry, event_ledger=ledger)
        result = replay.replay("m-1")

        for entry in result["timeline"]:
            assert self.EXPECTED_FIELDS <= set(entry.keys()), f"Missing fields: {self.EXPECTED_FIELDS - set(entry.keys())}"

    def test_combined_sources_have_all_schema_fields(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "ok"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={"skill": "navigate"}, timestamp="2026-06-08T10:01:30Z")

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory, event_ledger=ledger)
        result = replay.replay("m-1")

        for entry in result["timeline"]:
            assert self.EXPECTED_FIELDS <= set(entry.keys()), f"Missing fields: {self.EXPECTED_FIELDS - set(entry.keys())}"

    def test_source_field_values_are_correct(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "ok"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={"skill": "navigate"}, timestamp="2026-06-08T10:01:30Z")

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory, event_ledger=ledger)
        result = replay.replay("m-1")

        sources = {e["source"] for e in result["timeline"]}
        assert "mission-registry" in sources
        assert "memory" in sources
        assert "event-ledger" in sources

    def test_mission_id_field_matches_replayed_mission(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-42")
        _append_subtask(
            registry_path,
            mission_id="m-42",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-42")

        for entry in result["timeline"]:
            assert entry["mission_id"] == "m-42"

    def test_payload_is_dict(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "ok"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory)
        result = replay.replay("m-1")

        for entry in result["timeline"]:
            assert isinstance(entry["payload"], dict)


class TestReplayWithEventLedger:
    def test_replay_includes_action_events(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={"skill": "navigate"}, timestamp="2026-06-08T10:01:30Z")
        _append_ledger_event(ledger, task_id="t-1", event_type="action.feedback", payload={"progress": 50}, timestamp="2026-06-08T10:03:00Z")
        _append_ledger_event(ledger, task_id="t-1", event_type="action.succeeded", payload={"result": "arrived"}, timestamp="2026-06-08T10:04:00Z")

        registry = JsonlMissionRegistry(registry_path)
        replay = IncidentReplay(mission_registry=registry, event_ledger=ledger)
        result = replay.replay("m-1")

        timeline = result["timeline"]
        # 2 subtask events + 3 action events
        assert len(timeline) == 5
        action_events = [e for e in timeline if e["event_type"].startswith("skill.") or e["event_type"].startswith("action.")]
        assert len(action_events) == 3
        assert action_events[0]["event_type"] == "skill.started"
        assert action_events[0]["content"] == {"skill": "navigate"}
        assert action_events[1]["event_type"] == "action.feedback"
        assert action_events[2]["event_type"] == "action.succeeded"
        assert result["summary"]["action_event_count"] == 3

    def test_replay_filters_events_by_mission_task_ids(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        ledger = EventLedger(ledger_path)
        # Event for t-1 (in mission) -- should appear
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={}, timestamp="2026-06-08T10:02:00Z")
        # Event for t-99 (not in mission) -- should NOT appear
        _append_ledger_event(ledger, task_id="t-99", event_type="skill.started", payload={}, timestamp="2026-06-08T10:02:30Z")

        registry = JsonlMissionRegistry(registry_path)
        replay = IncidentReplay(mission_registry=registry, event_ledger=ledger)
        result = replay.replay("m-1")

        action_events = [e for e in result["timeline"] if e["event_type"] == "skill.started"]
        assert len(action_events) == 1
        assert action_events[0]["task_id"] == "t-1"
        assert result["summary"]["action_event_count"] == 1

    def test_replay_without_event_ledger_has_zero_action_events(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        replay = IncidentReplay(mission_registry=JsonlMissionRegistry(registry_path))
        result = replay.replay("m-1")

        assert result["summary"]["action_event_count"] == 0

    def test_replay_action_events_sorted_in_timeline(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={}, timestamp="2026-06-08T10:00:30Z")
        _append_ledger_event(ledger, task_id="t-1", event_type="action.succeeded", payload={}, timestamp="2026-06-08T10:06:00Z")

        registry = JsonlMissionRegistry(registry_path)
        replay = IncidentReplay(mission_registry=registry, event_ledger=ledger)
        result = replay.replay("m-1")

        timestamps = [e["timestamp"] for e in result["timeline"]]
        assert timestamps == sorted(timestamps)
        # skill.started at 10:00:30 should be first, action.succeeded at 10:06:00 last
        assert result["timeline"][0]["event_type"] == "skill.started"
        assert result["timeline"][-1]["event_type"] == "action.succeeded"

    def test_replay_combined_sources(self, tmp_path: Path) -> None:
        registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "memory.jsonl"
        ledger_path = tmp_path / "events.jsonl"
        _write_mission_registry(registry_path, mission_id="m-1")
        _append_subtask(
            registry_path,
            mission_id="m-1",
            robot_id="r-1",
            task_id="t-1",
            command="go to floor 2",
            status="succeeded",
            created_at="2026-06-08T10:01:00Z",
            updated_at="2026-06-08T10:05:00Z",
        )
        _write_memory_store(memory_path, [
            {
                "record_id": "rec-1",
                "mission_id": "m-1",
                "record_type": "outcome",
                "content": {"result": "ok"},
                "robot_id": "r-1",
                "subtask_id": "t-1",
                "created_at": "2026-06-08T10:06:00Z",
            },
        ])

        ledger = EventLedger(ledger_path)
        _append_ledger_event(ledger, task_id="t-1", event_type="skill.started", payload={"skill": "navigate"}, timestamp="2026-06-08T10:01:30Z")
        _append_ledger_event(ledger, task_id="t-1", event_type="action.succeeded", payload={}, timestamp="2026-06-08T10:04:30Z")

        registry = JsonlMissionRegistry(registry_path)
        memory = MissionMemoryStore(memory_path)
        replay = IncidentReplay(mission_registry=registry, mission_memory=memory, event_ledger=ledger)
        result = replay.replay("m-1")

        # 2 subtask + 1 memory + 2 action = 5
        assert len(result["timeline"]) == 5
        assert result["summary"]["memory_record_count"] == 1
        assert result["summary"]["action_event_count"] == 2
        timestamps = [e["timestamp"] for e in result["timeline"]]
        assert timestamps == sorted(timestamps)
