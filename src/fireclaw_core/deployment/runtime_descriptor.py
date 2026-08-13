"""Typed, data-only Plugin Runtime descriptors.

Descriptors deliberately contain no command strings.  The deployer maps the
small provider vocabulary below to trusted implementation code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Literal

from fireclaw_core.plugin.extension_loader import FireClawExtensionCandidate


MAX_RUNTIME_DESCRIPTOR_BYTES = 256 * 1024
RuntimeProviderKind = Literal["system_ros1", "ros1_catkin"]
LaunchArgumentType = Literal["string", "path", "number", "boolean", "json"]
ReadinessKind = Literal[
    "ros1_package",
    "ros1_node",
    "ros1_topic",
    "ros1_action",
    "ros1_tf",
]

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_ROS_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ROS_DISTRO_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_ARCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_ARG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_BINDING_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*)+$"
)
_ROS_NAME_RE = re.compile(r"^/[A-Za-z0-9_/~-]+$")


@dataclass(frozen=True)
class RuntimeProvider:
    kind: RuntimeProviderKind
    ros_distro: str
    packages: tuple[str, ...]
    architectures: tuple[str, ...] = ()
    workspace: Path | None = None
    source_paths: tuple[Path, ...] = ()

    def to_dict(self, *, plugin_root: Path | None = None) -> dict[str, Any]:
        def display(path: Path) -> str:
            if plugin_root is not None:
                try:
                    return path.relative_to(plugin_root).as_posix()
                except ValueError:
                    pass
            return str(path)

        return {
            "kind": self.kind,
            "ros_distro": self.ros_distro,
            "packages": list(self.packages),
            "architectures": list(self.architectures),
            "workspace": display(self.workspace) if self.workspace else None,
            "source_paths": [display(path) for path in self.source_paths],
        }


@dataclass(frozen=True)
class RuntimeLaunchArgument:
    name: str
    binding: str
    value_type: LaunchArgumentType
    required: bool = True
    default: Any = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "binding": self.binding,
            "type": self.value_type,
            "required": self.required,
        }
        if self.default is not None:
            result["default"] = self.default
        return result


@dataclass(frozen=True)
class RuntimeLaunch:
    file: Path
    arguments: tuple[RuntimeLaunchArgument, ...] = ()

    def to_dict(self, *, plugin_root: Path | None = None) -> dict[str, Any]:
        launch_file = str(self.file)
        if plugin_root is not None:
            try:
                launch_file = self.file.relative_to(plugin_root).as_posix()
            except ValueError:
                pass
        return {
            "file": launch_file,
            "arguments": [argument.to_dict() for argument in self.arguments],
        }


@dataclass(frozen=True)
class RuntimeReadinessProbe:
    kind: ReadinessKind
    name: str | None = None
    binding: str | None = None
    parent_binding: str | None = None
    child_binding: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "kind": self.kind,
                "name": self.name,
                "binding": self.binding,
                "parent_binding": self.parent_binding,
                "child_binding": self.child_binding,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class PluginRuntimeDescriptor:
    schema_version: str
    runtime_id: str
    providers: tuple[RuntimeProvider, ...]
    launch: RuntimeLaunch | None
    assets: tuple[Path, ...]
    readiness: tuple[RuntimeReadinessProbe, ...]
    descriptor_path: Path
    plugin_root: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "runtime_id": self.runtime_id,
            "providers": [
                provider.to_dict(plugin_root=self.plugin_root)
                for provider in self.providers
            ],
            "launch": (
                self.launch.to_dict(plugin_root=self.plugin_root)
                if self.launch is not None
                else None
            ),
            "assets": [
                asset.relative_to(self.plugin_root).as_posix()
                for asset in self.assets
            ],
            "readiness": [probe.to_dict() for probe in self.readiness],
            "descriptor_path": str(self.descriptor_path),
        }


def load_plugin_runtime_descriptor(
    candidate: FireClawExtensionCandidate,
) -> PluginRuntimeDescriptor | None:
    path = candidate.runtime_descriptor_path
    if path is None:
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("runtime descriptor must be a regular non-symlink file")
    if path.stat().st_size > MAX_RUNTIME_DESCRIPTOR_BYTES:
        raise ValueError("runtime descriptor exceeds the maximum size")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("runtime descriptor must be a JSON object")
    _reject_unknown(
        raw,
        {
            "schema_version",
            "runtime_id",
            "providers",
            "launch",
            "assets",
            "readiness",
        },
        "runtime descriptor",
    )
    schema_version = _required_string(raw, "schema_version")
    if schema_version != "1":
        raise ValueError(
            f"unsupported FireClaw Runtime descriptor version: {schema_version}"
        )
    runtime_id = _required_string(raw, "runtime_id")
    if _ID_RE.fullmatch(runtime_id) is None:
        raise ValueError("runtime_id contains unsupported characters")
    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, list) or not providers_raw:
        raise ValueError("runtime providers must be a non-empty array")
    if len(providers_raw) > 8:
        raise ValueError("runtime providers exceed the supported bound")
    providers = tuple(
        _parse_provider(value, candidate.root_dir, index)
        for index, value in enumerate(providers_raw)
    )
    launch = _parse_launch(raw.get("launch"), candidate.root_dir)
    asset_values = _string_tuple(raw.get("assets", []), "runtime assets")
    if len(asset_values) > 256:
        raise ValueError("runtime assets exceed the supported bound")
    if len(asset_values) != len(set(asset_values)):
        raise ValueError("runtime assets contains duplicates")
    assets = tuple(
        _resolve_file(
            candidate.root_dir,
            _safe_relative_path(value, "assets"),
            "runtime asset",
        )
        for value in asset_values
    )
    readiness_raw = raw.get("readiness", [])
    if not isinstance(readiness_raw, list):
        raise ValueError("runtime readiness must be an array")
    if len(readiness_raw) > 64:
        raise ValueError("runtime readiness probes exceed the supported bound")
    readiness = tuple(
        _parse_readiness(value, index)
        for index, value in enumerate(readiness_raw)
    )
    return PluginRuntimeDescriptor(
        schema_version=schema_version,
        runtime_id=runtime_id,
        providers=providers,
        launch=launch,
        assets=assets,
        readiness=readiness,
        descriptor_path=path,
        plugin_root=candidate.root_dir,
    )


def _parse_provider(value: Any, root: Path, index: int) -> RuntimeProvider:
    if not isinstance(value, dict):
        raise ValueError(f"runtime providers[{index}] must be an object")
    kind = _required_string(value, "kind")
    if kind not in {"system_ros1", "ros1_catkin"}:
        raise ValueError(
            f"runtime providers[{index}].kind must be system_ros1 or ros1_catkin"
        )
    common = {"kind", "ros_distro", "packages", "architectures"}
    allowed = common | ({"workspace", "source_paths"} if kind == "ros1_catkin" else set())
    _reject_unknown(value, allowed, f"runtime providers[{index}]")
    ros_distro = _required_string(value, "ros_distro")
    if _ROS_DISTRO_RE.fullmatch(ros_distro) is None:
        raise ValueError(f"runtime providers[{index}].ros_distro is invalid")
    packages = _string_tuple(value.get("packages"), f"runtime providers[{index}].packages")
    if not packages:
        raise ValueError(f"runtime providers[{index}].packages must not be empty")
    for package in packages:
        if _ROS_PACKAGE_RE.fullmatch(package) is None:
            raise ValueError(f"invalid ROS package name: {package}")
    if len(packages) != len(set(packages)):
        raise ValueError(f"runtime providers[{index}].packages contains duplicates")
    architectures = _string_tuple(
        value.get("architectures", []),
        f"runtime providers[{index}].architectures",
    )
    if any(_ARCH_RE.fullmatch(item) is None for item in architectures):
        raise ValueError(f"runtime providers[{index}].architectures is invalid")

    workspace = None
    source_paths: tuple[Path, ...] = ()
    if kind == "ros1_catkin":
        workspace = _resolve_directory(
            root,
            _safe_relative_path(value.get("workspace"), "workspace"),
            "runtime provider workspace",
        )
        source_values = _string_tuple(
            value.get("source_paths"),
            f"runtime providers[{index}].source_paths",
        )
        if not source_values:
            raise ValueError(
                f"runtime providers[{index}].source_paths must not be empty"
            )
        source_paths = tuple(
            _resolve_directory(
                workspace,
                _safe_relative_path(item, "source_paths"),
                "runtime provider source path",
            )
            for item in source_values
        )
    return RuntimeProvider(
        kind=kind,  # type: ignore[arg-type]
        ros_distro=ros_distro,
        packages=packages,
        architectures=architectures,
        workspace=workspace,
        source_paths=source_paths,
    )


def _parse_launch(value: Any, root: Path) -> RuntimeLaunch | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("runtime launch must be an object")
    _reject_unknown(value, {"file", "arguments"}, "runtime launch")
    launch_file = _resolve_file(
        root,
        _safe_relative_path(value.get("file"), "launch.file"),
        "runtime launch file",
    )
    if launch_file.suffix != ".launch":
        raise ValueError("runtime launch file must use the .launch suffix")
    arguments_raw = value.get("arguments", [])
    if not isinstance(arguments_raw, list):
        raise ValueError("runtime launch arguments must be an array")
    arguments: list[RuntimeLaunchArgument] = []
    seen_names: set[str] = set()
    for index, item in enumerate(arguments_raw):
        if not isinstance(item, dict):
            raise ValueError(f"runtime launch arguments[{index}] must be an object")
        _reject_unknown(
            item,
            {"name", "binding", "type", "required", "default"},
            f"runtime launch arguments[{index}]",
        )
        name = _required_string(item, "name")
        binding = _required_string(item, "binding")
        value_type = _required_string(item, "type")
        if _ARG_RE.fullmatch(name) is None:
            raise ValueError(f"invalid runtime launch argument name: {name}")
        if name in seen_names:
            raise ValueError(f"duplicate runtime launch argument name: {name}")
        seen_names.add(name)
        if _BINDING_RE.fullmatch(binding) is None:
            raise ValueError(f"invalid runtime binding key: {binding}")
        if value_type not in {"string", "path", "number", "boolean", "json"}:
            raise ValueError(f"unsupported runtime launch argument type: {value_type}")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ValueError("runtime launch argument required must be boolean")
        default = item.get("default")
        if default is not None:
            _validate_binding_value(default, value_type, binding)
        arguments.append(
            RuntimeLaunchArgument(
                name=name,
                binding=binding,
                value_type=value_type,  # type: ignore[arg-type]
                required=required,
                default=default,
            )
        )
    return RuntimeLaunch(file=launch_file, arguments=tuple(arguments))


def _parse_readiness(value: Any, index: int) -> RuntimeReadinessProbe:
    if not isinstance(value, dict):
        raise ValueError(f"runtime readiness[{index}] must be an object")
    _reject_unknown(
        value,
        {"kind", "name", "binding", "parent_binding", "child_binding"},
        f"runtime readiness[{index}]",
    )
    kind = _required_string(value, "kind")
    allowed = {"ros1_package", "ros1_node", "ros1_topic", "ros1_action", "ros1_tf"}
    if kind not in allowed:
        raise ValueError(f"unsupported runtime readiness kind: {kind}")
    name = _optional_string(value, "name")
    binding = _optional_string(value, "binding")
    parent_binding = _optional_string(value, "parent_binding")
    child_binding = _optional_string(value, "child_binding")
    if kind == "ros1_package":
        if name is None or _ROS_PACKAGE_RE.fullmatch(name) is None:
            raise ValueError("ros1_package readiness requires a package name")
        if any(item is not None for item in (binding, parent_binding, child_binding)):
            raise ValueError("ros1_package readiness accepts only name")
    elif kind == "ros1_tf":
        if name is not None or binding is not None:
            raise ValueError("ros1_tf readiness uses parent_binding and child_binding")
        for field_name, field_value in (
            ("parent_binding", parent_binding),
            ("child_binding", child_binding),
        ):
            if field_value is None or _BINDING_RE.fullmatch(field_value) is None:
                raise ValueError(f"ros1_tf readiness requires {field_name}")
    else:
        if (name is None) == (binding is None):
            raise ValueError(
                f"{kind} readiness requires exactly one of name or binding"
            )
        if name is not None and _ROS_NAME_RE.fullmatch(name) is None:
            raise ValueError(f"invalid ROS graph name: {name}")
        if binding is not None and _BINDING_RE.fullmatch(binding) is None:
            raise ValueError(f"invalid runtime binding key: {binding}")
        if parent_binding is not None or child_binding is not None:
            raise ValueError(f"{kind} readiness does not accept TF bindings")
    return RuntimeReadinessProbe(
        kind=kind,  # type: ignore[arg-type]
        name=name,
        binding=binding,
        parent_binding=parent_binding,
        child_binding=child_binding,
    )


def validate_runtime_binding_value(
    value: Any,
    value_type: LaunchArgumentType,
    binding: str,
) -> None:
    _validate_binding_value(value, value_type, binding)


def _validate_binding_value(value: Any, value_type: str, binding: str) -> None:
    if value_type in {"string", "path"}:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"binding {binding} must be a non-empty string")
    elif value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"binding {binding} must be a number")
    elif value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"binding {binding} must be boolean")
    elif value_type == "json":
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"binding {binding} must be JSON-compatible") from exc


def _resolve_directory(root: Path, relative: str, field_name: str) -> Path:
    candidate = root / relative
    if candidate.is_symlink():
        raise ValueError(f"{field_name} cannot be a symbolic link")
    path = candidate.resolve(strict=True)
    _require_inside(root, path, field_name)
    if not path.is_dir():
        raise ValueError(f"{field_name} must be a directory")
    return path


def _resolve_file(root: Path, relative: str, field_name: str) -> Path:
    candidate = root / relative
    if candidate.is_symlink():
        raise ValueError(f"{field_name} cannot be a symbolic link")
    path = candidate.resolve(strict=True)
    _require_inside(root, path, field_name)
    if not path.is_file():
        raise ValueError(f"{field_name} must be a regular file")
    return path


def _require_inside(root: Path, path: Path, field_name: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field_name} escapes the extension root") from exc


def _safe_relative_path(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty relative path")
    path = Path(value.strip())
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError(f"{field_name} must stay inside the Plugin")
    return path.as_posix()


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string when provided")
    return value.strip()


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of strings")
    result = tuple(
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    )
    if len(result) != len(value):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return result


def _reject_unknown(
    raw: dict[str, Any],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(
            f"{field_name} contains unsupported fields: {', '.join(unknown)}"
        )


__all__ = [
    "LaunchArgumentType",
    "PluginRuntimeDescriptor",
    "RuntimeLaunch",
    "RuntimeLaunchArgument",
    "RuntimeProvider",
    "RuntimeReadinessProbe",
    "load_plugin_runtime_descriptor",
    "validate_runtime_binding_value",
]
