from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

from fireclaw_core.ros.ros1_log_stream import Ros1LogStream


class _FakeCore:
    def __init__(self, initialized: bool = True) -> None:
        self.initialized = initialized

    def is_initialized(self) -> bool:
        return self.initialized


class _FakeSubscriber:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.unregistered = False

    def unregister(self) -> None:
        self.unregistered = True


class _FakeRospy:
    def __init__(self, *, initialized: bool = True) -> None:
        self.core = _FakeCore(initialized)
        self.subscriber: _FakeSubscriber | None = None
        self.subscription_args = None

    def Subscriber(self, topic, message_type, callback, *, queue_size):
        self.subscription_args = (topic, message_type, queue_size)
        self.subscriber = _FakeSubscriber(callback)
        return self.subscriber


class _FakeStamp:
    def __init__(self, value: float) -> None:
        self.value = value

    def to_sec(self) -> float:
        return self.value


@dataclass
class _FakeHeader:
    stamp: _FakeStamp = field(default_factory=lambda: _FakeStamp(12.5))


@dataclass
class _FakeLog:
    level: int
    name: str
    msg: str
    header: _FakeHeader = field(default_factory=_FakeHeader)
    file: str = "move_base.cpp"
    function: str = "executeCycle"
    line: int = 42
    topics: list[str] = field(default_factory=lambda: ["/move_base/status"])


def test_ros_log_stream_is_async_filters_info_and_captures_task_context():
    rospy = _FakeRospy()
    emitted: list[dict] = []
    emitted_event = threading.Event()
    sink_entered = threading.Event()
    release_sink = threading.Event()
    context = {
        "task_binding": "active_task",
        "task_id": "task-1",
        "session_id": "mission-1",
    }

    def sink(record: dict) -> None:
        sink_entered.set()
        release_sink.wait(1.0)
        emitted.append(record)
        emitted_event.set()

    stream = Ros1LogStream(
        sink,
        context_provider=lambda: dict(context),
        rospy_loader=lambda: rospy,
        log_message_loader=lambda: _FakeLog,
    )
    snapshot = stream.start()
    assert snapshot.status == "ready"
    assert rospy.subscription_args == ("/rosout_agg", _FakeLog, 100)
    assert rospy.subscriber is not None

    rospy.subscriber.callback(
        _FakeLog(level=2, name="/move_base", msg="ordinary info")
    )
    started = time.monotonic()
    rospy.subscriber.callback(
        _FakeLog(
            level=8,
            name="/move_base",
            msg="\x1b[31mAborting because no valid plan was found\x1b[0m",
        )
    )
    callback_elapsed = time.monotonic() - started
    context.update({"task_id": "task-ended", "session_id": "other"})

    assert callback_elapsed < 0.1
    assert sink_entered.wait(1.0)
    assert stream.flush(
        timeout_seconds=0.02,
        quiet_period_seconds=0.0,
    ) is False
    release_sink.set()
    assert emitted_event.wait(1.0)
    assert stream.flush(
        timeout_seconds=1.0,
        quiet_period_seconds=0.0,
    ) is True
    assert len(emitted) == 1
    record = emitted[0]
    assert record["severity"] == "ERROR"
    assert record["node"] == "/move_base"
    assert record["message"] == "Aborting because no valid plan was found"
    assert record["context"]["task_id"] == "task-1"
    assert record["ros_timestamp"] == 12.5

    stopped = stream.stop()
    assert stopped.status == "stopped"
    assert rospy.subscriber.unregistered is True
    assert stopped.received_count == 1
    assert stopped.emitted_count == 1


def test_ros_log_stream_degrades_without_initialized_ros_node():
    rospy = _FakeRospy(initialized=False)
    stream = Ros1LogStream(
        lambda record: None,
        rospy_loader=lambda: rospy,
        log_message_loader=lambda: _FakeLog,
    )

    snapshot = stream.start()

    assert snapshot.status == "degraded"
    assert snapshot.reason_code == "ros_node_not_initialized"
    assert rospy.subscriber is None
