from __future__ import annotations

import fcntl
from pathlib import Path
import signal
from typing import TextIO

import pytest

from fireclaw_core.deployment.supervisor import (
    _ProcessIdentity,
    _descendant_sessions,
    _processes_in_sessions,
    RuntimeSupervisor,
    RuntimeSupervisorSettings,
    inspect_runtime_supervisor_state,
)
from fireclaw_core.infra.runtime_state import (
    SqliteAuthoritativeRuntimeStore,
    SqliteResourceLeaseManager,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += max(0.0, duration)


class _Process:
    _next_pid = 1000

    def __init__(
        self,
        service: str,
        signal_order: list[tuple[str, int]],
        *,
        exit_after_polls: int | None = None,
        exit_code: int = 7,
    ) -> None:
        self.service = service
        self.signal_order = signal_order
        self.exit_after_polls = exit_after_polls
        self.exit_code = exit_code
        self.poll_count = 0
        self.return_code: int | None = None
        self.pid = _Process._next_pid
        _Process._next_pid += 1

    def poll(self) -> int | None:
        if self.return_code is not None:
            return self.return_code
        self.poll_count += 1
        if (
            self.exit_after_polls is not None
            and self.poll_count >= self.exit_after_polls
        ):
            self.return_code = self.exit_code
        return self.return_code

    def send_signal(self, signal_number: int) -> None:
        self.signal_order.append((self.service, signal_number))
        self.return_code = -signal_number


def _profile(
    tmp_path: Path,
    *,
    mode: str,
    manage_mission_gateway: bool = False,
) -> tuple[Path, Path, Path]:
    setup = tmp_path / "setup.bash"
    setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    data_dir = tmp_path / "robot-data"
    data_dir.mkdir()
    output_root = tmp_path / "deployments"
    profile = tmp_path / "robot.toml"
    server = (
        """
[server]
host = "127.0.0.1"
port = 8766
data_dir = "mission-data"
""".strip()
        if manage_mission_gateway
        else ""
    )
    profile.write_text(
        f"""
[robot]
id = "test-robot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

{server}

[deployment]
id = "test-robot"
mode = "{mode}"
output_root = "{output_root}"

[deployment.ros1]
distro = "noetic"
setup_files = ["{setup}"]

[plugins]
paths = ["{plugin}"]
selected = ["example.runtime"]
""".strip(),
        encoding="utf-8",
    )
    release = output_root / "test-robot" / "releases" / "fingerprint-1"
    bin_dir = release / "bin"
    bin_dir.mkdir(parents=True)
    names = ["fireclaw-bringup", "fireclaw-gateway"]
    if manage_mission_gateway:
        names.append("fireclaw-mission-gateway")
    for name in names:
        wrapper = bin_dir / name
        wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
    return profile, release, data_dir


class _StatusProbe:
    def __init__(self, release: Path, runtime_statuses: list[str]) -> None:
        self.release = release
        self.runtime_statuses = list(runtime_statuses)
        self.calls: list[bool] = []

    def __call__(self, profile_path, *, output_root, check_runtime):
        self.calls.append(check_runtime)
        if not check_runtime:
            return {"status": "installed", "release_dir": str(self.release)}
        status = (
            self.runtime_statuses.pop(0)
            if len(self.runtime_statuses) > 1
            else self.runtime_statuses[0]
        )
        return {"status": status, "release_dir": str(self.release)}


def _settings(*, restarts: int = 0) -> RuntimeSupervisorSettings:
    return RuntimeSupervisorSettings(
        runtime_ready_timeout_seconds=0.3,
        gateway_ready_timeout_seconds=0.3,
        mission_gateway_ready_timeout_seconds=0.3,
        readiness_monitor_interval_seconds=0.1,
        probe_interval_seconds=0.1,
        monitor_interval_seconds=0.1,
        shutdown_timeout_seconds=0.2,
        terminate_timeout_seconds=0.1,
        simulation_restart_limit=restarts,
        restart_backoff_seconds=0.1,
        readiness_failure_limit=2,
    )


def test_real_runtime_failure_never_restarts_and_persists_freeze(tmp_path):
    profile, release, data_dir = _profile(tmp_path, mode="real")
    clock = _Clock()
    status = _StatusProbe(release, ["installed_not_ready", "ready"])
    starts: list[str] = []
    signals: list[tuple[str, int]] = []

    def factory(
        service: str,
        argv: tuple[str, ...],
        output: TextIO,
        cwd: Path,
    ) -> _Process:
        starts.append(service)
        return _Process(
            service,
            signals,
            exit_after_polls=3 if service == "gateway" else None,
        )

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(restarts=5),
        status_probe=status,
        health_probe=lambda: {
            "status": "ok",
            "robot_id": "test-robot",
            "dry_run": False,
        },
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "gateway_exited"
    assert result["generation_count"] == 1
    assert result["automatic_restart_allowed"] is False
    assert starts == ["bringup", "gateway"]
    assert signals == [("bringup", signal.SIGINT)]
    state_path = data_dir / "memory-runtime.sqlite3"
    admission = SqliteResourceLeaseManager(
        SqliteAuthoritativeRuntimeStore(state_path)
    ).admission_snapshot()
    assert admission["closed"] is True
    assert admission["reason"] == "managed_runtime_failure"


def test_controlled_stop_uses_reverse_order(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    clock = _Clock()
    signals: list[tuple[str, int]] = []
    starts: list[str] = []

    def factory(service, argv, output, cwd):
        starts.append(service)
        return _Process(service, signals)

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(release, ["installed_not_ready", "ready"]),
        health_probe=lambda: _request_stop_after_health(supervisor),
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "stopped"
    assert starts == ["bringup", "gateway"]
    assert [item[0] for item in signals] == ["gateway", "bringup"]
    assert all(item[1] == signal.SIGINT for item in signals)
    inspected = inspect_runtime_supervisor_state(profile)
    assert inspected["status"] == "stopped"
    assert inspected["last_event"]["event_type"] == "supervisor.finished"
    assert inspected["log_dir"] == result["log_dir"]


def test_simulation_restarts_complete_generation_with_bound(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    clock = _Clock()
    starts: list[str] = []
    signals: list[tuple[str, int]] = []
    gateway_generation = 0
    health_calls = 0

    def factory(service, argv, output, cwd):
        nonlocal gateway_generation
        starts.append(service)
        if service == "gateway":
            gateway_generation += 1
        return _Process(
            service,
            signals,
            exit_after_polls=(3 if service == "gateway" and gateway_generation == 1 else None),
        )

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(restarts=1),
        status_probe=_StatusProbe(release, ["installed_not_ready", "ready"]),
        health_probe=lambda: health(),
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    def health():
        nonlocal health_calls
        health_calls += 1
        if health_calls == 2:
            supervisor.request_stop("test_complete")
        return {
            "status": "ok",
            "robot_id": "test-robot",
            "dry_run": True,
        }

    result = supervisor.run()

    assert result["status"] == "stopped"
    assert result["generation_count"] == 2
    assert starts == ["bringup", "gateway", "bringup", "gateway"]


def test_runtime_readiness_timeout_never_starts_gateway(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    clock = _Clock()
    starts: list[str] = []
    signals: list[tuple[str, int]] = []

    def factory(service, argv, output, cwd):
        starts.append(service)
        return _Process(service, signals)

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(release, ["installed_not_ready"]),
        health_probe=lambda: pytest.fail("Gateway health must not be probed"),
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "runtime_readiness_timeout"
    assert starts == ["bringup"]
    assert signals == [("bringup", signal.SIGINT)]


def test_managed_mission_gateway_starts_last_and_stops_first(tmp_path):
    profile, release, _ = _profile(
        tmp_path,
        mode="simulation",
        manage_mission_gateway=True,
    )
    clock = _Clock()
    starts: list[str] = []
    signals: list[tuple[str, int]] = []

    def factory(service, argv, output, cwd):
        starts.append(service)
        return _Process(service, signals)

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(release, ["installed_not_ready", "ready"]),
        health_probe=lambda: {
            "status": "ok",
            "robot_id": "test-robot",
            "dry_run": True,
        },
        mission_health_probe=lambda: _request_stop_after_mission_health(
            supervisor
        ),
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "stopped"
    assert starts == ["bringup", "gateway", "mission_gateway"]
    assert [item[0] for item in signals] == [
        "mission_gateway",
        "gateway",
        "bringup",
    ]
    assert all(item[1] == signal.SIGINT for item in signals)


def test_mission_gateway_crash_is_a_generation_failure(tmp_path):
    profile, release, _ = _profile(
        tmp_path,
        mode="simulation",
        manage_mission_gateway=True,
    )
    clock = _Clock()
    starts: list[str] = []
    signals: list[tuple[str, int]] = []

    def factory(service, argv, output, cwd):
        starts.append(service)
        return _Process(
            service,
            signals,
            exit_after_polls=3 if service == "mission_gateway" else None,
        )

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(release, ["installed_not_ready", "ready"]),
        health_probe=lambda: {
            "status": "ok",
            "robot_id": "test-robot",
            "dry_run": True,
        },
        mission_health_probe=lambda: {
            "status": "ok",
            "service": "mission_gateway",
        },
        process_factory=factory,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "mission_gateway_exited"
    assert starts == ["bringup", "gateway", "mission_gateway"]
    assert [item[0] for item in signals] == ["gateway", "bringup"]


def test_runtime_readiness_loss_after_start_fails_the_generation(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    clock = _Clock()
    signals: list[tuple[str, int]] = []

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(
            release,
            ["installed_not_ready", "ready", "installed_not_ready"],
        ),
        health_probe=lambda: {
            "status": "ok",
            "robot_id": "test-robot",
            "dry_run": True,
        },
        process_factory=lambda service, argv, output, cwd: _Process(
            service,
            signals,
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "runtime_readiness_lost"
    assert result["outcome"]["consecutive_failure_count"] == 2
    assert [item[0] for item in signals] == ["gateway", "bringup"]


def test_existing_ready_runtime_domain_is_rejected_before_spawn(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    starts: list[str] = []

    def ready_domain(profile_path, *, output_root, check_runtime):
        if not check_runtime:
            return {"status": "installed", "release_dir": str(release)}
        return {
            "status": "ready",
            "release_dir": str(release),
            "runtime_checks": {
                "ok": True,
                "checks": [
                    {
                        "kind": "ros1_node",
                        "target": "/move_base",
                        "ok": True,
                        "detail": None,
                    }
                ],
            },
        }

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(restarts=2),
        status_probe=ready_domain,
        health_probe=lambda: pytest.fail("Gateway health must not be probed"),
        process_factory=lambda service, argv, output, cwd: starts.append(service),
    )

    result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "runtime_domain_in_use"
    assert result["generation_count"] == 0
    assert result["automatic_restart_allowed"] is False
    assert starts == []


def test_ready_status_without_live_ros_evidence_does_not_block_start(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    clock = _Clock()
    starts: list[str] = []
    signals: list[tuple[str, int]] = []

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=_StatusProbe(release, ["ready"]),
        health_probe=lambda: _request_stop_after_health(supervisor),
        process_factory=lambda service, argv, output, cwd: (
            starts.append(service) or _Process(service, signals)
        ),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    result = supervisor.run()

    assert result["status"] == "stopped"
    assert starts == ["bringup", "gateway"]


def test_responsive_partial_ros_domain_is_rejected_before_spawn(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")

    def status_probe(profile_path, *, output_root, check_runtime):
        if not check_runtime:
            return {"status": "installed", "release_dir": str(release)}
        return {
            "status": "installed_not_ready",
            "release_dir": str(release),
            "runtime_checks": {
                "ok": False,
                "checks": [
                    {
                        "kind": "ros1_node",
                        "target": "/move_base",
                        "ok": False,
                        "detail": None,
                    }
                ],
            },
        }

    supervisor = RuntimeSupervisor(
        profile,
        settings=_settings(),
        status_probe=status_probe,
        health_probe=lambda: pytest.fail("Gateway health must not be probed"),
        process_factory=lambda *args: pytest.fail("No process may be spawned"),
    )

    result = supervisor.run()

    assert result["reason_code"] == "runtime_domain_in_use"
    assert result["outcome"]["evidence"]["responsive_check_count"] == 1


def test_instance_lock_rejects_a_second_supervisor_owner(tmp_path):
    profile, release, _ = _profile(tmp_path, mode="simulation")
    state_root = release.parents[1] / "state"
    state_root.mkdir(parents=True)
    lock_path = state_root / "runtime-supervisor.lock"
    lock_path.write_text('{"pid": 4242}\n', encoding="utf-8")

    with lock_path.open("r+", encoding="utf-8") as owner:
        fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        supervisor = RuntimeSupervisor(
            profile,
            settings=_settings(),
            status_probe=_StatusProbe(
                release,
                ["installed_not_ready", "ready"],
            ),
            health_probe=lambda: pytest.fail("Gateway health must not be probed"),
            process_factory=lambda *args: pytest.fail("No process may be spawned"),
        )

        result = supervisor.run()

    assert result["status"] == "failed"
    assert result["reason_code"] == "supervisor_already_running"
    assert result["generation_count"] == 0
    assert result["outcome"]["lock_owner"]["pid"] == 4242


def test_descendant_sessions_include_children_that_created_new_sessions():
    table = {
        100: _ProcessIdentity(100, 50, 100, 100, "S"),
        101: _ProcessIdentity(101, 100, 101, 101, "S"),
        102: _ProcessIdentity(102, 101, 101, 101, "S"),
        103: _ProcessIdentity(103, 100, 103, 103, "S"),
        200: _ProcessIdentity(200, 50, 200, 200, "S"),
    }

    sessions = _descendant_sessions(100, table)

    assert sessions == {100, 101, 103}
    assert [
        item.pid for item in _processes_in_sessions(sessions, table)
    ] == [100, 101, 102, 103]


def test_process_session_members_ignore_zombies():
    table = {
        100: _ProcessIdentity(100, 1, 100, 100, "Z"),
        101: _ProcessIdentity(101, 1, 101, 100, "S"),
    }

    assert [
        item.pid for item in _processes_in_sessions({100}, table)
    ] == [101]


def test_supervisor_settings_reject_invalid_restart_limit():
    with pytest.raises(ValueError, match="simulation_restart_limit"):
        RuntimeSupervisorSettings(simulation_restart_limit=-1)
    with pytest.raises(ValueError, match="simulation_restart_limit"):
        RuntimeSupervisorSettings(simulation_restart_limit=11)


def test_supervisor_settings_reject_non_finite_timeout():
    with pytest.raises(ValueError, match="gateway_ready_timeout_seconds"):
        RuntimeSupervisorSettings(gateway_ready_timeout_seconds=float("nan"))


def test_supervisor_settings_reject_invalid_readiness_failure_limit():
    with pytest.raises(ValueError, match="readiness_failure_limit"):
        RuntimeSupervisorSettings(readiness_failure_limit=0)


def _request_stop_after_health(supervisor: RuntimeSupervisor) -> dict[str, object]:
    supervisor.request_stop("test_complete")
    return {
        "status": "ok",
        "robot_id": "test-robot",
        "dry_run": True,
    }


def _request_stop_after_mission_health(
    supervisor: RuntimeSupervisor,
) -> dict[str, object]:
    supervisor.request_stop("test_complete")
    return {
        "status": "ok",
        "service": "mission_gateway",
    }
