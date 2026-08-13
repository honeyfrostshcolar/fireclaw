"""Manifest-first discovery and runtime loading for FireClaw extensions.

The loader mirrors OpenClaw's control-plane/runtime split:

* discovery reads only small, data-only ``fireclaw.plugin.json`` manifests;
* activation resolves a declared entrypoint and invokes its ``register``
  function with a plugin-scoped API;
* all contributions are committed through one ``FireClawPluginHost``
  transaction, so a failed extension cannot leave partial registrations.

The manifest is deliberately not an executable permission.  Code extensions
must declare a trusted/sandboxed boundary and still pass the host's normal
tool policy at execution time.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Mapping, Sequence

from fireclaw_core.plugin.plugin_host import (
    FireClawPluginApi,
    FireClawPluginHost,
    PluginRecord,
    PluginTrustLevel,
)
# Keep the loader importable while ``fireclaw_core.policy`` is initializing.
# Importing the policy package here would pull the legacy Skill registry back
# into ``fireclaw_core.plugin`` and create a package-initialization cycle.
DeploymentMode = Literal["simulation", "real"]


EXTENSION_MANIFEST_NAME = "fireclaw.plugin.json"
FIRECLAW_EXTENSION_API_VERSION = "1"
MAX_EXTENSION_MANIFEST_BYTES = 256 * 1024
MAX_DISCOVERED_EXTENSIONS = 128
_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_TRUST_LEVELS: frozenset[str] = frozenset({"builtin", "trusted", "sandboxed"})


@dataclass(frozen=True)
class FireClawExtensionManifest:
    """Data-only metadata read before any extension module is imported."""

    plugin_id: str
    name: str
    version: str | None
    description: str | None
    api_version: str
    entrypoint: str
    trust_level: PluginTrustLevel
    enabled_by_default: bool
    capabilities: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    runtime: str | None = None
    config_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "api_version": self.api_version,
            "entrypoint": self.entrypoint,
            "trust_level": self.trust_level,
            "enabled_by_default": self.enabled_by_default,
            "capabilities": list(self.capabilities),
            "skills": list(self.skills),
            "runtime": self.runtime,
            "config_schema": dict(self.config_schema),
        }


@dataclass(frozen=True)
class FireClawExtensionCandidate:
    """A validated manifest plus its bounded filesystem locations."""

    manifest: FireClawExtensionManifest
    root_dir: Path
    manifest_path: Path
    entrypoint_path: Path
    runtime_descriptor_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "root_dir": str(self.root_dir),
            "manifest_path": str(self.manifest_path),
            "entrypoint_path": str(self.entrypoint_path),
            "runtime_descriptor_path": (
                str(self.runtime_descriptor_path)
                if self.runtime_descriptor_path is not None
                else None
            ),
        }


@dataclass(frozen=True)
class FireClawExtensionDiagnostic:
    """Content-safe discovery or activation diagnostic."""

    phase: str
    code: str
    message: str
    path: str | None = None
    plugin_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "plugin_id": self.plugin_id,
        }


@dataclass(frozen=True)
class FireClawExtensionDiscovery:
    candidates: tuple[FireClawExtensionCandidate, ...]
    diagnostics: tuple[FireClawExtensionDiagnostic, ...] = ()


@dataclass(frozen=True)
class FireClawExtensionLoadReport:
    discovered: tuple[FireClawExtensionCandidate, ...]
    loaded: tuple[PluginRecord, ...]
    disabled_plugin_ids: tuple[str, ...] = ()
    diagnostics: tuple[FireClawExtensionDiagnostic, ...] = ()
    tool_ids: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovered": [item.to_dict() for item in self.discovered],
            "loaded": [item.to_dict() for item in self.loaded],
            "disabled_plugin_ids": list(self.disabled_plugin_ids),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "tool_ids": list(self.tool_ids),
        }


@dataclass(frozen=True)
class FireClawExtensionContext:
    """Runtime values supplied by the host to one extension."""

    candidate: FireClawExtensionCandidate
    mode: DeploymentMode
    role: str
    config: Mapping[str, Any]
    services: Mapping[str, Any]


class FireClawExtensionApi:
    """Plugin-scoped API exposed to an extension entrypoint.

    The API delegates contribution registration to the transaction-owned
    ``FireClawPluginApi``.  Runtime services are read-only by convention and
    are supplied by the host; an extension cannot replace the host registry.
    """

    def __init__(
        self,
        host_api: FireClawPluginApi,
        context: FireClawExtensionContext,
    ) -> None:
        self._host_api = host_api
        self._context = context

    @property
    def id(self) -> str:
        return self._context.candidate.manifest.plugin_id

    @property
    def name(self) -> str:
        return self._context.candidate.manifest.name

    @property
    def version(self) -> str | None:
        return self._context.candidate.manifest.version

    @property
    def root_dir(self) -> Path:
        return self._context.candidate.root_dir

    @property
    def mode(self) -> DeploymentMode:
        return self._context.mode

    @property
    def role(self) -> str:
        return self._context.role

    @property
    def config(self) -> Mapping[str, Any]:
        return self._context.config

    @property
    def services(self) -> Mapping[str, Any]:
        return self._context.services

    @property
    def manifest(self) -> FireClawExtensionManifest:
        return self._context.candidate.manifest

    def register_tool(
        self,
        tool: Any,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a public SDK ``ToolSpec`` or a legacy host Tool value."""
        self._host_api.register_tool(tool, name=name, metadata=metadata)

    def register_physical_capability(self, capability: Any) -> None:
        self._host_api.register_physical_capability(capability)

    def register_physical_tool(self, tool: Any) -> None:
        """Register a public SDK physical Tool contract."""
        self._host_api.register_physical_tool(tool)

    def register_hook(
        self,
        hook_type: str,
        hook_name: str,
        callback: Any,
    ) -> None:
        self._host_api.register_hook(hook_type, hook_name, callback)

    def register_service(
        self,
        service_id: str,
        service: Any,
        *,
        data_only: bool = False,
    ) -> None:
        self._host_api.register_service(service_id, service, data_only=data_only)

    def register_context_engine(self, engine_id: str, engine: Any) -> None:
        self._host_api.register_context_engine(engine_id, engine)

    def register_agent_harness(self, harness: Any) -> None:
        self._host_api.register_agent_harness(harness)

    def register_dispose(self, callback: Any) -> None:
        self._host_api.register_dispose(callback)


def _diagnostic(
    phase: str,
    code: str,
    message: str,
    *,
    path: Path | None = None,
    plugin_id: str | None = None,
) -> FireClawExtensionDiagnostic:
    return FireClawExtensionDiagnostic(
        phase=phase,
        code=code,
        message=message,
        path=str(path) if path is not None else None,
        plugin_id=plugin_id,
    )


def _string(value: Any, field_name: str, *, required: bool = False) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise ValueError(f"{field_name} must be a non-empty string")
    return None


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} must contain non-empty strings")
        result.append(item.strip())
    return tuple(result)


def _safe_relative_path(value: Any, field_name: str) -> str:
    raw = _string(value, field_name, required=True)
    assert raw is not None
    path = Path(raw)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{field_name} must be a relative path inside the extension")
    return path.as_posix()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_manifest(path: Path, root: Path) -> FireClawExtensionCandidate:
    if path.is_symlink() or not path.is_file():
        raise ValueError("manifest must be a regular non-symlink file")
    if not _inside(root, path):
        raise ValueError("manifest escapes the extension root")
    if path.stat().st_size > MAX_EXTENSION_MANIFEST_BYTES:
        raise ValueError("manifest exceeds the maximum size")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest must be a JSON object")

    plugin_id = _string(raw.get("id"), "id", required=True)
    assert plugin_id is not None
    if _PLUGIN_ID_RE.fullmatch(plugin_id) is None:
        raise ValueError("id contains unsupported characters")
    api_version = _string(raw.get("api_version", raw.get("apiVersion")), "api_version", required=True)
    assert api_version is not None
    if api_version != FIRECLAW_EXTENSION_API_VERSION:
        raise ValueError(f"unsupported FireClaw extension API version: {api_version}")
    entrypoint = _safe_relative_path(raw.get("entrypoint"), "entrypoint")
    entrypoint_candidate = root / entrypoint
    if entrypoint_candidate.is_symlink():
        raise ValueError("entrypoint cannot be a symbolic link")
    entrypoint_path = entrypoint_candidate.resolve(strict=False)
    if not _inside(root, entrypoint_path):
        raise ValueError("entrypoint escapes the extension root")
    if not entrypoint_path.is_file():
        raise ValueError("entrypoint must be a regular file inside the extension")
    if entrypoint_path.suffix != ".py":
        raise ValueError("entrypoint must be a Python module")

    trust_value = _string(raw.get("trust_level", "trusted"), "trust_level", required=True)
    assert trust_value is not None
    if trust_value not in _TRUST_LEVELS:
        raise ValueError("trust_level must be builtin, trusted, or sandboxed")
    capabilities = _string_tuple(raw.get("capabilities"), "capabilities")
    skills = tuple(
        _safe_relative_path(item, "skills")
        for item in (raw.get("skills") or ())
    )
    runtime = None
    runtime_descriptor_path = None
    runtime_value = raw.get("runtime")
    if runtime_value is not None:
        runtime = _safe_relative_path(runtime_value, "runtime")
        runtime_candidate = root / runtime
        if runtime_candidate.is_symlink():
            raise ValueError("runtime descriptor cannot be a symbolic link")
        runtime_descriptor_path = runtime_candidate.resolve(strict=False)
        if not _inside(root, runtime_descriptor_path):
            raise ValueError("runtime descriptor escapes the extension root")
        if not runtime_descriptor_path.is_file():
            raise ValueError(
                "runtime descriptor must be a regular file inside the extension"
            )
        if runtime_descriptor_path.suffix != ".json":
            raise ValueError("runtime descriptor must be a JSON file")
    config_schema = raw.get("config_schema", raw.get("configSchema", {}))
    if not isinstance(config_schema, dict):
        raise ValueError("config_schema must be an object")
    enabled_by_default = raw.get("enabled_by_default", raw.get("enabledByDefault", True))
    if not isinstance(enabled_by_default, bool):
        raise ValueError("enabled_by_default must be boolean")
    return FireClawExtensionCandidate(
        manifest=FireClawExtensionManifest(
            plugin_id=plugin_id,
            name=_string(raw.get("name"), "name") or plugin_id,
            version=_string(raw.get("version"), "version"),
            description=_string(raw.get("description"), "description"),
            api_version=api_version,
            entrypoint=entrypoint,
            trust_level=trust_value,  # type: ignore[arg-type]
            enabled_by_default=enabled_by_default,
            capabilities=capabilities,
            skills=skills,
            runtime=runtime,
            config_schema=dict(config_schema),
        ),
        root_dir=root,
        manifest_path=path,
        entrypoint_path=entrypoint_path,
        runtime_descriptor_path=runtime_descriptor_path,
    )


def _manifest_paths(root: Path) -> list[tuple[Path, Path]]:
    """Return explicit plugin roots and one-level extension children.

    Limiting the scan to one level avoids treating ROS package manifests deep
    inside an extension's source tree as FireClaw plugins.
    """

    if root.name == EXTENSION_MANIFEST_NAME:
        return [(root.parent, root)]
    if root.is_file():
        return []
    paths: list[tuple[Path, Path]] = []
    direct = root / EXTENSION_MANIFEST_NAME
    if direct.is_file() or direct.is_symlink():
        paths.append((root, direct))
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return paths
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        manifest = child / EXTENSION_MANIFEST_NAME
        if manifest.is_file() or manifest.is_symlink():
            paths.append((child, manifest))
    return paths


def discover_fireclaw_extensions(
    roots: Sequence[str | Path],
    *,
    max_extensions: int = MAX_DISCOVERED_EXTENSIONS,
) -> FireClawExtensionDiscovery:
    """Discover and validate manifests without importing extension code."""

    candidates: list[FireClawExtensionCandidate] = []
    diagnostics: list[FireClawExtensionDiagnostic] = []
    seen_ids: set[str] = set()
    if max_extensions <= 0 or max_extensions > MAX_DISCOVERED_EXTENSIONS:
        raise ValueError("max_extensions is outside the supported bound")

    for raw_root in roots:
        root_input = Path(raw_root).expanduser()
        if root_input.is_symlink():
            diagnostics.append(
                _diagnostic(
                    "discovery",
                    "root_symlink",
                    "Extension root cannot be a symbolic link.",
                    path=root_input,
                )
            )
            continue
        if not root_input.exists():
            diagnostics.append(
                _diagnostic("discovery", "root_missing", "Extension root does not exist.", path=root_input)
            )
            continue
        try:
            root = root_input.resolve(strict=True)
        except OSError:
            diagnostics.append(
                _diagnostic("discovery", "root_unreadable", "Extension root cannot be resolved.", path=root_input)
            )
            continue
        for candidate_root, manifest_path in _manifest_paths(root):
            if len(candidates) >= max_extensions:
                diagnostics.append(
                    _diagnostic("discovery", "extension_limit", "Extension discovery limit reached.", path=root)
                )
                return FireClawExtensionDiscovery(tuple(candidates), tuple(diagnostics))
            try:
                candidate_root = candidate_root.resolve(strict=True)
                candidate = _read_manifest(manifest_path, candidate_root)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                diagnostics.append(
                    _diagnostic(
                        "discovery",
                        "manifest_invalid",
                        f"Extension manifest rejected with {type(exc).__name__}.",
                        path=manifest_path,
                    )
                )
                continue
            plugin_id = candidate.manifest.plugin_id
            if plugin_id in seen_ids:
                diagnostics.append(
                    _diagnostic(
                        "discovery",
                        "duplicate_plugin_id",
                        "Duplicate extension ID; first discovered candidate wins.",
                        path=candidate.manifest_path,
                        plugin_id=plugin_id,
                    )
                )
                continue
            seen_ids.add(plugin_id)
            candidates.append(candidate)
    return FireClawExtensionDiscovery(tuple(candidates), tuple(diagnostics))


def _load_module(candidate: FireClawExtensionCandidate) -> ModuleType:
    """Import an extension entrypoint in an isolated package namespace.

    OpenClaw loads a plugin as a package rather than treating the entrypoint
    as one flat script.  Giving a FireClaw extension a private package name
    lets providers keep their adapter modules next to ``entrypoint.py`` and
    use normal relative imports without adding the extension directory to the
    process-wide ``sys.path``.
    """
    digest = hashlib.sha256(
        str(candidate.root_dir).encode("utf-8")
    ).hexdigest()[:20]
    package_name = f"fireclaw_extension_{digest}"
    plugin_package_name = f"{package_name}.plugin"
    module_name = f"{plugin_package_name}.{candidate.entrypoint_path.stem}"

    # The package modules are deliberately private to this extension.  A
    # second extension cannot import another extension's implementation by
    # guessing its normal package name.
    package = ModuleType(package_name)
    package.__path__ = [str(candidate.root_dir)]  # type: ignore[attr-defined]
    package.__package__ = package_name
    plugin_package = ModuleType(plugin_package_name)
    plugin_package.__path__ = [str(candidate.entrypoint_path.parent)]  # type: ignore[attr-defined]
    plugin_package.__package__ = plugin_package_name
    sys.modules[package_name] = package
    sys.modules[plugin_package_name] = plugin_package
    spec = importlib.util.spec_from_file_location(module_name, candidate.entrypoint_path)
    if spec is None or spec.loader is None:
        raise ImportError("extension entrypoint has no import loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        for name in (module_name, plugin_package_name, package_name):
            sys.modules.pop(name, None)
        raise
    return module


def _resolve_register(module: ModuleType) -> Any:
    register = getattr(module, "register", None)
    if callable(register):
        return register
    definition = getattr(module, "plugin", None)
    register = getattr(definition, "register", None)
    if callable(register):
        return register
    raise TypeError("extension entrypoint must export callable register(api)")


def load_fireclaw_extensions(
    host: FireClawPluginHost,
    roots: Sequence[str | Path],
    *,
    mode: DeploymentMode,
    role: str,
    plugin_configs: Mapping[str, Mapping[str, Any]] | None = None,
    services: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> FireClawExtensionLoadReport:
    """Discover, activate, and report extensions for one Agent Host.

    ``plugin_configs`` is generic and keyed by manifest ID.  The host never
    interprets plugin-specific fields; each extension validates its own config
    after the manifest has been accepted.
    """

    discovery = discover_fireclaw_extensions(roots)
    diagnostics = list(discovery.diagnostics)
    loaded: list[PluginRecord] = []
    disabled: list[str] = []
    tool_ids: list[str] = []
    configs = plugin_configs or {}
    runtime_services = dict(services or {})

    for candidate in discovery.candidates:
        raw_config = configs.get(candidate.manifest.plugin_id, {})
        if not isinstance(raw_config, Mapping):
            diagnostics.append(
                _diagnostic(
                    "activation",
                    "plugin_config_invalid",
                    "Plugin configuration must be an object.",
                    path=candidate.manifest.manifest_path,
                    plugin_id=candidate.manifest.plugin_id,
                )
            )
            continue
        enabled = raw_config.get("enabled", candidate.manifest.enabled_by_default)
        if not isinstance(enabled, bool):
            diagnostics.append(
                _diagnostic(
                    "activation",
                    "plugin_enabled_invalid",
                    "Plugin enabled value must be boolean.",
                    path=candidate.manifest.manifest_path,
                    plugin_id=candidate.manifest.plugin_id,
                )
            )
            continue
        if not enabled:
            disabled.append(candidate.manifest.plugin_id)
            continue

        context = FireClawExtensionContext(
            candidate=candidate,
            mode=mode,
            role=role,
            config=dict(raw_config),
            services=runtime_services,
        )

        try:
            module = _load_module(candidate)
            register = _resolve_register(module)

            def activate(host_api: FireClawPluginApi, register=register, context=context) -> None:
                result = register(FireClawExtensionApi(host_api, context))
                if inspect.isawaitable(result):
                    raise TypeError("async extension registration is not supported")

            record = host.activate(
                candidate.manifest.plugin_id,
                activate,
                name=candidate.manifest.name,
                version=candidate.manifest.version,
                description=candidate.manifest.description,
                source=f"extension:{candidate.root_dir}",
                api_version=candidate.manifest.api_version,
                trust_level=candidate.manifest.trust_level,
            )
            loaded.append(record)
            tool_ids.extend(
                contribution_id
                for kind, contribution_id in record.contribution_keys
                if kind == "tool"
            )
        except Exception as exc:
            diagnostics.append(
                _diagnostic(
                    "activation",
                    "extension_activation_failed",
                    f"Extension activation failed with {type(exc).__name__}.",
                    path=candidate.entrypoint_path,
                    plugin_id=candidate.manifest.plugin_id,
                )
            )
            if strict:
                raise

    return FireClawExtensionLoadReport(
        discovered=discovery.candidates,
        loaded=tuple(loaded),
        disabled_plugin_ids=tuple(disabled),
        diagnostics=tuple(diagnostics),
        tool_ids=tuple(sorted(set(tool_ids))),
    )


__all__ = [
    "EXTENSION_MANIFEST_NAME",
    "FIRECLAW_EXTENSION_API_VERSION",
    "FireClawExtensionApi",
    "FireClawExtensionCandidate",
    "FireClawExtensionContext",
    "FireClawExtensionDiagnostic",
    "FireClawExtensionDiscovery",
    "FireClawExtensionLoadReport",
    "FireClawExtensionManifest",
    "discover_fireclaw_extensions",
    "load_fireclaw_extensions",
]
