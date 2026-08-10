"""Read-only deployment security audit.

The report shape follows OpenClaw's security audit boundary: checks have
stable identifiers and severities, while callers such as the CLI and doctor
consume the same structured report.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any, Literal, Mapping, Sequence

from fireclaw_core.gateway.auth import (
    is_loopback_host,
    resolve_gateway_api_token,
)
from fireclaw_core.gateway.config import load_config
from fireclaw_core.gateway.network_security import (
    gateway_network_policy_from_config,
)
from fireclaw_core.infra.path_security import validate_runtime_root
from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root
from fireclaw_core.policy.deployment import deployment_profile_from_config


SecurityAuditSeverity = Literal["info", "warn", "critical"]


@dataclass(frozen=True)
class SecurityAuditFinding:
    check_id: str
    severity: SecurityAuditSeverity
    title: str
    detail: str
    remediation: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecurityAuditSummary:
    critical: int
    warn: int
    info: int

    @property
    def status(self) -> str:
        if self.critical:
            return "critical"
        if self.warn:
            return "warn"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "critical": self.critical,
            "warn": self.warn,
            "info": self.info,
            "status": self.status,
        }


@dataclass(frozen=True)
class SecurityAuditReport:
    ts: str
    summary: SecurityAuditSummary
    findings: tuple[SecurityAuditFinding, ...]
    config_path: str | None
    runtime_root: str | None
    deep: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "summary": self.summary.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "config_path": self.config_path,
            "runtime_root": self.runtime_root,
            "deep": self.deep,
        }


def run_security_audit(
    *,
    config_path: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
    runtime_root: str | Path | None = None,
    plugin_dirs: Sequence[str | Path] = (),
    environ: Mapping[str, str] | None = None,
    deep: bool = False,
) -> SecurityAuditReport:
    """Inspect one FireClaw deployment without executing plugins or tools."""

    findings: list[SecurityAuditFinding] = []
    resolved_config_path = (
        Path(config_path).expanduser().resolve(strict=False)
        if config_path is not None
        else None
    )
    loaded: dict[str, Any] = {}
    if config is not None:
        loaded = dict(config)
    elif resolved_config_path is not None:
        try:
            loaded = load_config(resolved_config_path)
        except Exception as exc:
            findings.append(
                _finding(
                    "config.parse_failed",
                    "critical",
                    "FireClaw configuration cannot be parsed",
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    "Fix the TOML configuration before starting either Gateway.",
                    path=str(resolved_config_path),
                )
            )
    else:
        findings.append(
            _finding(
                "config.not_provided",
                "warn",
                "No deployment configuration was provided",
                "Gateway, sandbox, path, and transport settings cannot be fully audited.",
                "Pass --config with the exact fireclaw.toml used for deployment.",
            )
        )

    environment = dict(os.environ if environ is None else environ)
    resolved_root = _audit_runtime_root(
        loaded,
        config_path=resolved_config_path,
        explicit=runtime_root,
        findings=findings,
        environ=environment,
    )
    if resolved_config_path is not None and resolved_config_path.exists():
        _audit_config_permissions(resolved_config_path, loaded, findings)

    _audit_gateway(
        "mission",
        host=str(loaded.get("host") or "127.0.0.1"),
        token=resolve_gateway_api_token(
            _optional_string(loaded.get("api_token")),
            env_var="FIRECLAW_GATEWAY_TOKEN",
        )
        if environ is None
        else _resolve_token(
            loaded.get("api_token"),
            environment.get("FIRECLAW_GATEWAY_TOKEN"),
        ),
        tls_enabled=bool(loaded.get("tls_enabled", False)),
        cert_file=loaded.get("tls_cert_file"),
        key_file=loaded.get("tls_key_file"),
        require_client_cert=bool(
            loaded.get("tls_require_client_cert", False)
        ),
        network=loaded.get("network"),
        path_base=resolved_root,
        findings=findings,
    )
    _audit_gateway(
        "robot",
        host=str(
            loaded.get("robot_gateway_host") or "127.0.0.1"
        ),
        token=_resolve_token(
            loaded.get("robot_gateway_api_token"),
            environment.get("FIRECLAW_GATEWAY_TOKEN"),
        ),
        tls_enabled=bool(
            loaded.get("robot_gateway_tls_enabled", False)
        ),
        cert_file=loaded.get("robot_gateway_tls_cert_file"),
        key_file=loaded.get("robot_gateway_tls_key_file"),
        require_client_cert=bool(
            loaded.get("robot_gateway_tls_require_client_cert", False)
        ),
        network=loaded.get("network"),
        path_base=resolved_root,
        findings=findings,
    )
    _audit_robot_client_transport(loaded, findings)
    _audit_deployment_profiles(loaded, resolved_root, findings)
    _audit_storage_paths(loaded, resolved_root, findings)

    for plugin_dir in plugin_dirs:
        _audit_plugin_directory(
            _resolve_path(plugin_dir, resolved_root),
            findings,
            deep=deep,
        )

    ordered = tuple(
        sorted(
            findings,
            key=lambda item: (
                {"critical": 0, "warn": 1, "info": 2}[item.severity],
                item.check_id,
            ),
        )
    )
    summary = SecurityAuditSummary(
        critical=sum(item.severity == "critical" for item in ordered),
        warn=sum(item.severity == "warn" for item in ordered),
        info=sum(item.severity == "info" for item in ordered),
    )
    return SecurityAuditReport(
        ts=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        findings=ordered,
        config_path=(
            str(resolved_config_path)
            if resolved_config_path is not None
            else None
        ),
        runtime_root=str(resolved_root) if resolved_root is not None else None,
        deep=deep,
    )


def _audit_runtime_root(
    loaded: Mapping[str, Any],
    *,
    config_path: Path | None,
    explicit: str | Path | None,
    findings: list[SecurityAuditFinding],
    environ: Mapping[str, str],
) -> Path | None:
    configured = explicit if explicit is not None else loaded.get("runtime_root")
    try:
        if configured is not None:
            base = config_path.parent if config_path is not None else Path.cwd()
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                candidate = base / candidate
            root = validate_runtime_root(candidate)
        elif config_path is not None:
            root = validate_runtime_root(config_path.parent)
        elif environ.get("FIRECLAW_HOME"):
            root = validate_runtime_root(environ["FIRECLAW_HOME"])
        else:
            root = resolve_fireclaw_runtime_root(
                configured=None,
                config_path=None,
            )
    except Exception as exc:
        findings.append(
            _finding(
                "runtime.root_unsafe",
                "critical",
                "Runtime root violates the host path policy",
                str(exc),
                "Use a dedicated FireClaw runtime directory outside protected system, home-secret, source, plugin, and Skill paths.",
            )
        )
        return None
    findings.append(
        _finding(
            "runtime.root_canonical",
            "info",
            "Runtime root resolves to a stable canonical path",
            "Relative deployment paths are evaluated from the reported runtime root.",
            path=str(root),
        )
    )
    return root


def _audit_config_permissions(
    path: Path,
    loaded: Mapping[str, Any],
    findings: list[SecurityAuditFinding],
) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o022:
        findings.append(
            _finding(
                "filesystem.config_writable_by_others",
                "critical",
                "Deployment configuration is writable by group or others",
                f"{path} has mode {mode:04o}.",
                "Restrict ownership and remove group/other write permission.",
                path=str(path),
                mode=f"{mode:04o}",
            )
        )
    contains_inline_secret = any(
        bool(loaded.get(key))
        for key in (
            "api_token",
            "robot_gateway_api_token",
            "robot_gateway_client_api_token",
            "provider_api_key",
            "robot_agent_provider_api_key",
        )
    )
    if contains_inline_secret and mode & 0o077:
        findings.append(
            _finding(
                "filesystem.config_secret_permissions",
                "warn",
                "Configuration containing inline secrets is broadly readable",
                f"{path} has mode {mode:04o}.",
                "Move secrets to deployment environment variables and set the config file to mode 0600.",
                path=str(path),
                mode=f"{mode:04o}",
            )
        )


def _audit_gateway(
    role: str,
    *,
    host: str,
    token: str | None,
    tls_enabled: bool,
    cert_file: Any,
    key_file: Any,
    require_client_cert: bool,
    network: Any,
    path_base: Path | None,
    findings: list[SecurityAuditFinding],
) -> None:
    remote = not is_loopback_host(host)
    prefix = f"gateway.{role}"
    if remote and not token:
        findings.append(
            _finding(
                f"{prefix}.remote_without_auth",
                "critical",
                f"{role.title()} Gateway is remotely exposed without authentication",
                f"The listener host is {host!r}, but no bearer token resolves.",
                "Configure a non-empty Gateway token before using a non-loopback bind.",
                host=host,
            )
        )
    if remote and not tls_enabled:
        findings.append(
            _finding(
                f"{prefix}.remote_without_tls",
                "critical",
                f"{role.title()} Gateway is remotely exposed without TLS",
                "Credentials, task contracts, approvals, and robot state would cross plaintext HTTP.",
                "Enable TLS with a trusted certificate, or bind only to loopback behind a trusted TLS proxy.",
                host=host,
            )
        )
    if tls_enabled:
        if not _optional_string(cert_file) or not _optional_string(key_file):
            findings.append(
                _finding(
                    f"{prefix}.tls_identity_incomplete",
                    "critical",
                    f"{role.title()} Gateway TLS identity is incomplete",
                    "TLS is enabled but cert_file or key_file is missing.",
                    "Configure both the certificate chain and its private key.",
                )
            )
        else:
            _audit_private_key(
                _resolve_path(key_file, path_base),
                check_id=f"{prefix}.tls_key_permissions",
                findings=findings,
            )
        if remote and not require_client_cert:
            findings.append(
                _finding(
                    f"{prefix}.mtls_not_required",
                    "warn",
                    f"{role.title()} Gateway does not require client certificates",
                    "Bearer authentication is configured without mutual TLS client identity.",
                    "For real deployments, enable client-certificate verification or terminate mTLS at a trusted proxy.",
                    host=host,
                )
            )
    try:
        policy = gateway_network_policy_from_config(
            network if isinstance(network, Mapping) else None
        )
    except Exception as exc:
        findings.append(
            _finding(
                f"{prefix}.network_policy_invalid",
                "critical",
                f"{role.title()} Gateway network policy is invalid",
                str(exc),
                "Correct request, connection, authentication, SSE, Host, and Origin limits.",
            )
        )
        return
    if host in {"0.0.0.0", "::", ""} and not policy.allowed_hosts:
        findings.append(
            _finding(
                f"{prefix}.wildcard_without_allowed_hosts",
                "critical",
                f"{role.title()} Gateway wildcard bind lacks an allowed Host list",
                "Host-header validation cannot admit a wildcard listener without explicit names.",
                "Set network.allowed_hosts to the deployed DNS names or IP addresses.",
                host=host,
            )
        )


def _audit_robot_client_transport(
    loaded: Mapping[str, Any],
    findings: list[SecurityAuditFinding],
) -> None:
    robot_host = str(
        loaded.get("robot_gateway_host") or "127.0.0.1"
    )
    if is_loopback_host(robot_host):
        return
    if bool(loaded.get("robot_gateway_tls_enabled", False)) and not (
        loaded.get("robot_gateway_client_tls_ca_file")
    ):
        findings.append(
            _finding(
                "gateway.robot_client.ca_not_explicit",
                "warn",
                "Mission Agent has no explicit Robot Gateway trust anchor",
                "The client will rely on the host default CA store.",
                "Configure mission.robot_gateway_tls.ca_file when Robot Gateways use a private deployment CA.",
            )
        )
    client_cert = loaded.get("robot_gateway_client_tls_cert_file")
    client_key = loaded.get("robot_gateway_client_tls_key_file")
    if bool(client_cert) != bool(client_key):
        findings.append(
            _finding(
                "gateway.robot_client.mtls_identity_incomplete",
                "critical",
                "Mission Agent client-certificate identity is incomplete",
                "Exactly one of the client certificate or private key is configured.",
                "Configure both client cert_file and key_file, or remove both.",
            )
        )


def _audit_deployment_profiles(
    loaded: Mapping[str, Any],
    runtime_root: Path | None,
    findings: list[SecurityAuditFinding],
) -> None:
    deployment = _mapping(loaded.get("deployment"))
    for role, relative in (
        ("mission_agent", Path("data/mission/agent-workspace")),
        ("robot_agent", Path("data/robot/agent-workspace")),
    ):
        default_root = (
            runtime_root / relative if runtime_root is not None else relative
        )
        try:
            profile = deployment_profile_from_config(
                deployment,
                role=role,  # type: ignore[arg-type]
                default_workspace_root=default_root,
                allowed_workspace_roots=(default_root,),
                path_base=runtime_root or Path.cwd(),
            )
        except Exception as exc:
            findings.append(
                _finding(
                    f"sandbox.{role}.profile_invalid",
                    "critical",
                    f"{role} deployment or sandbox profile is invalid",
                    str(exc),
                    "Correct the deployment profile before exposing Agent Tools.",
                )
            )
            continue
        sandbox = profile.sandbox
        selectors = (
            list(profile.allow)
            if profile.allow is not None
            else []
        )
        if any(selector.strip() in {"*", "group:*"} for selector in selectors):
            findings.append(
                _finding(
                    f"exec.{role}.broad_allow",
                    "warn",
                    f"{role} uses an overly broad Tool allow selector",
                    "Hard-denied effects still fail closed, but future Tool contributions may become exposed automatically.",
                    "Use explicit Tool names or narrow effect groups.",
                    selectors=selectors,
                )
            )
        computer_selected = any(
            selector == "group:computer" or selector.startswith("computer_")
            for selector in (*selectors, *profile.also_allow)
        )
        if profile.mode == "simulation" and computer_selected:
            if not sandbox.process_ready:
                findings.append(
                    _finding(
                        f"sandbox.{role}.computer_tools_not_ready",
                        "warn",
                        f"{role} selects computer Tools without a ready sandbox",
                        "The policy will block process Tools because the Docker image is disabled or not pinned.",
                        "Enable the Docker sandbox and pin image_digest before simulation execution.",
                    )
                )
        findings.append(
            _finding(
                f"sandbox.{role}.policy_effective",
                "info",
                f"{role} deployment policy parsed successfully",
                "The effective mode, immutable workspace boundary, image pin, and Tool selectors were validated.",
                profile_id=profile.profile_id,
                workspace_root=str(sandbox.workspace_root),
                process_ready=sandbox.process_ready,
                network=sandbox.network,
                max_file_bytes=sandbox.max_file_bytes,
                max_workspace_bytes=sandbox.max_workspace_bytes,
                max_workspace_files=sandbox.max_workspace_files,
                max_concurrent_processes=(
                    sandbox.max_concurrent_processes
                ),
            )
        )


def _audit_storage_paths(
    loaded: Mapping[str, Any],
    runtime_root: Path | None,
    findings: list[SecurityAuditFinding],
) -> None:
    for key in (
        "data_dir",
        "robot_gateway_memory_path",
        "robot_gateway_event_path",
        "robot_gateway_task_queue_path",
        "robot_gateway_runtime_state_path",
    ):
        value = loaded.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        path = _resolve_path(value, runtime_root)
        if not path.exists():
            continue
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            continue
        if mode & 0o022:
            findings.append(
                _finding(
                    f"filesystem.{key}.writable_by_others",
                    "critical",
                    f"{key} is writable by group or others",
                    f"{path} has mode {mode:04o}.",
                    "Restrict the authoritative state path to the FireClaw service account.",
                    path=str(path),
                    mode=f"{mode:04o}",
                )
            )
        elif path.is_file() and mode & 0o044:
            findings.append(
                _finding(
                    f"filesystem.{key}.readable_by_others",
                    "warn",
                    f"{key} is readable by group or others",
                    f"{path} may contain task, map, approval, or operator data and has mode {mode:04o}.",
                    "Use mode 0600 for state files unless a reviewed deployment group requires access.",
                    path=str(path),
                    mode=f"{mode:04o}",
                )
            )


def _audit_plugin_directory(
    directory: Path,
    findings: list[SecurityAuditFinding],
    *,
    deep: bool,
) -> None:
    if not directory.exists():
        findings.append(
            _finding(
                "plugin.directory_missing",
                "info",
                "Configured plugin descriptor directory does not exist",
                str(directory),
                path=str(directory),
            )
        )
        return
    descriptor_paths = (
        directory.rglob("*.json")
        if deep
        else directory.glob("*.json")
    )
    for descriptor in sorted(descriptor_paths):
        if descriptor.is_symlink():
            findings.append(
                _finding(
                    "plugin.symlink_descriptor",
                    "critical",
                    "Plugin descriptor is a symbolic link",
                    str(descriptor),
                    "Use reviewed regular descriptor files inside the plugin directory.",
                    path=str(descriptor),
                )
            )
            continue
        descriptor_stat = descriptor.stat()
        mode = stat.S_IMODE(descriptor_stat.st_mode)
        if mode & 0o022:
            findings.append(
                _finding(
                    "plugin.descriptor_writable_by_others",
                    "critical",
                    "Plugin descriptor is writable by group or others",
                    f"{descriptor} has mode {mode:04o}.",
                    "Restrict descriptor ownership and write permission.",
                    path=str(descriptor),
                    mode=f"{mode:04o}",
                )
            )
        if descriptor_stat.st_size > 1024 * 1024:
            findings.append(
                _finding(
                    "plugin.descriptor_too_large",
                    "critical",
                    "Plugin descriptor exceeds the hard audit size limit",
                    f"{descriptor} is larger than 1048576 bytes.",
                    "Replace it with a compact data-only descriptor.",
                    path=str(descriptor),
                    size_bytes=descriptor_stat.st_size,
                )
            )
            continue
        try:
            payload = json.loads(descriptor.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(
                _finding(
                    "plugin.descriptor_invalid",
                    "warn",
                    "Plugin descriptor cannot be parsed",
                    f"{descriptor}: {type(exc).__name__}",
                    "Replace it with a valid reviewed descriptor.",
                    path=str(descriptor),
                )
            )
            continue
        if not isinstance(payload, dict):
            findings.append(
                _finding(
                    "plugin.descriptor_invalid",
                    "warn",
                    "Plugin descriptor is not a JSON object",
                    str(descriptor),
                    "Replace it with a valid reviewed descriptor.",
                    path=str(descriptor),
                )
            )
            continue
        if not any(
            key in payload
            for key in ("source", "source_uri", "package_digest", "provenance")
        ):
            findings.append(
                _finding(
                    "plugin.provenance_untracked",
                    "warn",
                    "Plugin descriptor has no install provenance",
                    f"{descriptor.name} cannot be tied to an immutable package source.",
                    "Record the package source and immutable digest before enabling third-party plugins.",
                    path=str(descriptor),
                    plugin_id=payload.get("plugin_id"),
                )
            )

    executable_paths: set[Path] = set()
    for pattern in ("*.py", "*.pyc", "*.so", "*.pyd", "*.sh"):
        matches = directory.rglob(pattern) if deep else directory.glob(pattern)
        executable_paths.update(matches)
    for executable in sorted(executable_paths):
        findings.append(
            _finding(
                "plugin.executable_artifact_unadmitted",
                "critical",
                "Descriptor directory contains an executable plugin artifact",
                (
                    f"{executable} cannot be loaded as an in-process "
                    "descriptor-only plugin."
                ),
                (
                    "Keep this directory data-only. Package reviewed built-ins "
                    "with FireClaw or expose untrusted code through a sandboxed "
                    "typed Tool adapter."
                ),
                path=str(executable),
            )
        )


def _audit_private_key(
    path: Path,
    *,
    check_id: str,
    findings: list[SecurityAuditFinding],
) -> None:
    if not path.exists():
        findings.append(
            _finding(
                check_id,
                "critical",
                "Configured TLS private key does not exist",
                str(path),
                "Install the private key at the configured path before starting the Gateway.",
                path=str(path),
            )
        )
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        findings.append(
            _finding(
                check_id,
                "critical",
                "TLS private key is accessible to group or others",
                f"{path} has mode {mode:04o}.",
                "Restrict the private key to the FireClaw service account, normally mode 0600.",
                path=str(path),
                mode=f"{mode:04o}",
            )
        )


def _finding(
    check_id: str,
    severity: SecurityAuditSeverity,
    title: str,
    detail: str,
    remediation: str | None = None,
    **evidence: Any,
) -> SecurityAuditFinding:
    return SecurityAuditFinding(
        check_id=check_id,
        severity=severity,
        title=title,
        detail=detail,
        remediation=remediation,
        evidence=evidence,
    )


def _resolve_token(configured: Any, environment: str | None) -> str | None:
    value = _optional_string(configured)
    return value if value is not None else environment or None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _resolve_path(value: Any, base: Path | None) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve(strict=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit a FireClaw deployment without starting it."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runtime-root", default=None)
    parser.add_argument("--plugin-dir", action="append", default=[])
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Recursively inspect configured plugin descriptor directories.",
    )
    parser.add_argument(
        "--fail-on",
        choices=("critical", "warn", "never"),
        default="critical",
    )
    args = parser.parse_args(argv)
    report = run_security_audit(
        config_path=args.config,
        runtime_root=args.runtime_root,
        plugin_dirs=args.plugin_dir,
        deep=args.deep,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    if args.fail_on == "never":
        return 0
    if report.summary.critical:
        return 2
    if args.fail_on == "warn" and report.summary.warn:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
