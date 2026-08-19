"""Background daemon lifecycle manager for FireClaw runtime and gateway processes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
import webbrowser

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment import load_runtime_deployment_profile
from fireclaw_core.infra.hardware_safety_acceptance import run_hardware_safety_preflight
from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root
from fireclaw_core.infra.user_setup import (
    FireClawSetupError,
    resolve_active_profile_path,
)

DAEMON_SCHEMA_VERSION = 1
DAEMON_STATE_FILENAME = "runtime-daemon.json"
DAEMON_LOG_FILENAME = "runtime-daemon.log"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8766"


class FireClawDaemonError(ValueError):
    """A daemon lifecycle failure with a structured error code and operator action."""

    def __init__(self, message: str, *, code: str, operator_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.operator_action = operator_action


@dataclass(frozen=True)
class DaemonState:
    pid: int
    pgid: int
    profile_path: Path
    mode: str
    started_at: str
    gateway_url: str
    log_path: Path
    status: str
    robot_id: str | None = None
    schema_version: int = DAEMON_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pid": self.pid,
            "pgid": self.pgid,
            "profile_path": str(self.profile_path),
            "mode": self.mode,
            "started_at": self.started_at,
            "gateway_url": self.gateway_url,
            "log_path": str(self.log_path),
            "status": self.status,
            "robot_id": self.robot_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DaemonState:
        return cls(
            pid=int(data["pid"]),
            pgid=int(data.get("pgid", data["pid"])),
            profile_path=Path(data["profile_path"]),
            mode=str(data["mode"]),
            started_at=str(data["started_at"]),
            gateway_url=str(data.get("gateway_url", DEFAULT_GATEWAY_URL)),
            log_path=Path(data.get("log_path", "")),
            status=str(data.get("status", "running")),
            robot_id=data.get("robot_id"),
            schema_version=int(data.get("schema_version", DAEMON_SCHEMA_VERSION)),
        )


def _is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive (and not a zombie)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False

    # Reap child if possible
    try:
        reaped_pid, _ = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except (ChildProcessError, OSError):
        pass

    # On Linux, check if process is a zombie (defunct)
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            stat_content = proc_stat.read_text(encoding="utf-8").split()
            # 3rd field in /proc/[pid]/stat is state (R, S, D, Z, T, etc.)
            if len(stat_content) >= 3 and stat_content[2] == "Z":
                return False
        except (OSError, IndexError):
            pass
    return True


def _ensure_private_dir(path: Path) -> None:
    """Ensure directory exists with private permissions (0700)."""
    if path.is_symlink():
        raise FireClawDaemonError(
            "Directory must not be a symbolic link.",
            code="daemon_path_unsafe",
            operator_action="移除该符号链接后重试。",
        )
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise FireClawDaemonError(
            "Path is not a directory.",
            code="daemon_path_unsafe",
            operator_action="移动冲突文件后重试。",
        )
    if created:
        try:
            path.chmod(0o700)
        except OSError:
            pass


def _atomic_replace_json(path: Path, data: Mapping[str, Any]) -> None:
    """Atomically write JSON data to file with 0600 permissions."""
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            json.dump(dict(data), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _poll_gateway_health(url: str, timeout: float, pid: int | None = None) -> bool:
    """Poll gateway /health endpoint until it responds or timeout is exceeded."""
    health_url = url.rstrip("/") + "/health"
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if pid is not None and not _is_process_alive(pid):
            return False
        try:
            req = Request(health_url, headers={"User-Agent": "FireClaw-DaemonManager"})
            with urlopen(req, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except (HTTPError, URLError, OSError, TimeoutError):
            pass
        time.sleep(0.2)
    return False


class DaemonRuntimeManager:
    """Manages background supervisor/gateway processes for an active or explicit Profile."""

    def __init__(self, *, runtime_root: str | Path | None = None) -> None:
        self.runtime_root = resolve_fireclaw_runtime_root(configured=runtime_root)
        self.state_dir = self.runtime_root / "state"
        self.state_file = self.state_dir / DAEMON_STATE_FILENAME
        self.logs_dir = self.runtime_root / "logs"
        self.log_file = self.logs_dir / DAEMON_LOG_FILENAME

    def _read_state(self) -> DaemonState | None:
        if not self.state_file.exists() or self.state_file.is_symlink() or not self.state_file.is_file():
            return None
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != DAEMON_SCHEMA_VERSION:
                return None
            return DaemonState.from_dict(raw)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None

    def _remove_state_file(self) -> None:
        if self.state_file.exists():
            try:
                self.state_file.unlink()
            except OSError:
                pass

    def get_daemon_status(self, profile_path: str | Path | None = None) -> dict[str, Any]:
        """Inspect daemon liveness and self-heal stale PID states."""
        try:
            resolved_profile = resolve_active_profile_path(profile_path, runtime_root=self.runtime_root)
        except FireClawSetupError:
            resolved_profile = Path(profile_path).expanduser() if profile_path is not None else None

        state = self._read_state()
        if state is None:
            return {
                "status": "not_running",
                "daemon": None,
                "profile_path": str(resolved_profile) if resolved_profile else None,
                "message": "FireClaw daemon is not running.",
            }

        if not _is_process_alive(state.pid):
            # Dead process detected: self-heal by cleaning up state file
            self._remove_state_file()
            return {
                "status": "not_running",
                "stale_cleaned": True,
                "previous_pid": state.pid,
                "daemon": None,
                "profile_path": str(resolved_profile) if resolved_profile else str(state.profile_path),
                "message": f"Cleaned up stale daemon PID {state.pid}.",
            }

        return {
            "status": "running",
            "daemon": state.to_dict(),
            "profile_path": str(state.profile_path),
            "message": f"Daemon is running with PID {state.pid}.",
        }

    def start_daemon(
        self,
        profile_path: str | Path | None = None,
        *,
        foreground: bool = False,
        timeout: float = 15.0,
        spawner: Callable[[list[str], Path, Path], int] | None = None,
        health_poller: Callable[[str, float], bool] | None = None,
    ) -> dict[str, Any]:
        """Start the FireClaw supervisor/gateway in background daemon mode."""
        resolved_profile = resolve_active_profile_path(profile_path, runtime_root=self.runtime_root)
        robot = load_robot_capability_profile(resolved_profile)
        deployment = load_runtime_deployment_profile(resolved_profile)

        # Real-robot hardware safety preflight check
        if deployment.mode == "real":
            preflight, _ = run_hardware_safety_preflight(resolved_profile, live=False)
            checks_passed = preflight.get("status") == "passed" if "status" in preflight else preflight.get("passed", True)
            if not checks_passed:
                raise FireClawDaemonError(
                    "Real robot hardware safety preflight failed.",
                    code="hardware_safety_preflight_blocked",
                    operator_action="运行 fireclaw hardware-safety preflight 排查安全预检项。",
                )

        # Idempotency check: already running?
        curr_status = self.get_daemon_status(resolved_profile)
        if curr_status.get("status") == "running":
            return {
                "status": "already_running",
                "daemon": curr_status["daemon"],
                "message": f"Daemon is already running with PID {curr_status['daemon']['pid']}.",
            }

        _ensure_private_dir(self.state_dir)
        _ensure_private_dir(self.logs_dir)

        gateway_url = DEFAULT_GATEWAY_URL

        if foreground:
            from fireclaw_core.deployment.supervisor import run_runtime_supervisor

            return run_runtime_supervisor(resolved_profile)

        cmd = [
            sys.executable,
            "-m",
            "fireclaw_core",
            "deploy",
            "run",
            "--profile",
            str(resolved_profile),
        ]

        if spawner is not None:
            pid = spawner(cmd, self.log_file, self.runtime_root)
            pgid = pid
        else:
            with self.log_file.open("a", encoding="utf-8") as log_handle:
                try:
                    self.log_file.chmod(0o600)
                except OSError:
                    pass
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=str(self.runtime_root),
                    close_fds=True,
                )
                pid = proc.pid
                try:
                    pgid = os.getpgid(pid)
                except OSError:
                    pgid = pid

        state = DaemonState(
            pid=pid,
            pgid=pgid,
            profile_path=resolved_profile,
            mode=deployment.mode,
            started_at=datetime.now(timezone.utc).isoformat(),
            gateway_url=gateway_url,
            log_path=self.log_file,
            status="running",
            robot_id=robot.robot_id,
        )
        _atomic_replace_json(self.state_file, state.to_dict())

        # Health polling
        poller = health_poller or (lambda url, tout: _poll_gateway_health(url, tout, pid=pid))
        health_ok = poller(gateway_url, timeout)

        if not _is_process_alive(pid):
            self._remove_state_file()
            raise FireClawDaemonError(
                f"Daemon process {pid} exited unexpectedly during startup.",
                code="daemon_start_failed",
                operator_action=f"检查守护进程日志 {self.log_file} 获取详细退出原因。",
            )

        return {
            "status": "running",
            "daemon": state.to_dict(),
            "health_verified": health_ok,
            "message": "FireClaw daemon started successfully.",
        }

    def stop_daemon(
        self,
        profile_path: str | Path | None = None,
        *,
        timeout: float = 10.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Stop the FireClaw background daemon gracefully or with SIGKILL."""
        state = self._read_state()
        if state is None or not _is_process_alive(state.pid):
            self._remove_state_file()
            return {
                "status": "not_running",
                "message": "FireClaw daemon is not running.",
            }

        pid = state.pid
        pgid = state.pgid

        # Send termination signal to process group
        target_sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(pgid, target_sig)
        except (ProcessLookupError, OSError):
            try:
                os.kill(pid, target_sig)
            except (ProcessLookupError, OSError):
                pass

        # Wait for graceful exit
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass
            if not _is_process_alive(pid):
                break
            time.sleep(0.1)

        # Force kill if still alive
        if _is_process_alive(pid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            time.sleep(0.2)
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, OSError):
                pass

        self._remove_state_file()
        return {
            "status": "stopped",
            "pid": pid,
            "message": f"Daemon (PID {pid}) stopped.",
        }

    def open_console(
        self,
        profile_path: str | Path | None = None,
        *,
        browser: bool = True,
        browser_opener: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Open or report the FireClaw operator web console URL."""
        status = self.get_daemon_status(profile_path)
        if status.get("status") != "running" or not status.get("daemon"):
            raise FireClawDaemonError(
                "FireClaw daemon is not running.",
                code="daemon_not_running",
                operator_action="先运行 fireclaw start 启动守护进程。",
            )

        gateway_url = status["daemon"].get("gateway_url", DEFAULT_GATEWAY_URL)
        opened = False
        if browser:
            opener = browser_opener or webbrowser.open
            try:
                opened = bool(opener(gateway_url))
            except Exception:
                opened = False

        return {
            "status": "opened" if opened else "ready",
            "url": gateway_url,
            "browser_opened": opened,
            "pid": status["daemon"]["pid"],
            "robot_id": status["daemon"].get("robot_id"),
            "message": f"Console ready at {gateway_url}",
        }


__all__ = [
    "DAEMON_LOG_FILENAME",
    "DAEMON_SCHEMA_VERSION",
    "DAEMON_STATE_FILENAME",
    "DEFAULT_GATEWAY_URL",
    "DaemonRuntimeManager",
    "DaemonState",
    "FireClawDaemonError",
]
