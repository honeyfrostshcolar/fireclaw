"""Explicit systemd user-service lifecycle for a managed FireClaw runtime.

Deployment generation writes the unit as an immutable release artifact.  The
installer is a separate, operator-invoked step: ordinary ``deploy apply``
never modifies the user's service manager or starts robot processes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import subprocess
from typing import Any, Protocol
from uuid import uuid4

from fireclaw_core.deployment.profile import (
    RuntimeDeploymentProfile,
    load_runtime_deployment_profile,
)


MAX_SYSTEMD_UNIT_BYTES = 64 * 1024
SYSTEMD_MANAGED_MARKER = "# Managed by FireClaw. Do not edit this installed copy."


class SystemdServiceError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SystemctlResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""


class SystemctlRunner(Protocol):
    def run(self, args: Sequence[str]) -> SystemctlResult: ...


class SubprocessSystemctlRunner:
    """Bounded, shell-free ``systemctl --user`` runner."""

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("systemctl timeout must be positive")
        self.timeout_seconds = timeout_seconds

    def run(self, args: Sequence[str]) -> SystemctlResult:
        argv = ("systemctl", "--user", *(str(item) for item in args))
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                close_fds=True,
            )
        except FileNotFoundError as exc:
            raise SystemdServiceError(
                "systemctl is not installed on this host",
                code="systemctl_missing",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SystemdServiceError(
                "systemctl did not return before the timeout",
                code="systemctl_timeout",
            ) from exc
        return SystemctlResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout[-16_384:],
            stderr=completed.stderr[-16_384:],
        )


@dataclass(frozen=True)
class GeneratedSystemdService:
    unit_name: str
    content: str
    source_path: Path
    installed_path: Path

    @property
    def sha256(self) -> str:
        return sha256(self.content.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_name": self.unit_name,
            "source_path": str(self.source_path),
            "installed_path": str(self.installed_path),
            "sha256": self.sha256,
            "content": self.content,
        }


def systemd_unit_name(profile: RuntimeDeploymentProfile) -> str:
    return f"fireclaw-{profile.deployment_id}.service"


def render_systemd_user_unit(profile: RuntimeDeploymentProfile) -> str:
    """Render one unit whose main process is the trusted runtime supervisor."""

    unit_name = systemd_unit_name(profile)
    current = profile.deployment_root / "current"
    runtime_wrapper = current / "bin" / "fireclaw-runtime"
    environment_file = profile.deployment_root / "state" / "runtime.env"
    restart = "on-failure" if profile.mode == "simulation" else "no"
    description = f"FireClaw managed runtime ({profile.deployment_id})"
    lines = [
        SYSTEMD_MANAGED_MARKER,
        f"# Unit: {unit_name}",
        "[Unit]",
        f"Description={_systemd_value(description, 'Description')}",
        "After=network-online.target",
        "Wants=network-online.target",
        "StartLimitIntervalSec=60",
        "StartLimitBurst=3",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={_systemd_escape_arg(str(runtime_wrapper))}",
        f"WorkingDirectory={_systemd_escape_arg(str(current))}",
        f"EnvironmentFile=-{_systemd_escape_arg(str(environment_file))}",
        f"Restart={restart}",
        "RestartSec=5",
        # Exit 2 is a deployment/configuration refusal and exit 78 is a
        # supervisor policy failure after its own bounded recovery budget.
        # Neither should create a second, unbounded restart loop in systemd.
        "RestartPreventExitStatus=2 78",
        "KillMode=mixed",
        "KillSignal=SIGINT",
        # Leave room for reverse-order service shutdown plus one bounded orphan
        # process-tree drain after a violently terminated roslaunch leader.
        "TimeoutStopSec=120",
        "TimeoutStartSec=180",
        "UMask=0077",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def generated_systemd_service(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    status_probe: Callable[..., dict[str, Any]] | None = None,
) -> GeneratedSystemdService:
    """Resolve the integrity-checked unit from the active deployment release."""

    profile = load_runtime_deployment_profile(profile_path, output_root=output_root)
    if status_probe is None:
        from fireclaw_core.deployment.deployer import inspect_deployment_status

        status_probe = inspect_deployment_status
    status = status_probe(
        profile.profile_path,
        output_root=output_root,
        check_runtime=False,
    )
    if status.get("status") not in {
        "installed",
        "ready",
        "installed_not_ready",
    }:
        raise SystemdServiceError(
            "systemd service requires an installed, integrity-checked release",
            code=f"service_deployment_{status.get('status') or 'invalid'}",
        )
    release = _trusted_release(profile, status)
    name = systemd_unit_name(profile)
    source = release / "systemd" / name
    if source.is_symlink() or not source.is_file():
        raise SystemdServiceError(
            "installed release does not contain a trusted systemd unit",
            code="service_unit_missing",
        )
    if source.stat().st_size > MAX_SYSTEMD_UNIT_BYTES:
        raise SystemdServiceError(
            "generated systemd unit exceeds the size limit",
            code="service_unit_invalid",
        )
    try:
        content = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemdServiceError(
            "generated systemd unit could not be read",
            code="service_unit_invalid",
        ) from exc
    expected = render_systemd_user_unit(profile)
    if content != expected:
        raise SystemdServiceError(
            "generated systemd unit does not match the current Profile",
            code="service_unit_invalid",
        )
    destination_dir = _unit_dir(unit_dir)
    return GeneratedSystemdService(
        unit_name=name,
        content=content,
        source_path=source,
        installed_path=destination_dir / name,
    )


def install_systemd_user_service(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    runner: SystemctlRunner | None = None,
    enable: bool = True,
    start: bool = True,
    status_probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Install and optionally activate the generated unit atomically."""

    service = generated_systemd_service(
        profile_path,
        output_root=output_root,
        unit_dir=unit_dir,
        status_probe=status_probe,
    )
    destination = service.installed_path
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise SystemdServiceError(
            "systemd user unit directory must be a regular directory",
            code="service_unit_directory_invalid",
        )
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise SystemdServiceError(
                "refusing to replace a non-regular systemd unit",
                code="service_unit_conflict",
            )
        existing = _read_bounded_unit(destination)
        if SYSTEMD_MANAGED_MARKER not in existing.splitlines()[:2]:
            raise SystemdServiceError(
                "refusing to overwrite a systemd unit not owned by FireClaw",
                code="service_unit_conflict",
            )
    _atomic_write_unit(destination, service.content)

    control = runner or SubprocessSystemctlRunner()
    commands: list[SystemctlResult] = []
    commands.append(_run_required(control, ("daemon-reload",)))
    if enable:
        commands.append(_run_required(control, ("enable", service.unit_name)))
    if start:
        commands.append(_run_required(control, ("restart", service.unit_name)))
    return {
        "status": "installed",
        "unit_name": service.unit_name,
        "unit_path": str(destination),
        "sha256": service.sha256,
        "enabled": enable,
        "started": start,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "commands": [_command_evidence(item) for item in commands],
    }


def inspect_systemd_user_service(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    runner: SystemctlRunner | None = None,
    status_probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        service = generated_systemd_service(
            profile_path,
            output_root=output_root,
            unit_dir=unit_dir,
            status_probe=status_probe,
        )
    except (SystemdServiceError, OSError, ValueError) as exc:
        return {
            "status": "unavailable",
            "reason_code": getattr(exc, "code", "service_status_invalid"),
            "message": str(exc),
        }
    destination = service.installed_path
    if not destination.exists() and not destination.is_symlink():
        return {
            "status": "not_installed",
            "unit_name": service.unit_name,
            "unit_path": str(destination),
            "generated_sha256": service.sha256,
            "enabled": None,
            "active": None,
        }
    if destination.is_symlink() or not destination.is_file():
        return {
            "status": "invalid",
            "reason_code": "service_unit_conflict",
            "unit_name": service.unit_name,
            "unit_path": str(destination),
            "enabled": None,
            "active": None,
        }
    try:
        installed = _read_bounded_unit(destination)
    except SystemdServiceError as exc:
        return {
            "status": "invalid",
            "reason_code": exc.code,
            "unit_name": service.unit_name,
            "unit_path": str(destination),
            "enabled": None,
            "active": None,
        }
    installed_hash = sha256(installed.encode("utf-8")).hexdigest()
    if installed != service.content:
        return {
            "status": "stale",
            "reason_code": "service_unit_stale",
            "unit_name": service.unit_name,
            "unit_path": str(destination),
            "generated_sha256": service.sha256,
            "installed_sha256": installed_hash,
            "enabled": None,
            "active": None,
        }

    control = runner or SubprocessSystemctlRunner()
    try:
        enabled_result = control.run(("is-enabled", service.unit_name))
        active_result = control.run(("is-active", service.unit_name))
    except SystemdServiceError as exc:
        return {
            "status": "installed_control_unavailable",
            "reason_code": exc.code,
            "message": str(exc),
            "unit_name": service.unit_name,
            "unit_path": str(destination),
            "generated_sha256": service.sha256,
            "installed_sha256": installed_hash,
            "enabled": None,
            "active": None,
        }
    enabled = enabled_result.returncode == 0
    active = active_result.returncode == 0
    return {
        "status": "active" if active else "inactive",
        "unit_name": service.unit_name,
        "unit_path": str(destination),
        "generated_sha256": service.sha256,
        "installed_sha256": installed_hash,
        "enabled": enabled,
        "active": active,
        "systemctl": {
            "is_enabled": _command_evidence(enabled_result),
            "is_active": _command_evidence(active_result),
        },
    }


def control_systemd_user_service(
    profile_path: str | Path,
    action: str,
    *,
    output_root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    runner: SystemctlRunner | None = None,
    status_probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if action not in {"start", "stop", "restart"}:
        raise ValueError("systemd service action must be start, stop, or restart")
    service = _require_installed_managed_unit(
        profile_path,
        output_root=output_root,
        unit_dir=unit_dir,
        status_probe=status_probe,
    )
    result = _run_required(
        runner or SubprocessSystemctlRunner(),
        (action, service.unit_name),
    )
    return {
        "status": "requested",
        "action": action,
        "unit_name": service.unit_name,
        "unit_path": str(service.installed_path),
        "command": _command_evidence(result),
    }


def uninstall_systemd_user_service(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    unit_dir: str | Path | None = None,
    runner: SystemctlRunner | None = None,
    status_probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    service = _require_installed_managed_unit(
        profile_path,
        output_root=output_root,
        unit_dir=unit_dir,
        status_probe=status_probe,
    )
    control = runner or SubprocessSystemctlRunner()
    commands = [
        _run_allowed(control, ("stop", service.unit_name), {0, 5}),
        _run_allowed(control, ("disable", service.unit_name), {0, 1}),
    ]
    service.installed_path.unlink()
    commands.append(_run_required(control, ("daemon-reload",)))
    return {
        "status": "uninstalled",
        "unit_name": service.unit_name,
        "unit_path": str(service.installed_path),
        "commands": [_command_evidence(item) for item in commands],
    }


def _require_installed_managed_unit(
    profile_path: str | Path,
    *,
    output_root: str | Path | None,
    unit_dir: str | Path | None,
    status_probe: Callable[..., dict[str, Any]] | None,
) -> GeneratedSystemdService:
    service = generated_systemd_service(
        profile_path,
        output_root=output_root,
        unit_dir=unit_dir,
        status_probe=status_probe,
    )
    path = service.installed_path
    if path.is_symlink() or not path.is_file():
        raise SystemdServiceError(
            "managed systemd user service is not installed",
            code="service_not_installed",
        )
    if _read_bounded_unit(path) != service.content:
        raise SystemdServiceError(
            "installed systemd unit is stale or not owned by this deployment",
            code="service_unit_stale",
        )
    return service


def _trusted_release(
    profile: RuntimeDeploymentProfile,
    status: dict[str, Any],
) -> Path:
    raw = status.get("release_dir")
    if not isinstance(raw, str) or not raw:
        raise SystemdServiceError(
            "deployment status did not identify the active release",
            code="service_release_invalid",
        )
    release = Path(raw).resolve(strict=True)
    releases_root = (profile.deployment_root / "releases").resolve(strict=True)
    try:
        release.relative_to(releases_root)
    except ValueError as exc:
        raise SystemdServiceError(
            "active release escapes the deployment release root",
            code="service_release_invalid",
        ) from exc
    return release


def _unit_dir(value: str | Path | None) -> Path:
    if value is None:
        value = Path.home() / ".config" / "systemd" / "user"
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def _read_bounded_unit(path: Path) -> str:
    if path.stat().st_size > MAX_SYSTEMD_UNIT_BYTES:
        raise SystemdServiceError(
            "installed systemd unit exceeds the size limit",
            code="service_unit_invalid",
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemdServiceError(
            "installed systemd unit could not be read",
            code="service_unit_invalid",
        ) from exc


def _atomic_write_unit(path: Path, content: str) -> None:
    temporary = path.parent / f".{path.name}.tmp-{uuid4().hex}"
    descriptor = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run_required(
    runner: SystemctlRunner,
    args: Sequence[str],
) -> SystemctlResult:
    return _run_allowed(runner, args, {0})


def _run_allowed(
    runner: SystemctlRunner,
    args: Sequence[str],
    allowed: set[int],
) -> SystemctlResult:
    result = runner.run(args)
    if result.returncode not in allowed:
        detail = (result.stderr or result.stdout).strip()
        raise SystemdServiceError(
            "systemctl command failed"
            + (f": {detail}" if detail else ""),
            code="systemctl_failed",
        )
    return result


def _command_evidence(result: SystemctlResult) -> dict[str, Any]:
    return {
        "argv": list(result.argv),
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _systemd_value(value: str, label: str) -> str:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise SystemdServiceError(
            f"systemd {label} contains unsupported control characters",
            code="service_unit_value_invalid",
        )
    return value.replace("%", "%%")


def _systemd_escape_arg(value: str) -> str:
    escaped = _systemd_value(value, "argument")
    if not any(character.isspace() for character in escaped) and not any(
        character in escaped for character in ('"', "\\")
    ):
        return escaped
    return '"' + escaped.replace("\\", "\\\\").replace('"', '\\"') + '"'


__all__ = [
    "control_systemd_user_service",
    "generated_systemd_service",
    "GeneratedSystemdService",
    "inspect_systemd_user_service",
    "install_systemd_user_service",
    "render_systemd_user_unit",
    "SubprocessSystemctlRunner",
    "SystemctlResult",
    "SystemctlRunner",
    "SystemdServiceError",
    "systemd_unit_name",
    "uninstall_systemd_user_service",
]
