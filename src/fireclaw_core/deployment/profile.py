"""Robot-owned configuration for Plugin Runtime deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import re
import sys
from typing import Any, Mapping
from urllib.parse import urlparse

from fireclaw_core.infra import tomllib_compat as tomllib

from fireclaw_core.infra.path_security import validate_runtime_root


_DEPLOYMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_BINDING_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class ManagedMissionGateway:
    """Trusted local probe settings for a supervisor-owned Mission Gateway."""

    enabled: bool = False
    base_url: str | None = None
    tls_ca_file: Path | None = None
    tls_client_cert_file: Path | None = None
    tls_client_key_file: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "tls_ca_file": (
                str(self.tls_ca_file) if self.tls_ca_file is not None else None
            ),
            "tls_client_cert_file": (
                str(self.tls_client_cert_file)
                if self.tls_client_cert_file is not None
                else None
            ),
            "tls_client_key_file": (
                str(self.tls_client_key_file)
                if self.tls_client_key_file is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RuntimeDeploymentProfile:
    deployment_id: str
    mode: str
    profile_path: Path
    output_root: Path
    architecture: str
    ros_distro: str
    ros_setup_files: tuple[Path, ...]
    plugin_paths: tuple[Path, ...]
    selected_plugin_ids: tuple[str, ...]
    plugin_configs: Mapping[str, Mapping[str, Any]]
    bindings: Mapping[str, Any]
    robot_launch: Path | None = None
    robot_id: str | None = None
    robot_base_url: str | None = None
    mission_gateway: ManagedMissionGateway = ManagedMissionGateway()

    @property
    def deployment_root(self) -> Path:
        return self.output_root / self.deployment_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "mode": self.mode,
            "profile_path": str(self.profile_path),
            "output_root": str(self.output_root),
            "deployment_root": str(self.deployment_root),
            "architecture": self.architecture,
            "ros1": {
                "distro": self.ros_distro,
                "setup_files": [str(path) for path in self.ros_setup_files],
            },
            "plugin_paths": [str(path) for path in self.plugin_paths],
            "selected_plugins": list(self.selected_plugin_ids),
            "bindings": dict(self.bindings),
            "robot_launch": str(self.robot_launch) if self.robot_launch else None,
            "robot_id": self.robot_id,
            "robot_base_url": self.robot_base_url,
            "supervisor": {
                "mission_gateway": self.mission_gateway.to_dict(),
            },
        }


def load_runtime_deployment_profile(
    path: str | Path,
    *,
    output_root: str | Path | None = None,
) -> RuntimeDeploymentProfile:
    authored = Path(path).expanduser()
    if authored.is_symlink():
        raise ValueError("deployment profile must be a regular non-symlink TOML file")
    target = authored.resolve(strict=True)
    if not target.is_file():
        raise ValueError("deployment profile must be a regular non-symlink TOML file")
    with target.open("rb") as handle:
        raw = tomllib.load(handle)

    deployment = _table(raw, "deployment", required=True)
    robot = _table(raw, "robot", required=False)
    server = _table(raw, "server", required=False)
    plugins = _table(raw, "plugins", required=True)
    ros1 = deployment.get("ros1")
    if not isinstance(ros1, dict):
        raise ValueError("deployment profile requires [deployment.ros1]")
    launch = deployment.get("launch", {})
    if not isinstance(launch, dict):
        raise ValueError("deployment.launch must be a TOML table")
    bindings_raw = deployment.get("bindings", {})
    if not isinstance(bindings_raw, dict):
        raise ValueError("deployment.bindings must be a TOML table")
    supervisor = deployment.get("supervisor", {})
    if not isinstance(supervisor, dict):
        raise ValueError("deployment.supervisor must be a TOML table")
    mission_gateway_raw = supervisor.get("mission_gateway", {})
    if not isinstance(mission_gateway_raw, dict):
        raise ValueError(
            "deployment.supervisor.mission_gateway must be a TOML table"
        )

    robot_id = _optional_string(robot, "id")
    deployment_id = _optional_string(deployment, "id") or robot_id
    if deployment_id is None:
        raise ValueError("deployment.id or robot.id must be configured")
    if _DEPLOYMENT_ID_RE.fullmatch(deployment_id) is None:
        raise ValueError("deployment.id contains unsupported characters")
    mode = _optional_string(deployment, "mode") or "real"
    if mode not in {"real", "simulation"}:
        raise ValueError("deployment.mode must be real or simulation")
    architecture = _optional_string(deployment, "architecture") or platform.machine()
    if not architecture:
        raise ValueError("deployment architecture could not be determined")

    configured_output = output_root
    if configured_output is None:
        configured_output = deployment.get("output_root")
    if configured_output is None:
        configured_output = Path.home() / ".fireclaw" / "deployments"
    resolved_output = _resolve_profile_path(
        configured_output,
        target.parent,
    )
    resolved_output = validate_runtime_root(resolved_output)

    ros_distro = _required_string(ros1, "distro", "deployment.ros1")
    setup_values = ros1.get("setup_files")
    if setup_values is None:
        setup_values = [f"/opt/ros/{ros_distro}/setup.bash"]
    setup_files = tuple(
        _resolve_profile_path(value, target.parent)
        for value in _string_list(
            setup_values,
            "deployment.ros1.setup_files",
            require_nonempty=True,
        )
    )

    plugin_paths = tuple(
        _resolve_profile_path(value, target.parent)
        for value in _string_list(
            plugins.get("paths"),
            "plugins.paths",
            require_nonempty=True,
        )
    )
    selected = tuple(
        _string_list(
            plugins.get("selected"),
            "plugins.selected",
            require_nonempty=True,
        )
    )
    if len(selected) != len(set(selected)):
        raise ValueError("plugins.selected contains duplicate Plugin IDs")
    if any(_PLUGIN_ID_RE.fullmatch(plugin_id) is None for plugin_id in selected):
        raise ValueError("plugins.selected contains an invalid Plugin ID")
    raw_configs = plugins.get("config", {})
    if not isinstance(raw_configs, dict):
        raise ValueError("plugins.config must be a TOML table")
    plugin_configs: dict[str, Mapping[str, Any]] = {}
    for plugin_id, config in raw_configs.items():
        if not isinstance(plugin_id, str) or not isinstance(config, dict):
            raise ValueError("plugins.config entries must be Plugin tables")
        plugin_configs[plugin_id] = dict(config)

    robot_launch_value = launch.get("robot_launch")
    robot_launch = (
        _resolve_profile_path(robot_launch_value, target.parent)
        if robot_launch_value is not None
        else None
    )
    bindings: dict[str, Any] = {}
    _flatten_bindings(bindings_raw, (), bindings)
    mission_gateway = _managed_mission_gateway(
        server=server,
        configured=mission_gateway_raw,
        profile_base=target.parent,
    )
    return RuntimeDeploymentProfile(
        deployment_id=deployment_id,
        mode=mode,
        profile_path=target,
        output_root=resolved_output,
        architecture=architecture,
        ros_distro=ros_distro,
        ros_setup_files=setup_files,
        plugin_paths=plugin_paths,
        selected_plugin_ids=selected,
        plugin_configs=plugin_configs,
        bindings=bindings,
        robot_launch=robot_launch,
        robot_id=robot_id,
        robot_base_url=_optional_string(robot, "base_url"),
        mission_gateway=mission_gateway,
    )


def _managed_mission_gateway(
    *,
    server: Mapping[str, Any],
    configured: Mapping[str, Any],
    profile_base: Path,
) -> ManagedMissionGateway:
    raw_enabled = configured.get("enabled")
    if raw_enabled is None:
        enabled = bool(server)
    elif isinstance(raw_enabled, bool):
        enabled = raw_enabled
    else:
        raise ValueError(
            "deployment.supervisor.mission_gateway.enabled must be a boolean"
        )
    if not enabled:
        return ManagedMissionGateway()
    if not server:
        raise ValueError(
            "managed Mission Gateway requires a [server] configuration"
        )

    server_tls = server.get("tls", {})
    if not isinstance(server_tls, dict):
        raise ValueError("server.tls must be a TOML table")
    tls_enabled = server_tls.get("enabled", False)
    if not isinstance(tls_enabled, bool):
        raise ValueError("server.tls.enabled must be a boolean")

    configured_base_url = _optional_string(configured, "base_url")
    if configured_base_url is None:
        host = _optional_string(server, "host") or "127.0.0.1"
        port = _tcp_port(server.get("port", 8766), "server.port")
        probe_host = _loopback_probe_host(host)
        scheme = "https" if tls_enabled else "http"
        base_url = f"{scheme}://{probe_host}:{port}"
    else:
        base_url = _validated_probe_url(configured_base_url)

    ca_value = configured.get("ca_file", server_tls.get("ca_file"))
    cert_value = configured.get("client_cert_file")
    key_value = configured.get("client_key_file")
    if (cert_value is None) != (key_value is None):
        raise ValueError(
            "managed Mission Gateway probe client_cert_file and "
            "client_key_file must be configured together"
        )
    if (
        configured_base_url is None
        and server_tls.get("require_client_cert") is True
        and cert_value is None
    ):
        raise ValueError(
            "managed Mission Gateway with required client certificates needs "
            "deployment.supervisor.mission_gateway client_cert_file and "
            "client_key_file"
        )
    return ManagedMissionGateway(
        enabled=True,
        base_url=base_url,
        tls_ca_file=(
            _resolve_profile_path(ca_value, profile_base)
            if ca_value is not None
            else None
        ),
        tls_client_cert_file=(
            _resolve_profile_path(cert_value, profile_base)
            if cert_value is not None
            else None
        ),
        tls_client_key_file=(
            _resolve_profile_path(key_value, profile_base)
            if key_value is not None
            else None
        ),
    )


def _tcp_port(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value < 1 or value > 65535:
        raise ValueError(f"{field_name} must be between 1 and 65535")
    return value


def _loopback_probe_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized in {"0.0.0.0", "*"}:
        return "127.0.0.1"
    if normalized in {"::", "[::]"}:
        return "[::1]"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _validated_probe_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "managed Mission Gateway base_url must be an HTTP(S) origin"
        )
    try:
        if parsed.port is not None:
            _tcp_port(parsed.port, "mission_gateway.base_url port")
    except ValueError as exc:
        raise ValueError("managed Mission Gateway base_url has an invalid port") from exc
    return value.rstrip("/")


def _flatten_bindings(
    value: Mapping[str, Any],
    prefix: tuple[str, ...],
    target: dict[str, Any],
) -> None:
    for raw_key, item in value.items():
        if not isinstance(raw_key, str) or _BINDING_SEGMENT_RE.fullmatch(raw_key) is None:
            raise ValueError("deployment binding keys contain unsupported characters")
        path = (*prefix, raw_key)
        if isinstance(item, dict):
            _flatten_bindings(item, path, target)
            continue
        if len(path) < 2:
            raise ValueError("deployment bindings must be namespaced tables")
        key = ".".join(path)
        if key in target:
            raise ValueError(f"duplicate deployment binding: {key}")
        _validate_toml_value(item, key)
        target[key] = item


def _validate_toml_value(value: Any, key: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, list):
        for item in value:
            _validate_toml_value(item, key)
        return
    raise ValueError(f"deployment binding {key} must be a TOML scalar or array")


def _table(raw: Mapping[str, Any], key: str, *, required: bool) -> dict[str, Any]:
    value = raw.get(key)
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"deployment profile requires [{key}]")
    return value


def _required_string(raw: Mapping[str, Any], key: str, scope: str) -> str:
    value = _optional_string(raw, key)
    if value is None:
        raise ValueError(f"{scope}.{key} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value.strip()


def _string_list(
    value: Any,
    field_name: str,
    *,
    require_nonempty: bool,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of strings")
    result = [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]
    if len(result) != len(value) or (require_nonempty and not result):
        qualifier = "non-empty " if require_nonempty else ""
        raise ValueError(f"{field_name} must be a {qualifier}array of strings")
    return result


def _resolve_profile_path(value: str | Path, base: Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("deployment path must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


__all__ = [
    "ManagedMissionGateway",
    "RuntimeDeploymentProfile",
    "load_runtime_deployment_profile",
]
