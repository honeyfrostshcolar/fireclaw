"""Managed lifecycle for ROS bringup and both FireClaw Gateways.

The supervisor owns only trusted, deployment-generated wrappers.  It starts
them in dependency order, waits for explicit readiness, and stops them in
reverse order.  Simulation may restart the complete generation a bounded
number of times; real deployments deliberately require operator intervention
after any unexpected process exit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Protocol, TextIO
from uuid import uuid4

from fireclaw_core.agent.robot_registry import RobotRegistryEntry
from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment.deployer import (
    DeploymentError,
    inspect_deployment_status,
)
from fireclaw_core.deployment.profile import load_runtime_deployment_profile
from fireclaw_core.gateway.transport import GatewayTlsClientConfig
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient
from fireclaw_core.subagent.subagent_client import RobotSubagentClient


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def send_signal(self, signal_number: int) -> None: ...


StatusProbe = Callable[..., dict[str, Any]]
HealthProbe = Callable[[], dict[str, Any]]
ProcessFactory = Callable[
    [str, Sequence[str], TextIO, Path],
    ManagedProcess,
]
MAX_LIFECYCLE_AUDIT_BYTES = 1024 * 1024
MAX_SUPERVISOR_LOCK_BYTES = 4096
MAX_OWNED_PROCESS_SESSIONS = 256
NON_RESTARTABLE_FAILURES = {
    "supervisor_process_stop_failed",
    "supervisor_process_tree_cleanup_failed",
    "supervisor_process_tree_tracking_failed",
}


@dataclass(frozen=True)
class _ProcessIdentity:
    pid: int
    ppid: int
    process_group: int
    session: int
    state: str


@dataclass(frozen=True)
class RuntimeSupervisorSettings:
    runtime_ready_timeout_seconds: float = 120.0
    gateway_ready_timeout_seconds: float = 30.0
    mission_gateway_ready_timeout_seconds: float = 30.0
    readiness_monitor_interval_seconds: float = 5.0
    probe_interval_seconds: float = 1.0
    monitor_interval_seconds: float = 0.25
    shutdown_timeout_seconds: float = 15.0
    terminate_timeout_seconds: float = 3.0
    simulation_restart_limit: int = 2
    restart_backoff_seconds: float = 2.0
    readiness_failure_limit: int = 3

    def __post_init__(self) -> None:
        positive = {
            "runtime_ready_timeout_seconds": self.runtime_ready_timeout_seconds,
            "gateway_ready_timeout_seconds": self.gateway_ready_timeout_seconds,
            "mission_gateway_ready_timeout_seconds": (
                self.mission_gateway_ready_timeout_seconds
            ),
            "readiness_monitor_interval_seconds": (
                self.readiness_monitor_interval_seconds
            ),
            "probe_interval_seconds": self.probe_interval_seconds,
            "monitor_interval_seconds": self.monitor_interval_seconds,
            "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
            "terminate_timeout_seconds": self.terminate_timeout_seconds,
            "restart_backoff_seconds": self.restart_backoff_seconds,
        }
        for name, value in positive.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if (
            isinstance(self.simulation_restart_limit, bool)
            or not isinstance(self.simulation_restart_limit, int)
            or self.simulation_restart_limit < 0
            or self.simulation_restart_limit > 10
        ):
            raise ValueError(
                "simulation_restart_limit must be an integer between 0 and 10"
            )
        if (
            isinstance(self.readiness_failure_limit, bool)
            or not isinstance(self.readiness_failure_limit, int)
            or self.readiness_failure_limit < 1
            or self.readiness_failure_limit > 20
        ):
            raise ValueError(
                "readiness_failure_limit must be an integer between 1 and 20"
            )


class RuntimeSupervisor:
    """Own and monitor one generated FireClaw runtime generation."""

    def __init__(
        self,
        profile_path: str | Path,
        *,
        output_root: str | Path | None = None,
        settings: RuntimeSupervisorSettings | None = None,
        status_probe: StatusProbe = inspect_deployment_status,
        health_probe: HealthProbe | None = None,
        mission_health_probe: HealthProbe | None = None,
        process_factory: ProcessFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        out: TextIO = sys.stderr,
    ) -> None:
        self.profile = load_runtime_deployment_profile(
            profile_path,
            output_root=output_root,
        )
        self.profile_path = self.profile.profile_path
        self.robot_profile = load_robot_capability_profile(self.profile_path)
        self.output_root = output_root
        self.settings = settings or RuntimeSupervisorSettings()
        self._status_probe = status_probe
        self._health_probe = health_probe or self._build_default_health_probe()
        self._mission_health_probe = (
            mission_health_probe or self._build_default_mission_health_probe()
            if self.profile.mission_gateway.enabled
            else None
        )
        self._manage_process_trees = process_factory is None
        self._process_factory = process_factory or _spawn_subprocess
        self._monotonic = monotonic
        self._sleep = sleep
        self._out = out
        self._stop_event = threading.Event()
        self._stop_reason = "operator_requested"
        self._release: Path | None = None
        self._run_dir: Path | None = None
        self._audit: TextIO | None = None
        self._process_logs: list[TextIO] = []
        self._instance_lock_fd: int | None = None
        self._owned_process_sessions: dict[int, set[int]] = {}

    def request_stop(
        self,
        reason: str = "operator_requested",
        *,
        record: bool = True,
    ) -> None:
        self._stop_reason = reason.strip() or "operator_requested"
        self._stop_event.set()
        if record and self._audit is not None:
            self._record("supervisor.stop_requested", reason=self._stop_reason)

    def run(self) -> dict[str, Any]:
        static = self._probe_status(check_runtime=False)
        if static.get("status") != "installed":
            raise DeploymentError(
                "managed runtime requires an installed, integrity-checked release",
                code=f"supervisor_deployment_{static.get('status') or 'invalid'}",
            )
        self._release = self._validate_release(static)
        self._run_dir = self._create_run_dir()
        self._audit = _open_exclusive_text(self._run_dir / "lifecycle.jsonl")
        self._record(
            "supervisor.started",
            deployment_id=self.profile.deployment_id,
            mode=self.profile.mode,
            release_dir=str(self._release),
            log_dir=str(self._run_dir),
        )

        try:
            lock_owner = self._acquire_instance_lock()
            if lock_owner is not None:
                final = self._result(
                    status="failed",
                    reason_code="supervisor_already_running",
                    generation=0,
                    outcome={
                        "status": "failed",
                        "reason_code": "supervisor_already_running",
                        "lock_owner": lock_owner,
                    },
                )
                self._record(
                    "supervisor.start_rejected",
                    reason_code="supervisor_already_running",
                    lock_owner=lock_owner,
                )
                self._record(
                    "supervisor.finished",
                    status=final["status"],
                    reason_code=final["reason_code"],
                    generation_count=0,
                )
                return final

            occupied = self._probe_existing_runtime_domain()
            if occupied is not None:
                final = self._result(
                    status="failed",
                    reason_code="runtime_domain_in_use",
                    generation=0,
                    outcome={
                        "status": "failed",
                        "reason_code": "runtime_domain_in_use",
                        "evidence": occupied,
                    },
                )
                self._record(
                    "supervisor.start_rejected",
                    reason_code="runtime_domain_in_use",
                    evidence=occupied,
                )
                self._record(
                    "supervisor.finished",
                    status=final["status"],
                    reason_code=final["reason_code"],
                    generation_count=0,
                )
                return final

            generation = 1
            while True:
                try:
                    outcome = self._run_generation(generation)
                except DeploymentError as exc:
                    outcome = {
                        "status": "failed",
                        "reason_code": exc.code,
                        "message": str(exc),
                    }
                    self._record(
                        "supervisor.generation_failed",
                        generation=generation,
                        reason_code=exc.code,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                safety_freeze = self._freeze_real_admission(
                    reason=(
                        "managed_runtime_shutdown_unconfirmed"
                        if outcome["status"] == "stopped" or self._stop_event.is_set()
                        else "managed_runtime_failure"
                    )
                )
                if safety_freeze is not None:
                    outcome = {**outcome, "safety_freeze": safety_freeze}
                    if safety_freeze.get("ok") is not True:
                        final = self._result(
                            status="failed",
                            reason_code="real_runtime_safety_freeze_failed",
                            generation=generation,
                            outcome=outcome,
                        )
                        break
                if outcome["status"] == "stopped" or self._stop_event.is_set():
                    final = self._result(
                        status="stopped",
                        reason_code=self._stop_reason,
                        generation=generation,
                        outcome=outcome,
                    )
                    break
                can_restart = (
                    self.profile.mode == "simulation"
                    and generation <= self.settings.simulation_restart_limit
                    and outcome.get("reason_code") not in NON_RESTARTABLE_FAILURES
                )
                if not can_restart:
                    final = self._result(
                        status="failed",
                        reason_code=str(outcome["reason_code"]),
                        generation=generation,
                        outcome=outcome,
                    )
                    break
                delay = self.settings.restart_backoff_seconds * (2 ** (generation - 1))
                self._record(
                    "supervisor.restart_scheduled",
                    generation=generation,
                    next_generation=generation + 1,
                    delay_seconds=delay,
                    reason_code=outcome["reason_code"],
                )
                if not self._sleep_interruptibly(delay):
                    final = self._result(
                        status="stopped",
                        reason_code=self._stop_reason,
                        generation=generation,
                        outcome=outcome,
                    )
                    break
                generation += 1
            self._record(
                "supervisor.finished",
                status=final["status"],
                reason_code=final["reason_code"],
                generation_count=final["generation_count"],
            )
            return final
        finally:
            for handle in self._process_logs:
                handle.close()
            if self._audit is not None:
                self._audit.close()
            self._release_instance_lock()

    def _run_generation(self, generation: int) -> dict[str, Any]:
        assert self._release is not None
        bringup: ManagedProcess | None = None
        gateway: ManagedProcess | None = None
        mission_gateway: ManagedProcess | None = None
        outcome: dict[str, Any]
        try:
            bringup = self._spawn(
                "bringup",
                self._release / "bin" / "fireclaw-bringup",
                generation,
            )
            outcome = self._wait_for_runtime_ready(bringup, generation)
            if outcome["status"] != "ready":
                return outcome

            gateway = self._spawn(
                "gateway",
                self._release / "bin" / "fireclaw-gateway",
                generation,
            )
            outcome = self._wait_for_gateway_ready(
                bringup,
                gateway,
                generation,
            )
            if outcome["status"] != "ready":
                return outcome

            if self.profile.mission_gateway.enabled:
                mission_gateway = self._spawn(
                    "mission_gateway",
                    self._release / "bin" / "fireclaw-mission-gateway",
                    generation,
                )
                outcome = self._wait_for_mission_gateway_ready(
                    bringup,
                    gateway,
                    mission_gateway,
                    generation,
                )
                if outcome["status"] != "ready":
                    return outcome

            self._track_process_tree(bringup)
            self._record("supervisor.generation_ready", generation=generation)
            next_readiness_probe = (
                self._monotonic()
                + self.settings.readiness_monitor_interval_seconds
            )
            readiness_failures = {
                "runtime": 0,
                "gateway": 0,
                "mission_gateway": 0,
            }
            while not self._stop_event.is_set():
                bringup_code = bringup.poll()
                if bringup_code is not None:
                    self._record(
                        "process.exited",
                        service="bringup",
                        generation=generation,
                        exit_code=bringup_code,
                        expected=False,
                    )
                    return {
                        "status": "failed",
                        "reason_code": "bringup_exited",
                        "service": "bringup",
                        "exit_code": bringup_code,
                    }
                gateway_code = gateway.poll()
                if gateway_code is not None:
                    self._record(
                        "process.exited",
                        service="gateway",
                        generation=generation,
                        exit_code=gateway_code,
                        expected=False,
                    )
                    return {
                        "status": "failed",
                        "reason_code": "gateway_exited",
                        "service": "gateway",
                        "exit_code": gateway_code,
                    }
                if mission_gateway is not None:
                    mission_gateway_code = mission_gateway.poll()
                    if mission_gateway_code is not None:
                        self._record(
                            "process.exited",
                            service="mission_gateway",
                            generation=generation,
                            exit_code=mission_gateway_code,
                            expected=False,
                        )
                        return {
                            "status": "failed",
                            "reason_code": "mission_gateway_exited",
                            "service": "mission_gateway",
                            "exit_code": mission_gateway_code,
                        }
                if self._monotonic() >= next_readiness_probe:
                    self._track_process_tree(bringup)
                    self._track_process_tree(gateway)
                    if mission_gateway is not None:
                        self._track_process_tree(mission_gateway)
                    readiness_outcome = self._monitor_generation_readiness(
                        generation=generation,
                        readiness_failures=readiness_failures,
                    )
                    if readiness_outcome is not None:
                        return readiness_outcome
                    next_readiness_probe = (
                        self._monotonic()
                        + self.settings.readiness_monitor_interval_seconds
                    )
                self._sleep(self.settings.monitor_interval_seconds)
            return {
                "status": "stopped",
                "reason_code": self._stop_reason,
            }
        except DeploymentError as exc:
            self._record(
                "supervisor.generation_failed",
                generation=generation,
                reason_code=exc.code,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            return {
                "status": "failed",
                "reason_code": exc.code,
                "message": str(exc),
            }
        except Exception as exc:
            self._record(
                "supervisor.generation_failed",
                generation=generation,
                reason_code="process_start_failed",
                error_type=type(exc).__name__,
                message=str(exc),
            )
            return {
                "status": "failed",
                "reason_code": "process_start_failed",
                "message": str(exc),
            }
        finally:
            cleanup_errors: list[DeploymentError] = []
            for service, process in (
                ("mission_gateway", mission_gateway),
                ("gateway", gateway),
                ("bringup", bringup),
            ):
                if process is None:
                    continue
                try:
                    self._stop_process(service, process, generation)
                except DeploymentError as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                raise cleanup_errors[0]

    def _wait_for_runtime_ready(
        self,
        bringup: ManagedProcess,
        generation: int,
    ) -> dict[str, Any]:
        deadline = self._monotonic() + self.settings.runtime_ready_timeout_seconds
        previous: tuple[Any, Any] | None = None
        while self._monotonic() < deadline:
            self._track_process_tree(bringup)
            if self._stop_event.is_set():
                return {"status": "stopped", "reason_code": self._stop_reason}
            exit_code = bringup.poll()
            if exit_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "bringup_exited_before_ready",
                    "service": "bringup",
                    "exit_code": exit_code,
                }
            try:
                status = self._probe_status(check_runtime=True)
            except (DeploymentError, OSError, ValueError) as exc:
                status = {
                    "status": "error",
                    "code": getattr(exc, "code", "runtime_probe_failed"),
                }
            signature = (status.get("status"), status.get("code"))
            if signature != previous:
                self._record(
                    "runtime.readiness",
                    generation=generation,
                    status=status.get("status"),
                    reason_code=status.get("code"),
                )
                previous = signature
            if status.get("status") == "ready":
                return {"status": "ready", "reason_code": "runtime_ready"}
            self._sleep(self.settings.probe_interval_seconds)
        return {
            "status": "failed",
            "reason_code": "runtime_readiness_timeout",
            "service": "bringup",
        }

    def _wait_for_gateway_ready(
        self,
        bringup: ManagedProcess,
        gateway: ManagedProcess,
        generation: int,
    ) -> dict[str, Any]:
        deadline = self._monotonic() + self.settings.gateway_ready_timeout_seconds
        last_error: str | None = None
        expected_dry_run = self.profile.mode != "real"
        while self._monotonic() < deadline:
            self._track_process_tree(bringup)
            self._track_process_tree(gateway)
            if self._stop_event.is_set():
                return {"status": "stopped", "reason_code": self._stop_reason}
            bringup_code = bringup.poll()
            if bringup_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "bringup_exited_during_gateway_start",
                    "service": "bringup",
                    "exit_code": bringup_code,
                }
            gateway_code = gateway.poll()
            if gateway_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "gateway_exited_before_ready",
                    "service": "gateway",
                    "exit_code": gateway_code,
                }
            try:
                health = self._health_probe()
                robot_matches = (
                    self.profile.robot_id is None
                    or health.get("robot_id") == self.profile.robot_id
                )
                if (
                    health.get("status") == "ok"
                    and health.get("dry_run") is expected_dry_run
                    and robot_matches
                ):
                    self._record(
                        "gateway.ready",
                        generation=generation,
                        robot_id=health.get("robot_id"),
                        dry_run=health.get("dry_run"),
                    )
                    return {"status": "ready", "reason_code": "gateway_ready"}
                last_error = "Gateway health identity or execution mode mismatch"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            self._sleep(self.settings.probe_interval_seconds)
        self._record(
            "gateway.readiness_timeout",
            generation=generation,
            message=last_error,
        )
        return {
            "status": "failed",
            "reason_code": "gateway_readiness_timeout",
            "service": "gateway",
            "message": last_error,
        }

    def _wait_for_mission_gateway_ready(
        self,
        bringup: ManagedProcess,
        gateway: ManagedProcess,
        mission_gateway: ManagedProcess,
        generation: int,
    ) -> dict[str, Any]:
        deadline = (
            self._monotonic()
            + self.settings.mission_gateway_ready_timeout_seconds
        )
        last_error: str | None = None
        while self._monotonic() < deadline:
            self._track_process_tree(bringup)
            self._track_process_tree(gateway)
            self._track_process_tree(mission_gateway)
            if self._stop_event.is_set():
                return {"status": "stopped", "reason_code": self._stop_reason}
            bringup_code = bringup.poll()
            if bringup_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "bringup_exited_during_mission_gateway_start",
                    "service": "bringup",
                    "exit_code": bringup_code,
                }
            gateway_code = gateway.poll()
            if gateway_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "gateway_exited_during_mission_gateway_start",
                    "service": "gateway",
                    "exit_code": gateway_code,
                }
            mission_gateway_code = mission_gateway.poll()
            if mission_gateway_code is not None:
                return {
                    "status": "failed",
                    "reason_code": "mission_gateway_exited_before_ready",
                    "service": "mission_gateway",
                    "exit_code": mission_gateway_code,
                }
            try:
                if self._mission_health_probe is None:
                    raise RuntimeError("Mission Gateway health probe is unavailable")
                health = self._mission_health_probe()
                if (
                    health.get("status") == "ok"
                    and health.get("service") == "mission_gateway"
                ):
                    self._record(
                        "mission_gateway.ready",
                        generation=generation,
                        service=health.get("service"),
                    )
                    return {
                        "status": "ready",
                        "reason_code": "mission_gateway_ready",
                    }
                last_error = "Mission Gateway health identity mismatch"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            self._sleep(self.settings.probe_interval_seconds)
        self._record(
            "mission_gateway.readiness_timeout",
            generation=generation,
            message=last_error,
        )
        return {
            "status": "failed",
            "reason_code": "mission_gateway_readiness_timeout",
            "service": "mission_gateway",
            "message": last_error,
        }

    def _monitor_generation_readiness(
        self,
        *,
        generation: int,
        readiness_failures: dict[str, int],
    ) -> dict[str, Any] | None:
        checks: list[tuple[str, bool, str | None]] = []
        try:
            runtime = self._probe_status(check_runtime=True)
            runtime_ok = runtime.get("status") == "ready"
            runtime_detail = str(
                runtime.get("code") or runtime.get("status") or "unknown"
            )
        except (DeploymentError, OSError, ValueError) as exc:
            runtime_ok = False
            runtime_detail = f"{type(exc).__name__}: {exc}"
        checks.append(("runtime", runtime_ok, runtime_detail))

        try:
            health = self._health_probe()
            expected_dry_run = self.profile.mode != "real"
            gateway_ok = (
                health.get("status") == "ok"
                and health.get("dry_run") is expected_dry_run
                and (
                    self.profile.robot_id is None
                    or health.get("robot_id") == self.profile.robot_id
                )
            )
            gateway_detail = (
                None
                if gateway_ok
                else "Gateway health identity or execution mode mismatch"
            )
        except Exception as exc:
            gateway_ok = False
            gateway_detail = f"{type(exc).__name__}: {exc}"
        checks.append(("gateway", gateway_ok, gateway_detail))

        if self.profile.mission_gateway.enabled:
            try:
                if self._mission_health_probe is None:
                    raise RuntimeError("Mission Gateway health probe is unavailable")
                mission_health = self._mission_health_probe()
                mission_ok = (
                    mission_health.get("status") == "ok"
                    and mission_health.get("service") == "mission_gateway"
                )
                mission_detail = (
                    None
                    if mission_ok
                    else "Mission Gateway health identity mismatch"
                )
            except Exception as exc:
                mission_ok = False
                mission_detail = f"{type(exc).__name__}: {exc}"
            checks.append(("mission_gateway", mission_ok, mission_detail))

        for service, ready, detail in checks:
            previous = readiness_failures[service]
            if ready:
                readiness_failures[service] = 0
                if previous:
                    self._record(
                        "service.readiness_recovered",
                        service=service,
                        generation=generation,
                        previous_failure_count=previous,
                    )
                continue
            current = previous + 1
            readiness_failures[service] = current
            self._record(
                "service.readiness_degraded",
                service=service,
                generation=generation,
                consecutive_failure_count=current,
                failure_limit=self.settings.readiness_failure_limit,
                message=detail,
            )
            if current >= self.settings.readiness_failure_limit:
                reason_service = (
                    "runtime" if service == "runtime" else service
                )
                return {
                    "status": "failed",
                    "reason_code": f"{reason_service}_readiness_lost",
                    "service": service,
                    "consecutive_failure_count": current,
                    "message": detail,
                }
        return None

    def _spawn(
        self,
        service: str,
        executable: Path,
        generation: int,
    ) -> ManagedProcess:
        assert self._release is not None and self._run_dir is not None
        resolved = executable.resolve(strict=True)
        try:
            resolved.relative_to(self._release)
        except ValueError as exc:
            raise DeploymentError(
                "generated runtime wrapper escapes the immutable release",
                code="supervisor_wrapper_invalid",
            ) from exc
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise DeploymentError(
                f"generated runtime wrapper is not executable: {service}",
                code="supervisor_wrapper_invalid",
            )
        log_path = self._run_dir / f"{generation:02d}-{service}.log"
        log = _open_exclusive_text(log_path)
        self._process_logs.append(log)
        process = self._process_factory(
            service,
            (str(resolved),),
            log,
            self._release,
        )
        self._track_process_tree(process)
        self._record(
            "process.started",
            service=service,
            generation=generation,
            pid=getattr(process, "pid", None),
            log_path=str(log_path),
        )
        return process

    def _stop_process(
        self,
        service: str,
        process: ManagedProcess,
        generation: int,
    ) -> None:
        self._track_process_tree(process)
        if process.poll() is None:
            self._record(
                "process.stop_signal",
                service=service,
                generation=generation,
                signal="SIGINT",
            )
            self._send_signal(service, process, generation, signal.SIGINT)
            stopped = self._wait_for_exit(
                process,
                self.settings.shutdown_timeout_seconds,
            )
            if not stopped:
                self._record(
                    "process.stop_signal",
                    service=service,
                    generation=generation,
                    signal="SIGTERM",
                )
                self._send_signal(service, process, generation, signal.SIGTERM)
                stopped = self._wait_for_exit(
                    process,
                    self.settings.terminate_timeout_seconds,
                )
            if not stopped:
                self._record(
                    "process.stop_signal",
                    service=service,
                    generation=generation,
                    signal="SIGKILL",
                )
                self._send_signal(service, process, generation, signal.SIGKILL)
                stopped = self._wait_for_exit(
                    process,
                    self.settings.terminate_timeout_seconds,
                )
            if not stopped:
                raise DeploymentError(
                    f"managed process did not exit after SIGKILL: {service}",
                    code="supervisor_process_stop_failed",
                )
        self._drain_process_tree(service, process, generation)

    def _send_signal(
        self,
        service: str,
        process: ManagedProcess,
        generation: int,
        signal_number: int,
    ) -> None:
        try:
            _signal_process(process, signal_number)
        except OSError as exc:
            self._record(
                "process.signal_failed",
                service=service,
                generation=generation,
                signal=signal.Signals(signal_number).name,
                error_type=type(exc).__name__,
                message=str(exc),
            )

    def _wait_for_exit(self, process: ManagedProcess, timeout_seconds: float) -> bool:
        deadline = self._monotonic() + timeout_seconds
        while self._monotonic() < deadline:
            if process.poll() is not None:
                return True
            self._sleep(min(0.05, timeout_seconds))
        return process.poll() is not None

    def _track_process_tree(self, process: ManagedProcess) -> None:
        if not self._manage_process_trees:
            return
        root_pid = int(process.pid)
        table = _read_process_table()
        if root_pid not in table and process.poll() is None:
            raise DeploymentError(
                "managed process tree could not be observed in /proc",
                code="supervisor_process_tree_tracking_failed",
            )
        sessions = _descendant_sessions(root_pid, table)
        owned = self._owned_process_sessions.setdefault(root_pid, {root_pid})
        owned.update(sessions)
        if len(owned) > MAX_OWNED_PROCESS_SESSIONS:
            raise DeploymentError(
                "managed process tree exceeded the owned-session limit",
                code="supervisor_process_tree_cleanup_failed",
            )

    def _drain_process_tree(
        self,
        service: str,
        process: ManagedProcess,
        generation: int,
    ) -> None:
        if not self._manage_process_trees:
            return
        sessions = self._owned_process_sessions.get(
            int(process.pid),
            {int(process.pid)},
        )
        members = _processes_in_sessions(sessions, _read_process_table())
        if not members:
            self._owned_process_sessions.pop(int(process.pid), None)
            return
        self._record(
            "process.descendants_remaining",
            service=service,
            generation=generation,
            process_count=len(members),
            process_ids=[item.pid for item in members[:64]],
            sessions=sorted(sessions)[:64],
        )
        stages = (
            (signal.SIGINT, self.settings.shutdown_timeout_seconds),
            (signal.SIGTERM, self.settings.terminate_timeout_seconds),
            (signal.SIGKILL, self.settings.terminate_timeout_seconds),
        )
        for signal_number, timeout_seconds in stages:
            members = _processes_in_sessions(sessions, _read_process_table())
            if not members:
                self._owned_process_sessions.pop(int(process.pid), None)
                return
            groups = sorted({item.process_group for item in members})
            self._record(
                "process.descendant_stop_signal",
                service=service,
                generation=generation,
                signal=signal.Signals(signal_number).name,
                process_count=len(members),
                process_groups=groups[:64],
            )
            for process_group in groups:
                if process_group <= 0 or process_group == os.getpgrp():
                    continue
                try:
                    os.killpg(process_group, signal_number)
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    self._record(
                        "process.descendant_signal_failed",
                        service=service,
                        generation=generation,
                        signal=signal.Signals(signal_number).name,
                        process_group=process_group,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
            if self._wait_for_process_sessions(sessions, timeout_seconds):
                self._owned_process_sessions.pop(int(process.pid), None)
                self._record(
                    "process.descendants_stopped",
                    service=service,
                    generation=generation,
                )
                return
        remaining = _processes_in_sessions(sessions, _read_process_table())
        self._record(
            "process.descendant_cleanup_failed",
            service=service,
            generation=generation,
            process_count=len(remaining),
            process_ids=[item.pid for item in remaining[:64]],
            sessions=sorted(sessions)[:64],
        )
        raise DeploymentError(
            f"managed process descendants did not stop: {service}",
            code="supervisor_process_tree_cleanup_failed",
        )

    def _wait_for_process_sessions(
        self,
        sessions: set[int],
        timeout_seconds: float,
    ) -> bool:
        deadline = self._monotonic() + timeout_seconds
        while self._monotonic() < deadline:
            if not _processes_in_sessions(sessions, _read_process_table()):
                return True
            self._sleep(min(0.05, timeout_seconds))
        return not _processes_in_sessions(sessions, _read_process_table())

    def _probe_status(self, *, check_runtime: bool) -> dict[str, Any]:
        return self._status_probe(
            self.profile_path,
            output_root=self.output_root,
            check_runtime=check_runtime,
        )

    def _validate_release(self, status: Mapping[str, Any]) -> Path:
        raw = status.get("release_dir")
        if not isinstance(raw, str) or not raw.strip():
            raise DeploymentError(
                "deployment status did not identify the active release",
                code="supervisor_release_invalid",
            )
        release = Path(raw).resolve(strict=True)
        releases_root = (
            self.profile.deployment_root / "releases"
        ).resolve(strict=True)
        try:
            release.relative_to(releases_root)
        except ValueError as exc:
            raise DeploymentError(
                "active release is outside the deployment release root",
                code="supervisor_release_invalid",
            ) from exc
        return release

    def _create_run_dir(self) -> Path:
        state_root = self.profile.deployment_root / "state"
        state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved_state = state_root.resolve(strict=True)
        deployment_root = self.profile.deployment_root.resolve(strict=True)
        try:
            resolved_state.relative_to(deployment_root)
        except ValueError as exc:
            raise DeploymentError(
                "supervisor state directory escapes the deployment root",
                code="supervisor_state_invalid",
            ) from exc
        log_root = resolved_state / "runtime-supervisor"
        log_root.mkdir(mode=0o700, exist_ok=True)
        resolved_log_root = log_root.resolve(strict=True)
        try:
            resolved_log_root.relative_to(resolved_state)
        except ValueError as exc:
            raise DeploymentError(
                "supervisor log directory escapes the deployment state root",
                code="supervisor_state_invalid",
            ) from exc
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = resolved_log_root / f"run-{timestamp}-{uuid4().hex[:8]}"
        run_dir.mkdir(mode=0o700)
        return run_dir

    def _acquire_instance_lock(self) -> dict[str, Any] | None:
        """Hold one advisory lock for the complete supervisor lifetime.

        The lock protects deployments started by this supervisor.  The
        separate Runtime-domain probe below also catches healthy legacy/manual
        instances that predate the lock file.
        """

        assert self._release is not None and self._run_dir is not None
        state_root = (self.profile.deployment_root / "state").resolve(strict=True)
        lock_path = state_root / "runtime-supervisor.lock"
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise DeploymentError(
                "runtime supervisor lock could not be opened",
                code="supervisor_lock_invalid",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise DeploymentError(
                    "runtime supervisor lock must be a regular file",
                    code="supervisor_lock_invalid",
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return _read_lock_owner(descriptor)
            except OSError as exc:
                raise DeploymentError(
                    "runtime supervisor lock could not be acquired",
                    code="supervisor_lock_invalid",
                ) from exc
            os.fchmod(descriptor, 0o600)
            owner = {
                "schema_version": 1,
                "pid": os.getpid(),
                "deployment_id": self.profile.deployment_id,
                "mode": self.profile.mode,
                "release_dir": str(self._release),
                "run_dir": str(self._run_dir),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
            encoded = (
                json.dumps(owner, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            os.ftruncate(descriptor, 0)
            os.lseek(descriptor, 0, os.SEEK_SET)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
            self._instance_lock_fd = descriptor
            self._record("supervisor.lock_acquired", lock_path=str(lock_path))
            return None
        finally:
            if self._instance_lock_fd != descriptor:
                os.close(descriptor)

    def _release_instance_lock(self) -> None:
        descriptor = self._instance_lock_fd
        self._instance_lock_fd = None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _probe_existing_runtime_domain(self) -> dict[str, Any] | None:
        """Fail before spawning when this Profile's ROS domain already responds."""

        try:
            status = self._probe_status(check_runtime=True)
        except (DeploymentError, OSError, ValueError) as exc:
            raise DeploymentError(
                "runtime startup preflight could not inspect the ROS domain",
                code="supervisor_preflight_failed",
            ) from exc
        runtime_checks = status.get("runtime_checks")
        checks = (
            runtime_checks.get("checks")
            if isinstance(runtime_checks, Mapping)
            else None
        )
        responsive: list[dict[str, Any]] = []
        if isinstance(checks, list):
            for item in checks:
                if not isinstance(item, Mapping):
                    continue
                kind = item.get("kind")
                if kind not in {
                    "ros1_node",
                    "ros1_topic",
                    "ros1_action",
                    "ros1_tf",
                }:
                    continue
                graph_responded = (
                    kind in {"ros1_node", "ros1_topic", "ros1_action"}
                    and item.get("detail") is None
                )
                if item.get("ok") is True or graph_responded:
                    responsive.append(
                        {
                            "kind": kind,
                            "target": item.get("target"),
                            "ok": item.get("ok") is True,
                        }
                    )
        if not responsive:
            return None
        return {
            "deployment_status": status.get("status"),
            "responsive_check_count": len(responsive),
            "responsive_checks": responsive[:16],
        }

    def _build_default_health_probe(self) -> HealthProbe:
        base_url = self.profile.robot_base_url or "http://127.0.0.1:8765"
        entry = RobotRegistryEntry(
            robot_id=self.profile.robot_id or self.profile.deployment_id,
            base_url=base_url.rstrip("/"),
        )
        client = RobotSubagentClient(
            timeout_seconds=min(2.0, self.settings.gateway_ready_timeout_seconds),
        )
        return lambda: client.get_health(entry)

    def _build_default_mission_health_probe(self) -> HealthProbe:
        managed = self.profile.mission_gateway
        if not managed.enabled or managed.base_url is None:
            raise DeploymentError(
                "managed Mission Gateway has no health endpoint",
                code="mission_gateway_probe_invalid",
            )
        client = MissionGatewayClient(
            managed.base_url,
            timeout=min(
                2.0,
                self.settings.mission_gateway_ready_timeout_seconds,
            ),
            tls=GatewayTlsClientConfig(
                ca_file=(
                    str(managed.tls_ca_file)
                    if managed.tls_ca_file is not None
                    else None
                ),
                cert_file=(
                    str(managed.tls_client_cert_file)
                    if managed.tls_client_cert_file is not None
                    else None
                ),
                key_file=(
                    str(managed.tls_client_key_file)
                    if managed.tls_client_key_file is not None
                    else None
                ),
            ),
        )
        return client.get_health

    def _sleep_interruptibly(self, duration: float) -> bool:
        deadline = self._monotonic() + duration
        while self._monotonic() < deadline:
            if self._stop_event.is_set():
                return False
            self._sleep(
                min(
                    self.settings.monitor_interval_seconds,
                    max(0.0, deadline - self._monotonic()),
                )
            )
        return not self._stop_event.is_set()

    def _freeze_real_admission(self, *, reason: str) -> dict[str, Any] | None:
        if self.profile.mode != "real":
            return None
        from fireclaw_core.infra.runtime_state import (
            SqliteAuthoritativeRuntimeStore,
            SqliteResourceLeaseManager,
        )

        runtime_state_path = self.robot_profile.memory_path.with_name(
            f"{self.robot_profile.memory_path.stem}-runtime.sqlite3"
        )
        try:
            store = SqliteAuthoritativeRuntimeStore(runtime_state_path)
            leases = SqliteResourceLeaseManager(store)
            current = leases.admission_snapshot()
            if current.get("closed") is True:
                result = {
                    "ok": True,
                    "status": "already_frozen",
                    "reason_code": current.get("reason"),
                    "revision": current.get("revision"),
                    "runtime_state_path": str(runtime_state_path),
                }
            else:
                closed_at = datetime.now(timezone.utc).isoformat()
                revision = leases.close_admission(
                    reason=reason,
                    task_id=(
                        "runtime-supervisor:"
                        f"{self._run_dir.name if self._run_dir else 'unknown'}"
                    ),
                    closed_at=closed_at,
                )
                result = {
                    "ok": True,
                    "status": "frozen",
                    "reason_code": reason,
                    "revision": revision,
                    "closed_at": closed_at,
                    "runtime_state_path": str(runtime_state_path),
                }
        except Exception as exc:
            result = {
                "ok": False,
                "status": "freeze_failed",
                "reason_code": "runtime_state_write_failed",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "runtime_state_path": str(runtime_state_path),
            }
        self._record("resource_admission.safety_freeze", **result)
        return result

    def _record(self, event_type: str, **payload: Any) -> None:
        record = {
            "schema_version": 1,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        if self._audit is not None:
            self._audit.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            self._audit.flush()
        detail = payload.get("reason_code") or payload.get("status") or ""
        suffix = f" ({detail})" if detail else ""
        print(f"[runtime] {event_type}{suffix}", file=self._out, flush=True)

    def _result(
        self,
        *,
        status: str,
        reason_code: str,
        generation: int,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": status,
            "reason_code": reason_code,
            "deployment_id": self.profile.deployment_id,
            "mode": self.profile.mode,
            "generation_count": generation,
            "automatic_restart_allowed": (
                generation > 0
                and self.profile.mode == "simulation"
                and self.settings.simulation_restart_limit > 0
            ),
            "release_dir": str(self._release) if self._release else None,
            "log_dir": str(self._run_dir) if self._run_dir else None,
            "outcome": dict(outcome),
        }


def run_runtime_supervisor(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    settings: RuntimeSupervisorSettings | None = None,
) -> dict[str, Any]:
    supervisor = RuntimeSupervisor(
        profile_path,
        output_root=output_root,
        settings=settings,
    )
    installed_handlers: dict[int, Any] = {}

    def request_stop(signal_number: int, _frame: Any) -> None:
        supervisor.request_stop(
            signal.Signals(signal_number).name.lower(),
            record=False,
        )

    if threading.current_thread() is threading.main_thread():
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            installed_handlers[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, request_stop)
    try:
        return supervisor.run()
    finally:
        for signal_number, handler in installed_handlers.items():
            signal.signal(signal_number, handler)


def inspect_runtime_supervisor_state(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read the latest bounded lifecycle record without inferring liveness."""

    profile = load_runtime_deployment_profile(
        profile_path,
        output_root=output_root,
    )
    log_root = profile.deployment_root / "state" / "runtime-supervisor"
    if not log_root.exists():
        return {"status": "no_runs", "last_event": None, "log_dir": None}
    if log_root.is_symlink() or not log_root.is_dir():
        return {
            "status": "invalid",
            "reason_code": "supervisor_log_root_invalid",
            "last_event": None,
            "log_dir": str(log_root),
        }
    deployment_root = profile.deployment_root.resolve(strict=True)
    resolved_log_root = log_root.resolve(strict=True)
    try:
        resolved_log_root.relative_to(deployment_root)
    except ValueError:
        return {
            "status": "invalid",
            "reason_code": "supervisor_log_root_escape",
            "last_event": None,
            "log_dir": str(log_root),
        }
    runs = sorted(
        (
            item
            for item in resolved_log_root.iterdir()
            if item.name.startswith("run-")
            and item.is_dir()
            and not item.is_symlink()
        ),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
    )
    if not runs:
        return {"status": "no_runs", "last_event": None, "log_dir": None}
    latest = runs[-1]
    audit = latest / "lifecycle.jsonl"
    if audit.is_symlink() or not audit.is_file():
        return {
            "status": "invalid",
            "reason_code": "supervisor_audit_missing",
            "last_event": None,
            "log_dir": str(latest),
        }
    if audit.stat().st_size > MAX_LIFECYCLE_AUDIT_BYTES:
        return {
            "status": "invalid",
            "reason_code": "supervisor_audit_too_large",
            "last_event": None,
            "log_dir": str(latest),
        }
    try:
        lines = [
            line
            for line in audit.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        last = json.loads(lines[-1]) if lines else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        last = None
    if not isinstance(last, dict):
        return {
            "status": "invalid",
            "reason_code": "supervisor_audit_invalid",
            "last_event": None,
            "log_dir": str(latest),
        }
    return {
        "status": (
            str(last.get("status") or "finished")
            if last.get("event_type") == "supervisor.finished"
            else "running_or_interrupted"
        ),
        "reason_code": last.get("reason_code"),
        "last_event": last,
        "log_dir": str(latest),
    }


def _spawn_subprocess(
    _service: str,
    argv: Sequence[str],
    output: TextIO,
    cwd: Path,
) -> ManagedProcess:
    return subprocess.Popen(
        tuple(argv),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        cwd=str(cwd),
        start_new_session=True,
        close_fds=True,
    )


def _signal_process(process: ManagedProcess, signal_number: int) -> None:
    try:
        if isinstance(process, subprocess.Popen):
            os.killpg(process.pid, signal_number)
        else:
            process.send_signal(signal_number)
    except ProcessLookupError:
        return


def _open_exclusive_text(path: Path) -> TextIO:
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    return os.fdopen(descriptor, "w", encoding="utf-8", buffering=1)


def _read_lock_owner(descriptor: int) -> dict[str, Any]:
    try:
        raw = os.pread(descriptor, MAX_SUPERVISOR_LOCK_BYTES + 1, 0)
        if len(raw) > MAX_SUPERVISOR_LOCK_BYTES:
            raise ValueError("lock metadata exceeds the size limit")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return {"status": "locked", "metadata": "unavailable"}
    if not isinstance(payload, Mapping):
        return {"status": "locked", "metadata": "invalid"}
    owner: dict[str, Any] = {"status": "locked"}
    for key in (
        "schema_version",
        "pid",
        "deployment_id",
        "mode",
        "release_dir",
        "run_dir",
        "acquired_at",
    ):
        value = payload.get(key)
        if value is None or isinstance(value, (str, int, float, bool)):
            owner[key] = value
    return owner


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("runtime supervisor lock metadata write made no progress")
        offset += written


def _read_process_table() -> dict[int, _ProcessIdentity]:
    table: dict[int, _ProcessIdentity] = {}
    try:
        entries = tuple(Path("/proc").iterdir())
    except OSError as exc:
        raise DeploymentError(
            "runtime supervisor cannot inspect /proc",
            code="supervisor_process_tree_tracking_failed",
        ) from exc
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            closing_parenthesis = raw.rfind(")")
            if closing_parenthesis < 0:
                continue
            fields = raw[closing_parenthesis + 2 :].split()
            if len(fields) < 4:
                continue
            pid = int(entry.name)
            table[pid] = _ProcessIdentity(
                pid=pid,
                state=fields[0],
                ppid=int(fields[1]),
                process_group=int(fields[2]),
                session=int(fields[3]),
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return table


def _descendant_sessions(
    root_pid: int,
    table: Mapping[int, _ProcessIdentity],
) -> set[int]:
    children: dict[int, list[int]] = {}
    for item in table.values():
        children.setdefault(item.ppid, []).append(item.pid)
    pending = [root_pid]
    seen: set[int] = set()
    sessions = {root_pid}
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        item = table.get(pid)
        if item is not None and item.session > 0:
            sessions.add(item.session)
        pending.extend(children.get(pid, ()))
    return sessions


def _processes_in_sessions(
    sessions: set[int],
    table: Mapping[int, _ProcessIdentity],
) -> list[_ProcessIdentity]:
    current_pid = os.getpid()
    return sorted(
        (
            item
            for item in table.values()
            if item.session in sessions
            and item.pid != current_pid
            and item.state != "Z"
        ),
        key=lambda item: item.pid,
    )


__all__ = [
    "inspect_runtime_supervisor_state",
    "RuntimeSupervisor",
    "RuntimeSupervisorSettings",
    "run_runtime_supervisor",
]
