from __future__ import annotations

from types import SimpleNamespace

from fireclaw_core.ros.ros1_runtime import Ros1RuntimeLifecycle


def _fake_rospy(*, initialized: bool = False):
    state = {"initialized": initialized}
    init_calls: list[tuple[str, bool, bool]] = []
    shutdown_calls: list[str] = []

    def init_node(name: str, *, anonymous: bool, disable_signals: bool) -> None:
        init_calls.append((name, anonymous, disable_signals))
        state["initialized"] = True

    def signal_shutdown(reason: str) -> None:
        shutdown_calls.append(reason)
        state["initialized"] = False

    rospy = SimpleNamespace(
        core=SimpleNamespace(
            is_initialized=lambda: state["initialized"],
        ),
        init_node=init_node,
        signal_shutdown=signal_shutdown,
    )
    return rospy, init_calls, shutdown_calls


def test_ros1_runtime_initializes_after_bounded_master_preflight() -> None:
    rospy, init_calls, shutdown_calls = _fake_rospy()
    probes: list[tuple[str, float]] = []
    runtime = Ros1RuntimeLifecycle(
        startup_timeout_seconds=0.2,
        master_probe=lambda uri, timeout: (
            probes.append((uri, timeout)) or (True, None)
        ),
        rospy_loader=lambda: rospy,
    )

    started = runtime.start()

    assert started.status == "ready"
    assert started.reason_code == "ros_node_initialized"
    assert started.node_initialized is True
    assert started.owns_node is True
    assert probes == [("http://localhost:11311", 0.2)]
    assert init_calls == [("fireclaw_gateway", True, True)]

    stopped = runtime.stop()

    assert stopped.status == "stopped"
    assert len(shutdown_calls) == 1


def test_ros1_runtime_degrades_without_initializing_when_master_is_absent() -> None:
    rospy, init_calls, shutdown_calls = _fake_rospy()
    runtime = Ros1RuntimeLifecycle(
        startup_timeout_seconds=0.2,
        master_probe=lambda uri, timeout: (False, "connection refused"),
        rospy_loader=lambda: rospy,
    )

    started = runtime.start()

    assert started.status == "degraded"
    assert started.reason_code == "ros_master_unavailable"
    assert started.node_initialized is False
    assert init_calls == []
    assert shutdown_calls == []


def test_ros1_runtime_adopts_but_does_not_shutdown_existing_node() -> None:
    rospy, init_calls, shutdown_calls = _fake_rospy(initialized=True)
    probe_calls: list[str] = []
    runtime = Ros1RuntimeLifecycle(
        master_probe=lambda uri, timeout: (
            probe_calls.append(uri) or (True, None)
        ),
        rospy_loader=lambda: rospy,
    )

    started = runtime.start()
    runtime.stop()

    assert started.status == "ready"
    assert started.reason_code == "ros_node_adopted"
    assert started.owns_node is False
    assert probe_calls == []
    assert init_calls == []
    assert shutdown_calls == []


def test_ros1_runtime_does_not_expose_master_uri_credentials() -> None:
    rospy, _, _ = _fake_rospy(initialized=True)
    runtime = Ros1RuntimeLifecycle(
        master_uri="http://operator:secret@robot.local:11311",
        rospy_loader=lambda: rospy,
    )

    snapshot = runtime.start()

    assert snapshot.master_uri == "http://robot.local:11311"
    assert "operator" not in snapshot.master_uri
    assert "secret" not in snapshot.master_uri
