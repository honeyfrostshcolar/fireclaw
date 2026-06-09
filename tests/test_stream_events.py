from __future__ import annotations

import json
import threading
import time
from queue import Queue

import pytest

from fireclaw_core.stream_events import (
    EventBus,
    StreamEvent,
    TelemetryTracker,
    stream_event_from_ledger_record,
)


class TestStreamEvent:
    def test_create_minimal(self) -> None:
        event = StreamEvent(
            event_type="task.received",
            source="gateway",
        )
        assert event.event_type == "task.received"
        assert event.source == "gateway"
        assert event.event_id.startswith("evt-")
        assert event.mission_id is None
        assert event.robot_id is None
        assert event.task_id is None
        assert event.sequence >= 0
        assert event.payload == {}

    def test_create_full(self) -> None:
        event = StreamEvent(
            event_type="action.succeeded",
            source="robot-alpha",
            mission_id="mission-1",
            robot_id="robot-alpha",
            task_id="task-1",
            payload={"skill": "navigate"},
        )
        assert event.mission_id == "mission-1"
        assert event.robot_id == "robot-alpha"
        assert event.task_id == "task-1"
        assert event.payload == {"skill": "navigate"}

    def test_to_dict(self) -> None:
        event = StreamEvent(
            event_type="task.received",
            source="gateway",
            task_id="t1",
        )
        d = event.to_dict()
        assert d["event_type"] == "task.received"
        assert d["source"] == "gateway"
        assert d["task_id"] == "t1"
        assert "event_id" in d
        assert "timestamp" in d
        assert "sequence" in d

    def test_to_sse_format(self) -> None:
        event = StreamEvent(
            event_type="task.received",
            source="gateway",
        )
        sse = event.to_sse_format()
        assert sse.startswith("event: task.received\ndata: ")
        assert sse.endswith("\n\n")
        data = json.loads(sse.split("data: ", 1)[1].strip())
        assert data["event_type"] == "task.received"

    def test_from_dict_roundtrip(self) -> None:
        event = StreamEvent(
            event_type="action.feedback",
            source="robot-beta",
            robot_id="robot-beta",
            task_id="t2",
            payload={"progress": 50},
        )
        d = event.to_dict()
        restored = StreamEvent.from_dict(d)
        assert restored.event_type == event.event_type
        assert restored.source == event.source
        assert restored.robot_id == event.robot_id
        assert restored.task_id == event.task_id
        assert restored.payload == event.payload


class TestEventBus:
    def test_subscribe_and_publish(self) -> None:
        bus = EventBus()
        received: list[StreamEvent] = []
        bus.subscribe(lambda e: received.append(e))
        event = StreamEvent(event_type="test", source="test")
        bus.publish(event)
        assert len(received) == 1
        assert received[0].event_type == "test"

    def test_multiple_subscribers(self) -> None:
        bus = EventBus()
        a: list[StreamEvent] = []
        b: list[StreamEvent] = []
        bus.subscribe(lambda e: a.append(e))
        bus.subscribe(lambda e: b.append(e))
        bus.publish(StreamEvent(event_type="x", source="s"))
        assert len(a) == 1
        assert len(b) == 1

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        received: list[StreamEvent] = []
        token = bus.subscribe(lambda e: received.append(e))
        bus.publish(StreamEvent(event_type="a", source="s"))
        assert len(received) == 1
        bus.unsubscribe(token)
        bus.publish(StreamEvent(event_type="b", source="s"))
        assert len(received) == 1  # no new event

    def test_thread_safe_publish(self) -> None:
        bus = EventBus()
        count = 0
        lock = threading.Lock()

        def handler(event: StreamEvent) -> None:
            nonlocal count
            with lock:
                count += 1

        bus.subscribe(handler)
        threads = [
            threading.Thread(
                target=lambda: bus.publish(StreamEvent(event_type="t", source="s"))
            )
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert count == 20

    def test_sequence_auto_increment(self) -> None:
        bus = EventBus()
        events: list[StreamEvent] = []
        bus.subscribe(lambda e: events.append(e))
        for _ in range(5):
            bus.publish(StreamEvent(event_type="x", source="s"))
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5

    def test_subscribe_with_filter(self) -> None:
        bus = EventBus()
        received: list[StreamEvent] = []
        bus.subscribe(lambda e: received.append(e), event_types={"task.done"})
        bus.publish(StreamEvent(event_type="task.start", source="s"))
        bus.publish(StreamEvent(event_type="task.done", source="s"))
        bus.publish(StreamEvent(event_type="task.error", source="s"))
        assert len(received) == 1
        assert received[0].event_type == "task.done"


class TestTelemetryTracker:
    def test_record_task_latency(self) -> None:
        tracker = TelemetryTracker()
        tracker.record_event(
            StreamEvent(event_type="task.received", source="g", task_id="t1",
                        timestamp="2026-01-01T00:00:00Z")
        )
        tracker.record_event(
            StreamEvent(event_type="task.completed", source="g", task_id="t1",
                        timestamp="2026-01-01T00:00:05Z")
        )
        latencies = tracker.task_latencies()
        assert "t1" in latencies
        assert abs(latencies["t1"] - 5.0) < 0.01

    def test_record_action_duration(self) -> None:
        tracker = TelemetryTracker()
        tracker.record_event(
            StreamEvent(event_type="action.started", source="r", task_id="t1",
                        timestamp="2026-01-01T00:00:00Z",
                        payload={"action_id": "a1"})
        )
        tracker.record_event(
            StreamEvent(event_type="action.succeeded", source="r", task_id="t1",
                        timestamp="2026-01-01T00:00:03Z",
                        payload={"action_id": "a1"})
        )
        durations = tracker.action_durations()
        assert "a1" in durations
        assert abs(durations["a1"] - 3.0) < 0.01

    def test_cancel_latency(self) -> None:
        tracker = TelemetryTracker()
        tracker.record_event(
            StreamEvent(event_type="task.cancel_requested", source="g", task_id="t1",
                        timestamp="2026-01-01T00:00:00Z")
        )
        tracker.record_event(
            StreamEvent(event_type="task.cancelled", source="g", task_id="t1",
                        timestamp="2026-01-01T00:00:02Z")
        )
        latencies = tracker.cancel_latencies()
        assert "t1" in latencies
        assert abs(latencies["t1"] - 2.0) < 0.01

    def test_failure_reasons(self) -> None:
        tracker = TelemetryTracker()
        tracker.record_event(
            StreamEvent(event_type="task.failed", source="g", task_id="t1",
                        payload={"error": "timeout"})
        )
        tracker.record_event(
            StreamEvent(event_type="action.failed", source="r", task_id="t2",
                        payload={"error": "sensor malfunction"})
        )
        reasons = tracker.failure_reasons()
        assert reasons["t1"] == "timeout"
        assert reasons["t2"] == "sensor malfunction"

    def test_heartbeat_age(self) -> None:
        tracker = TelemetryTracker()
        now = time.time()
        tracker.update_heartbeat("robot-alpha", now - 30)
        tracker.update_heartbeat("robot-beta", now - 5)
        ages = tracker.heartbeat_ages()
        assert 25 < ages["robot-alpha"] < 35
        assert 0 < ages["robot-beta"] < 10

    def test_empty_state(self) -> None:
        tracker = TelemetryTracker()
        assert tracker.task_latencies() == {}
        assert tracker.action_durations() == {}
        assert tracker.cancel_latencies() == {}
        assert tracker.failure_reasons() == {}
        assert tracker.heartbeat_ages() == {}


class TestStreamEventFromLedgerRecord:
    def test_stream_event_from_task_ledger_event(self) -> None:
        event = stream_event_from_ledger_record(
            {
                "type": "task.completed",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "task_id": "task-1",
                "session_id": "mission-1",
                "payload": {"status": "completed"},
            },
            source="robot-alpha",
            robot_id="robot-alpha",
        )
        assert event.event_type == "task.completed"
        assert event.mission_id == "mission-1"
        assert event.robot_id == "robot-alpha"
        assert event.task_id == "task-1"
        assert event.payload == {"status": "completed"}
        assert event.timestamp == "2026-01-01T00:00:00+00:00"
        assert event.source == "robot-alpha"

    def test_defaults_when_fields_missing(self) -> None:
        event = stream_event_from_ledger_record(
            {},
            source="gateway",
        )
        assert event.event_type == "unknown"
        assert event.source == "gateway"
        assert event.robot_id is None
        assert event.task_id is None
        assert event.mission_id is None
        assert event.payload == {}
        assert event.timestamp is not None  # generated
