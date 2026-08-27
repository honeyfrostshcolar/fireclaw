from __future__ import annotations

import threading

import fireclaw_core.gateway.gateway as gateway_module
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig, TaskControl


class _FakeServer:
    def __init__(self) -> None:
        self.server_address = ("127.0.0.1", 8765)
        self._stopped = threading.Event()
        self.closed = False

    def serve_forever(self) -> None:
        self._stopped.wait(1.0)

    def shutdown(self) -> None:
        self._stopped.set()

    def server_close(self) -> None:
        self.closed = True


class _FakeRosLogSnapshot:
    def __init__(self, status: str) -> None:
        self.status = status

    def to_dict(self):
        return {
            "schema_version": 1,
            "status": self.status,
            "reason_code": f"test_ros_log_{self.status}",
            "topic": "/rosout_agg",
            "minimum_level": "WARN",
            "dropped_count": 0,
        }


class _FakeRosLogStream:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.status = "not_started"

    def start(self):
        self.calls.append("ros-log-start")
        self.status = "ready"
        return _FakeRosLogSnapshot(self.status)

    def stop(self):
        self.calls.append("ros-log-stop")
        self.status = "stopped"
        return _FakeRosLogSnapshot(self.status)

    def snapshot(self):
        return _FakeRosLogSnapshot(self.status)


def test_robot_gateway_owns_adapter_runtime_before_http_intake(
    tmp_path,
    monkeypatch,
) -> None:
    fake_server = _FakeServer()
    monkeypatch.setattr(
        gateway_module,
        "create_gateway_http_server",
        lambda *args, **kwargs: fake_server,
    )
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
        )
    )
    calls: list[str] = []
    gateway.robot.start_runtime = lambda: (
        calls.append("start")
        or {
            "schema_version": 1,
            "status": "ready",
            "reason_code": "test_runtime_ready",
        }
    )
    gateway.robot.stop_runtime = lambda: (
        calls.append("stop")
        or {
            "schema_version": 1,
            "status": "stopped",
            "reason_code": "test_runtime_stopped",
        }
    )
    gateway.robot.runtime_state = lambda: {
        "schema_version": 1,
        "status": "ready",
        "reason_code": "test_runtime_ready",
    }

    gateway.start()
    try:
        assert calls == ["start"]
        assert gateway.health()["robot_runtime"]["status"] == "ready"
    finally:
        gateway.stop()

    assert calls == ["start", "stop"]
    assert fake_server.closed is True


def test_blocking_robot_gateway_stops_adapter_runtime_when_server_exits(
    tmp_path,
    monkeypatch,
) -> None:
    fake_server = _FakeServer()
    fake_server._stopped.set()
    monkeypatch.setattr(
        gateway_module,
        "create_gateway_http_server",
        lambda *args, **kwargs: fake_server,
    )
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
        )
    )
    calls: list[str] = []
    gateway.robot.start_runtime = lambda: (
        calls.append("start")
        or {"schema_version": 1, "status": "ready"}
    )
    gateway.robot.stop_runtime = lambda: (
        calls.append("stop")
        or {"schema_version": 1, "status": "stopped"}
    )

    gateway.serve_forever()

    assert calls == ["start", "stop"]
    assert fake_server.closed is True
    assert gateway._server is None


def test_robot_gateway_starts_ros_log_stream_after_adapter_and_stops_it_first(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[str] = []
    fake_server = _FakeServer()

    def create_server(*args, **kwargs):
        calls.append("http-create")
        return fake_server

    monkeypatch.setattr(gateway_module, "create_gateway_http_server", create_server)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
        ),
        ros_log_stream=_FakeRosLogStream(calls),
    )
    gateway.robot.start_runtime = lambda: (
        calls.append("robot-start")
        or {"schema_version": 1, "status": "ready"}
    )
    gateway.robot.stop_runtime = lambda: (
        calls.append("robot-stop")
        or {"schema_version": 1, "status": "stopped"}
    )

    gateway.start()
    try:
        assert calls[:3] == ["robot-start", "ros-log-start", "http-create"]
        assert gateway.health()["ros_log_stream"]["status"] == "ready"
    finally:
        gateway.stop()

    assert calls[-2:] == ["ros-log-stop", "robot-stop"]


def test_robot_gateway_persists_task_bound_ros_log_in_task_trace(tmp_path) -> None:
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
        )
    )
    control = TaskControl(
        task_id="task-1",
        session_id="mission-1",
        command="前往巡检点",
        started_at="2026-08-23T00:00:00+00:00",
    )
    gateway._task_controls[control.task_id] = control
    captured_context = gateway._ros_log_task_context()
    gateway._task_controls.pop(control.task_id)

    gateway._record_ros_log({
        "severity": "ERROR",
        "node": "/move_base",
        "message": "Aborting because a valid plan could not be found.",
        "context": captured_context,
    })

    trace = gateway.task_trace("task-1")
    ros_events = [event for event in trace["events"] if event["type"] == "ros.log"]
    assert len(ros_events) == 1
    assert ros_events[0]["session_id"] == "mission-1"
    assert ros_events[0]["payload"]["task_binding"] == "active_task"
    assert ros_events[0]["payload"]["node"] == "/move_base"
