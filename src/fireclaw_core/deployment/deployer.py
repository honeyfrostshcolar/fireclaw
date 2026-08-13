"""Plan, apply, and inspect Plugin Runtime deployments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import sys
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from uuid import uuid4
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment.command import (
    DeploymentCommandRunner,
    SubprocessDeploymentCommandRunner,
)
from fireclaw_core.deployment.profile import (
    RuntimeDeploymentProfile,
    load_runtime_deployment_profile,
)
from fireclaw_core.deployment.runtime_descriptor import (
    PluginRuntimeDescriptor,
    RuntimeProvider,
    RuntimeReadinessProbe,
    load_plugin_runtime_descriptor,
    validate_runtime_binding_value,
)
from fireclaw_core.deployment.systemd_service import (
    render_systemd_user_unit,
    systemd_unit_name,
)
from fireclaw_core.evaluation.artifacts import canonical_json_sha256, sha256_file
from fireclaw_core.plugin.extension_loader import (
    FireClawExtensionCandidate,
    discover_fireclaw_extensions,
)


DEPLOYMENT_SCHEMA_VERSION = "1"
# Bump whenever trusted generated artifact content changes independently of
# Profile/Plugin inputs, so an older content-addressed release is never reused.
DEPLOYMENT_GENERATOR_VERSION = "6"
MAX_SOURCE_FILES = 100_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024


class DeploymentError(RuntimeError):
    """A content-safe deployment failure suitable for CLI reporting."""

    def __init__(self, message: str, *, code: str = "deployment_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RosPackageEvidence:
    name: str
    package_dir: Path
    package_xml_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "package_dir": str(self.package_dir),
            "package_xml_sha256": self.package_xml_sha256,
        }


@dataclass(frozen=True)
class PluginDeploymentPlan:
    plugin_id: str
    candidate: FireClawExtensionCandidate
    descriptor: PluginRuntimeDescriptor | None
    provider: RuntimeProvider | None
    package_evidence: tuple[RosPackageEvidence, ...]
    source_packages: tuple[RosPackageEvidence, ...]
    launch_arguments: Mapping[str, Any]
    manifest_sha256: str
    descriptor_sha256: str | None
    launch_sha256: str | None
    asset_sha256: Mapping[str, str]
    source_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "plugin_root": str(self.candidate.root_dir),
            "manifest_path": str(self.candidate.manifest_path),
            "manifest_sha256": self.manifest_sha256,
            "runtime": self.descriptor.to_dict() if self.descriptor else None,
            "provider": (
                self.provider.to_dict(plugin_root=self.candidate.root_dir)
                if self.provider
                else {"kind": "python_only"}
            ),
            "package_evidence": [item.to_dict() for item in self.package_evidence],
            "source_packages": [item.to_dict() for item in self.source_packages],
            "launch_arguments": dict(self.launch_arguments),
            "descriptor_sha256": self.descriptor_sha256,
            "launch_sha256": self.launch_sha256,
            "asset_sha256": dict(self.asset_sha256),
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class DeploymentPlan:
    profile: RuntimeDeploymentProfile
    plugins: tuple[PluginDeploymentPlan, ...]
    python_executable: Path
    fingerprint: str
    actions: tuple[dict[str, Any], ...]

    @property
    def release_dir(self) -> Path:
        return self.profile.deployment_root / "releases" / self.fingerprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "generator_version": DEPLOYMENT_GENERATOR_VERSION,
            "status": "ready",
            "fingerprint": self.fingerprint,
            "release_dir": str(self.release_dir),
            "python_executable": str(self.python_executable),
            "profile": self.profile.to_dict(),
            "plugins": [plugin.to_dict() for plugin in self.plugins],
            "actions": list(self.actions),
        }


def build_deployment_plan(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    runner: DeploymentCommandRunner | None = None,
) -> DeploymentPlan:
    command_runner = runner or SubprocessDeploymentCommandRunner()
    profile = load_runtime_deployment_profile(
        profile_path,
        output_root=output_root,
    )
    # Reuse the canonical Robot capability-profile parser rather than letting
    # the deployment layer accept a TOML file the generated Gateway cannot use.
    load_robot_capability_profile(profile.profile_path)
    if profile.architecture != platform.machine():
        raise DeploymentError(
            "deployment architecture does not match this machine",
            code="architecture_mismatch",
        )
    _validate_profile_files(profile)

    discovery = discover_fireclaw_extensions(profile.plugin_paths)
    if discovery.diagnostics:
        codes = ", ".join(sorted({item.code for item in discovery.diagnostics}))
        raise DeploymentError(
            f"Plugin discovery reported invalid candidates: {codes}",
            code="plugin_discovery_failed",
        )
    candidates = {item.manifest.plugin_id: item for item in discovery.candidates}
    missing = [
        plugin_id
        for plugin_id in profile.selected_plugin_ids
        if plugin_id not in candidates
    ]
    if missing:
        raise DeploymentError(
            f"selected Plugins were not discovered: {', '.join(missing)}",
            code="plugin_not_found",
        )
    for plugin_id in profile.selected_plugin_ids:
        config = profile.plugin_configs.get(plugin_id, {})
        if config.get("enabled") is False:
            raise DeploymentError(
                f"selected Plugin {plugin_id} is disabled by plugins.config",
                code="selected_plugin_disabled",
            )
        _validate_plugin_config(plugin_id, config)

    selected_candidates = tuple(candidates[item] for item in profile.selected_plugin_ids)
    descriptors = tuple(
        load_plugin_runtime_descriptor(candidate)
        for candidate in selected_candidates
    )
    runtime_descriptors = tuple(item for item in descriptors if item is not None)
    environment: dict[str, str] = {}
    installed_packages: dict[str, RosPackageEvidence] = {}
    if runtime_descriptors:
        environment = _load_ros_environment(profile.ros_setup_files, command_runner)
        actual_distro = environment.get("ROS_DISTRO")
        if actual_distro != profile.ros_distro:
            raise DeploymentError(
                "sourced ROS_DISTRO does not match deployment.ros1.distro",
                code="ros_distro_mismatch",
            )
        required_system_packages = {
            package
            for descriptor in runtime_descriptors
            for provider in descriptor.providers
            if provider.kind == "system_ros1"
            and _provider_compatible(provider, profile)
            for package in provider.packages
        }
        installed_packages = _find_installed_ros_packages(
            environment,
            required_system_packages,
        )

    plugin_plans: list[PluginDeploymentPlan] = []
    runtime_ids: set[str] = set()
    claimed_source_packages: dict[str, str] = {}
    for candidate, descriptor in zip(selected_candidates, descriptors):
        provider: RuntimeProvider | None = None
        package_evidence: tuple[RosPackageEvidence, ...] = ()
        source_packages: tuple[RosPackageEvidence, ...] = ()
        source_digest: str | None = None
        asset_sha256: dict[str, str] = {}
        launch_arguments: Mapping[str, Any] = {}
        if descriptor is not None:
            if descriptor.runtime_id in runtime_ids:
                raise DeploymentError(
                    f"duplicate runtime_id: {descriptor.runtime_id}",
                    code="duplicate_runtime_id",
                )
            runtime_ids.add(descriptor.runtime_id)
            provider = _select_provider(
                descriptor,
                profile,
                installed_packages,
            )
            if provider.kind == "system_ros1":
                package_evidence = tuple(
                    installed_packages[package]
                    for package in provider.packages
                )
            else:
                source_package_map = _discover_source_packages(provider.source_paths)
                absent = [
                    package
                    for package in provider.packages
                    if package not in source_package_map
                ]
                if absent:
                    raise DeploymentError(
                        f"Plugin {candidate.manifest.plugin_id} source is missing ROS packages: "
                        + ", ".join(absent),
                        code="source_package_missing",
                    )
                source_packages = tuple(
                    source_package_map[package]
                    for package in provider.packages
                )
                for package in provider.packages:
                    previous = claimed_source_packages.get(package)
                    if previous is not None:
                        raise DeploymentError(
                            f"ROS package {package} is supplied by both {previous} and "
                            f"{candidate.manifest.plugin_id}",
                            code="duplicate_source_package",
                        )
                    claimed_source_packages[package] = candidate.manifest.plugin_id
                source_digest = _hash_source_packages(source_packages)
            launch_arguments = _resolve_launch_arguments(descriptor, profile)
            _validate_readiness_bindings(descriptor, profile)
            asset_sha256 = {
                asset.relative_to(descriptor.plugin_root).as_posix(): sha256_file(
                    asset
                )
                for asset in descriptor.assets
            }

        plugin_plans.append(
            PluginDeploymentPlan(
                plugin_id=candidate.manifest.plugin_id,
                candidate=candidate,
                descriptor=descriptor,
                provider=provider,
                package_evidence=package_evidence,
                source_packages=source_packages,
                launch_arguments=launch_arguments,
                manifest_sha256=sha256_file(candidate.manifest_path),
                descriptor_sha256=(
                    sha256_file(descriptor.descriptor_path)
                    if descriptor is not None
                    else None
                ),
                launch_sha256=(
                    sha256_file(descriptor.launch.file)
                    if descriptor is not None and descriptor.launch is not None
                    else None
                ),
                asset_sha256=asset_sha256,
                source_sha256=source_digest,
            )
        )

    python_executable = Path(sys.executable).resolve(strict=True)
    normalized = {
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "generator_version": DEPLOYMENT_GENERATOR_VERSION,
        "python_executable": str(python_executable),
        "profile": profile.to_dict(),
        "plugin_configs": {
            plugin_id: dict(profile.plugin_configs.get(plugin_id, {}))
            for plugin_id in profile.selected_plugin_ids
        },
        "profile_sha256": sha256_file(profile.profile_path),
        "plugins": [plugin.to_dict() for plugin in plugin_plans],
    }
    fingerprint = canonical_json_sha256(normalized)
    actions = _build_actions(profile, plugin_plans, fingerprint)
    return DeploymentPlan(
        profile=profile,
        plugins=tuple(plugin_plans),
        python_executable=python_executable,
        fingerprint=fingerprint,
        actions=actions,
    )


def apply_deployment(
    plan: DeploymentPlan,
    *,
    runner: DeploymentCommandRunner | None = None,
) -> dict[str, Any]:
    command_runner = runner or SubprocessDeploymentCommandRunner()
    root = plan.profile.deployment_root
    releases = root / "releases"
    failures = root / "failures"
    state_dir = root / "state"
    for directory in (root, releases, failures, state_dir):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    final_release = plan.release_dir
    if final_release.exists():
        _validate_existing_release(final_release, plan.fingerprint)
        _activate_release(root, final_release, plan)
        return _apply_result(plan, final_release, reused=True)

    # Build at the final content-addressed path.  Catkin install spaces embed
    # their prefix and are not safely relocatable.  The release remains
    # invisible to ``current`` until every receipt/hash check has completed.
    stage = final_release
    stage.mkdir(mode=0o700)
    try:
        _materialize_release(stage, plan, command_runner)
        checks = _validate_release_artifacts(stage)
        if not checks["ok"]:
            raise DeploymentError(
                "generated release failed its artifact hash check",
                code="release_invalid",
            )
    except Exception as exc:
        failure_dir = failures / (
            f"{plan.fingerprint}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-"
            f"{uuid4().hex[:8]}"
        )
        try:
            _atomic_json(
                stage / "deployment-failure.json",
                {
                    "schema_version": DEPLOYMENT_SCHEMA_VERSION,
                    "status": "failed",
                    "code": getattr(exc, "code", "deployment_apply_failed"),
                    "message": str(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            stage.replace(failure_dir)
        except OSError:
            failure_dir = stage
        if isinstance(exc, DeploymentError):
            raise DeploymentError(
                f"{exc}; failure artifacts: {failure_dir}",
                code=exc.code,
            ) from exc
        raise DeploymentError(
            f"deployment apply failed; failure artifacts: {failure_dir}",
            code="deployment_apply_failed",
        ) from exc

    _activate_release(root, final_release, plan)
    return _apply_result(plan, final_release, reused=False)


def inspect_deployment_status(
    profile_path: str | Path,
    *,
    output_root: str | Path | None = None,
    check_runtime: bool = True,
    runner: DeploymentCommandRunner | None = None,
) -> dict[str, Any]:
    command_runner = runner or SubprocessDeploymentCommandRunner()
    plan = build_deployment_plan(
        profile_path,
        output_root=output_root,
        runner=command_runner,
    )
    root = plan.profile.deployment_root
    root_receipt_path = root / "deployment-receipt.json"
    if not root_receipt_path.is_file():
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": "not_deployed",
            "fingerprint": plan.fingerprint,
            "deployment_root": str(root),
        }
    receipt = _read_json_object(root_receipt_path)
    installed_fingerprint = receipt.get("fingerprint")
    if installed_fingerprint != plan.fingerprint:
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": "stale",
            "fingerprint": plan.fingerprint,
            "installed_fingerprint": installed_fingerprint,
            "deployment_root": str(root),
        }
    release = plan.release_dir
    static_checks = _validate_release_artifacts(release)
    if not static_checks["ok"]:
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": "invalid",
            "fingerprint": plan.fingerprint,
            "deployment_root": str(root),
            "static_checks": static_checks,
        }
    environment = _load_ros_environment((release / "setup.bash",), command_runner)
    required_packages = {
        package
        for plugin in plan.plugins
        if plugin.provider is not None
        for package in plugin.provider.packages
    }
    installed = _find_installed_ros_packages(environment, required_packages)
    missing_packages = sorted(required_packages - set(installed))
    static_checks["ros_packages"] = {
        "ok": not missing_packages,
        "missing": missing_packages,
        "found": sorted(installed),
    }
    if missing_packages:
        static_checks["ok"] = False
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": "invalid",
            "fingerprint": plan.fingerprint,
            "deployment_root": str(root),
            "release_dir": str(release),
            "static_checks": static_checks,
        }
    if not check_runtime:
        return {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "status": "installed",
            "fingerprint": plan.fingerprint,
            "deployment_root": str(root),
            "release_dir": str(release),
            "static_checks": static_checks,
            "runtime_checks": {"status": "not_checked", "checks": []},
        }
    runtime_checks = _run_readiness_checks(plan, environment, command_runner)
    return {
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "status": "ready" if runtime_checks["ok"] else "installed_not_ready",
        "fingerprint": plan.fingerprint,
        "deployment_root": str(root),
        "release_dir": str(release),
        "static_checks": static_checks,
        "runtime_checks": runtime_checks,
    }


def _validate_profile_files(profile: RuntimeDeploymentProfile) -> None:
    for setup_file in profile.ros_setup_files:
        if not setup_file.is_file():
            raise DeploymentError(
                f"ROS setup file does not exist: {setup_file}",
                code="ros_setup_missing",
            )
    for plugin_path in profile.plugin_paths:
        if not plugin_path.exists():
            raise DeploymentError(
                f"Plugin path does not exist: {plugin_path}",
                code="plugin_path_missing",
            )
    if profile.robot_launch is not None and not profile.robot_launch.is_file():
        raise DeploymentError(
            f"robot bringup launch does not exist: {profile.robot_launch}",
            code="robot_launch_missing",
        )
    mission_probe_files = {
        "mission_gateway_probe_ca_missing": profile.mission_gateway.tls_ca_file,
        "mission_gateway_probe_cert_missing": (
            profile.mission_gateway.tls_client_cert_file
        ),
        "mission_gateway_probe_key_missing": (
            profile.mission_gateway.tls_client_key_file
        ),
    }
    for code, path in mission_probe_files.items():
        if path is not None and not path.is_file():
            raise DeploymentError(
                f"managed Mission Gateway probe file does not exist: {path}",
                code=code,
            )


def _validate_plugin_config(plugin_id: str, config: Mapping[str, Any]) -> None:
    sensitive_fields = {
        "api_key",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                if not isinstance(raw_key, str):
                    raise DeploymentError(
                        f"Plugin {plugin_id} config keys must be strings",
                        code="plugin_config_invalid",
                    )
                normalized = raw_key.lower()
                indirect = normalized.endswith(("_env", "_file", "_path", "_ref"))
                inline_secret = normalized in sensitive_fields or any(
                    normalized.endswith(f"_{field}")
                    for field in sensitive_fields
                )
                if not indirect and inline_secret:
                    raise DeploymentError(
                        f"Plugin {plugin_id} config contains an inline credential field",
                        code="plugin_config_secret_forbidden",
                    )
                visit(item, (*path, raw_key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, (*path, str(index)))
            return
        if value is None or isinstance(value, (str, int, float, bool)):
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise DeploymentError(
                    f"Plugin {plugin_id} config is not data-only JSON-compatible",
                    code="plugin_config_invalid",
                ) from exc
            return
        raise DeploymentError(
            f"Plugin {plugin_id} config is not data-only JSON-compatible",
            code="plugin_config_invalid",
        )

    visit(config, ())


def _load_ros_environment(
    setup_files: Sequence[Path],
    runner: DeploymentCommandRunner,
) -> dict[str, str]:
    safe_environment = dict(os.environ)
    for key in ("BASH_ENV", "ENV", "CDPATH", "PROMPT_COMMAND"):
        safe_environment.pop(key, None)
    script = (
        'set -eo pipefail\n'
        'for setup_file in "$@"; do source "$setup_file"; done\n'
        'exec env -0'
    )
    result = runner.run(
        ("/bin/bash", "-c", script, "fireclaw-deploy", *(str(path) for path in setup_files)),
        timeout_seconds=15.0,
        max_output_bytes=2 * 1024 * 1024,
        env=safe_environment,
    )
    if not result.ok:
        raise DeploymentError(
            "ROS environment setup failed",
            code="ros_environment_failed",
        )
    environment: dict[str, str] = {}
    for record in result.output.split("\x00"):
        key, separator, value = record.partition("=")
        if separator and key:
            environment[key] = value
    if not environment:
        raise DeploymentError(
            "ROS environment setup returned no environment",
            code="ros_environment_failed",
        )
    return environment


def _find_installed_ros_packages(
    environment: Mapping[str, str],
    package_names: set[str],
) -> dict[str, RosPackageEvidence]:
    remaining = set(package_names)
    found: dict[str, RosPackageEvidence] = {}
    prefixes = [
        Path(value).expanduser().resolve(strict=False)
        for value in environment.get("CMAKE_PREFIX_PATH", "").split(os.pathsep)
        if value
    ]
    for prefix in prefixes:
        for package in sorted(remaining):
            package_dir = prefix / "share" / package
            package_xml = package_dir / "package.xml"
            if package_xml.is_file():
                found[package] = RosPackageEvidence(
                    name=package,
                    package_dir=package_dir.resolve(strict=False),
                    package_xml_sha256=sha256_file(package_xml),
                )
        remaining -= set(found)
        if not remaining:
            break
    return found


def _provider_compatible(
    provider: RuntimeProvider,
    profile: RuntimeDeploymentProfile,
) -> bool:
    return (
        provider.ros_distro == profile.ros_distro
        and (
            not provider.architectures
            or profile.architecture in provider.architectures
        )
    )


def _select_provider(
    descriptor: PluginRuntimeDescriptor,
    profile: RuntimeDeploymentProfile,
    installed_packages: Mapping[str, RosPackageEvidence],
) -> RuntimeProvider:
    system = [
        provider
        for provider in descriptor.providers
        if provider.kind == "system_ros1" and _provider_compatible(provider, profile)
    ]
    for provider in system:
        if all(package in installed_packages for package in provider.packages):
            return provider
    source = [
        provider
        for provider in descriptor.providers
        if provider.kind == "ros1_catkin" and _provider_compatible(provider, profile)
    ]
    if source:
        return source[0]
    raise DeploymentError(
        f"no compatible Runtime provider for {descriptor.runtime_id}",
        code="runtime_provider_unavailable",
    )


def _discover_source_packages(
    source_paths: Sequence[Path],
) -> dict[str, RosPackageEvidence]:
    found: dict[str, RosPackageEvidence] = {}
    for source_root in source_paths:
        for package_xml in sorted(source_root.rglob("package.xml")):
            if package_xml.is_symlink() or not package_xml.is_file():
                continue
            try:
                package_name = ElementTree.parse(package_xml).getroot().findtext("name")
            except ElementTree.ParseError as exc:
                raise DeploymentError(
                    f"invalid ROS package.xml under {source_root}",
                    code="package_manifest_invalid",
                ) from exc
            if package_name is None or not package_name.strip():
                raise DeploymentError(
                    f"ROS package.xml has no package name under {source_root}",
                    code="package_manifest_invalid",
                )
            name = package_name.strip()
            if name in found:
                raise DeploymentError(
                    f"duplicate ROS package {name} in Runtime source",
                    code="duplicate_source_package",
                )
            found[name] = RosPackageEvidence(
                name=name,
                package_dir=package_xml.parent.resolve(strict=True),
                package_xml_sha256=sha256_file(package_xml),
            )
    return found


def _hash_source_packages(packages: Sequence[RosPackageEvidence]) -> str:
    digest = sha256()
    file_count = 0
    byte_count = 0
    for package in sorted(packages, key=lambda item: item.name):
        digest.update(package.name.encode("utf-8") + b"\x00")
        for path in sorted(package.package_dir.rglob("*")):
            if path.is_symlink():
                raise DeploymentError(
                    f"Runtime source contains a symbolic link: {path}",
                    code="runtime_source_symlink",
                )
            if not path.is_file():
                continue
            file_count += 1
            byte_count += path.stat().st_size
            if file_count > MAX_SOURCE_FILES or byte_count > MAX_SOURCE_BYTES:
                raise DeploymentError(
                    "Runtime source exceeds deployment hashing bounds",
                    code="runtime_source_too_large",
                )
            relative = path.relative_to(package.package_dir).as_posix()
            digest.update(relative.encode("utf-8") + b"\x00")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _resolve_launch_arguments(
    descriptor: PluginRuntimeDescriptor,
    profile: RuntimeDeploymentProfile,
) -> Mapping[str, Any]:
    if descriptor.launch is None:
        return {}
    resolved: dict[str, Any] = {}
    for argument in descriptor.launch.arguments:
        if argument.binding in profile.bindings:
            value = profile.bindings[argument.binding]
        elif argument.default is not None:
            value = argument.default
        elif argument.required:
            raise DeploymentError(
                f"required deployment binding is missing: {argument.binding}",
                code="runtime_binding_missing",
            )
        else:
            continue
        validate_runtime_binding_value(value, argument.value_type, argument.binding)
        if argument.value_type == "path":
            path = Path(str(value)).expanduser()
            if not path.is_absolute():
                path = profile.profile_path.parent / path
            path = path.resolve(strict=False)
            if not path.is_file():
                raise DeploymentError(
                    f"deployment binding path does not exist: {argument.binding}",
                    code="runtime_binding_path_missing",
                )
            value = str(path)
        resolved[argument.name] = value
    return resolved


def _validate_readiness_bindings(
    descriptor: PluginRuntimeDescriptor,
    profile: RuntimeDeploymentProfile,
) -> None:
    for probe in descriptor.readiness:
        keys = tuple(
            key
            for key in (
                probe.binding,
                probe.parent_binding,
                probe.child_binding,
            )
            if key is not None
        )
        for key in keys:
            value = profile.bindings.get(key)
            if not isinstance(value, str) or not value.strip():
                raise DeploymentError(
                    f"readiness binding is missing or not a string: {key}",
                    code="runtime_binding_missing",
                )
            if probe.kind in {"ros1_node", "ros1_topic", "ros1_action"} and (
                not value.startswith("/") or any(character.isspace() for character in value)
            ):
                raise DeploymentError(
                    f"readiness binding is not an absolute ROS graph name: {key}",
                    code="runtime_binding_invalid",
                )


def _build_actions(
    profile: RuntimeDeploymentProfile,
    plugins: Sequence[PluginDeploymentPlan],
    fingerprint: str,
) -> tuple[dict[str, Any], ...]:
    actions: list[dict[str, Any]] = []
    source_plugins = [
        plugin.plugin_id
        for plugin in plugins
        if plugin.provider is not None and plugin.provider.kind == "ros1_catkin"
    ]
    for plugin in plugins:
        kind = plugin.provider.kind if plugin.provider is not None else "python_only"
        actions.append(
            {
                "action": (
                    "reuse_system_ros1"
                    if kind == "system_ros1"
                    else "stage_ros1_catkin"
                    if kind == "ros1_catkin"
                    else "register_python_plugin"
                ),
                "plugin_id": plugin.plugin_id,
                "provider": kind,
            }
        )
    if source_plugins:
        actions.append(
            {
                "action": "build_unified_catkin_install_space",
                "plugins": source_plugins,
            }
        )
    actions.extend(
        (
            {
                "action": "generate_unified_environment",
                "release": fingerprint,
            },
            {
                "action": "generate_robot_bringup",
                "plugin_count": len(plugins),
            },
            {
                "action": "write_deployment_receipt",
                "deployment_root": str(profile.deployment_root),
            },
        )
    )
    return tuple(actions)


def _materialize_release(
    stage: Path,
    plan: DeploymentPlan,
    runner: DeploymentCommandRunner,
) -> None:
    logs = stage / "logs"
    logs.mkdir(mode=0o700)
    source_packages = {
        package.name: package
        for plugin in plan.plugins
        for package in plugin.source_packages
    }
    if source_packages:
        workspace = stage / "workspace"
        source_space = workspace / "src"
        source_space.mkdir(parents=True)
        for package_name, package in sorted(source_packages.items()):
            (source_space / package_name).symlink_to(
                package.package_dir,
                target_is_directory=True,
            )
        environment = _load_ros_environment(plan.profile.ros_setup_files, runner)
        executable = shutil.which("catkin_make", path=environment.get("PATH"))
        if executable is None:
            raise DeploymentError(
                "catkin_make is unavailable in the configured ROS environment",
                code="catkin_make_missing",
            )
        install_space = stage / "install"
        result = runner.run(
            (
                executable,
                "-C",
                str(workspace),
                f"-DCMAKE_INSTALL_PREFIX={install_space}",
                "install",
            ),
            timeout_seconds=3600.0,
            max_output_bytes=16 * 1024 * 1024,
            env=environment,
            cwd=workspace,
        )
        (logs / "catkin_make.log").write_text(result.output, encoding="utf-8")
        if not result.ok:
            raise DeploymentError(
                "unified Catkin build failed; inspect logs/catkin_make.log",
                code="catkin_build_failed",
            )
        if not (install_space / "setup.bash").is_file():
            raise DeploymentError(
                "Catkin build did not produce install/setup.bash",
                code="catkin_install_missing",
            )

    _write_setup_script(stage, plan)
    _write_unified_launch(stage, plan)
    _write_generated_gateway_config(stage, plan)
    _write_wrappers(stage, plan)
    _write_systemd_service(stage, plan)
    _atomic_json(stage / "deployment-lock.json", plan.to_dict())
    _atomic_json(
        stage / "runtime-inventory.json",
        {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "generator_version": DEPLOYMENT_GENERATOR_VERSION,
            "deployment_id": plan.profile.deployment_id,
            "fingerprint": plan.fingerprint,
            "python_executable": str(plan.python_executable),
            "plugins": [plugin.to_dict() for plugin in plan.plugins],
        },
    )
    artifacts = _artifact_hashes(stage)
    _atomic_json(
        stage / "deployment-receipt.json",
        {
            "schema_version": DEPLOYMENT_SCHEMA_VERSION,
            "generator_version": DEPLOYMENT_GENERATOR_VERSION,
            "status": "installed",
            "deployment_id": plan.profile.deployment_id,
            "fingerprint": plan.fingerprint,
            "python_executable": str(plan.python_executable),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_path": str(plan.profile.profile_path),
            "profile_sha256": sha256_file(plan.profile.profile_path),
            "artifacts": artifacts,
        },
    )


def _write_setup_script(stage: Path, plan: DeploymentPlan) -> None:
    lines = ["#!/usr/bin/env bash", "set -e"]
    for setup_file in plan.profile.ros_setup_files:
        lines.append(f"source {shlex.quote(str(setup_file))}")
    if any(plugin.source_packages for plugin in plan.plugins):
        lines.append(f"source {shlex.quote(str(stage / 'install' / 'setup.bash'))}")
    lines.extend(
        (
            f"export FIRECLAW_DEPLOYMENT_ROOT={shlex.quote(str(plan.profile.deployment_root))}",
            f"export FIRECLAW_DEPLOYMENT_RECEIPT={shlex.quote(str(stage / 'deployment-receipt.json'))}",
        )
    )
    _write_executable(stage / "setup.bash", "\n".join(lines) + "\n")


def _write_unified_launch(stage: Path, plan: DeploymentPlan) -> None:
    lines = [
        '<?xml version="1.0"?>',
        "<!-- Generated by trusted FireClaw deployment code; not LLM-authored. -->",
        "<launch>",
    ]
    if plan.profile.robot_launch is not None:
        lines.append(f"  <include file={quoteattr(str(plan.profile.robot_launch))}/>")
    for plugin in plan.plugins:
        if plugin.descriptor is None or plugin.descriptor.launch is None:
            continue
        lines.append(f"  <!-- Plugin: {plugin.plugin_id} -->")
        lines.append(
            f"  <include file={quoteattr(str(plugin.descriptor.launch.file))}>"
        )
        for name, value in plugin.launch_arguments.items():
            lines.append(
                f"    <arg name={quoteattr(name)} value={quoteattr(_ros_launch_value(value))}/>")
        lines.append("  </include>")
    lines.append("</launch>")
    (stage / "fireclaw_bringup.launch").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def _write_generated_gateway_config(stage: Path, plan: DeploymentPlan) -> None:
    root = plan.profile.deployment_root
    values: dict[str, Any] = {
        "runtime": {"root_dir": str(root / "state")},
        "deployment": {"mode": plan.profile.mode},
        "robot_agent": {"enabled": True, "planner": "deterministic"},
        "plugins": {
            "paths": [str(plugin.candidate.root_dir) for plugin in plan.plugins],
            "config": {
                plugin.plugin_id: {
                    "enabled": True,
                    **dict(plan.profile.plugin_configs.get(plugin.plugin_id, {})),
                }
                for plugin in plan.plugins
            },
        },
        "robot_gateway": {
            "profile_path": str(plan.profile.profile_path),
            "dry_run": plan.profile.mode != "real",
        },
    }
    if plan.profile.robot_base_url:
        parsed = urlparse(plan.profile.robot_base_url)
        if parsed.hostname:
            values["robot_gateway"]["host"] = parsed.hostname
        if parsed.port:
            values["robot_gateway"]["port"] = parsed.port
    rendered = _render_toml(values)
    target = stage / "fireclaw.generated.toml"
    target.write_text(rendered, encoding="utf-8")
    target.chmod(0o600)


def _write_wrappers(stage: Path, plan: DeploymentPlan) -> None:
    bin_dir = stage / "bin"
    bin_dir.mkdir()
    setup = stage / "setup.bash"
    bringup = stage / "fireclaw_bringup.launch"
    generated_config = stage / "fireclaw.generated.toml"
    profile = plan.profile.profile_path
    output_root = plan.profile.output_root
    fireclaw_cli = (
        f"{shlex.quote(str(plan.python_executable))} -m fireclaw_core"
    )
    _write_executable(
        bin_dir / "fireclaw-bringup",
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -e",
                f"source {shlex.quote(str(setup))}",
                f"exec roslaunch {shlex.quote(str(bringup))} \"$@\"",
                "",
            )
        ),
    )
    _write_executable(
        bin_dir / "fireclaw-gateway",
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -e",
                f"source {shlex.quote(str(setup))}",
                (
                    f"{fireclaw_cli} deploy status "
                    f"--profile {shlex.quote(str(profile))} "
                    f"--output-root {shlex.quote(str(output_root))}"
                ),
                (
                    f"exec {fireclaw_cli} robot-gateway \"$@\" "
                    f"--config {shlex.quote(str(generated_config))} "
                    f"--robot-profile {shlex.quote(str(profile))}"
                ),
                "",
            )
        ),
    )
    if plan.profile.mission_gateway.enabled:
        _write_executable(
            bin_dir / "fireclaw-mission-gateway",
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    "set -e",
                    'if [[ "$#" -ne 0 ]]; then',
                    '  echo "fireclaw-mission-gateway accepts no runtime overrides" >&2',
                    "  exit 64",
                    "fi",
                    (
                        f"exec {fireclaw_cli} serve "
                        f"--config {shlex.quote(str(profile))} "
                        f"--robot-profile {shlex.quote(str(profile))}"
                    ),
                    "",
                )
            ),
        )
    _write_executable(
        bin_dir / "fireclaw-runtime",
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -e",
                (
                    f"exec {fireclaw_cli} deploy run \"$@\" "
                    f"--profile {shlex.quote(str(profile))} "
                    f"--output-root {shlex.quote(str(output_root))}"
                ),
                "",
            )
        ),
    )


def _write_systemd_service(stage: Path, plan: DeploymentPlan) -> None:
    systemd_dir = stage / "systemd"
    systemd_dir.mkdir()
    unit = systemd_dir / systemd_unit_name(plan.profile)
    unit.write_text(
        render_systemd_user_unit(plan.profile),
        encoding="utf-8",
    )
    unit.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _render_toml(value: Mapping[str, Any]) -> str:
    lines: list[str] = []

    def emit(table: Mapping[str, Any], path: tuple[str, ...]) -> None:
        scalars = [(key, item) for key, item in table.items() if not isinstance(item, Mapping)]
        children = [(key, item) for key, item in table.items() if isinstance(item, Mapping)]
        if path:
            lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")
        for key, item in scalars:
            lines.append(f"{_toml_key(key)} = {_toml_value(item)}")
        if path or scalars:
            lines.append("")
        for key, child in children:
            emit(child, (*path, key))

    emit(value, ())
    return "\n".join(lines).rstrip() + "\n"


def _toml_key(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise DeploymentError(
        "generated Gateway config contains an unsupported value",
        code="generated_config_invalid",
    )


def _ros_launch_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(
        stat.S_IRUSR
        | stat.S_IWUSR
        | stat.S_IXUSR
        | stat.S_IRGRP
        | stat.S_IXGRP
        | stat.S_IROTH
        | stat.S_IXOTH
    )


def _artifact_hashes(root: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "deployment-receipt.json":
            continue
        relative = path.relative_to(root)
        # Catkin build/devel intermediates are retained for diagnosis but are
        # not runtime inputs.  The immutable source digest and install-space
        # files are recorded instead, keeping receipts bounded and relevant.
        if relative.parts and relative.parts[0] == "workspace":
            continue
        artifacts[relative.as_posix()] = sha256_file(path)
    return artifacts


def _validate_existing_release(release: Path, fingerprint: str) -> None:
    receipt_path = release / "deployment-receipt.json"
    if not receipt_path.is_file():
        raise DeploymentError(
            "content-addressed release exists without a receipt",
            code="release_incomplete",
        )
    receipt = _read_json_object(receipt_path)
    if receipt.get("fingerprint") != fingerprint:
        raise DeploymentError(
            "content-addressed release receipt fingerprint mismatch",
            code="release_invalid",
        )
    checks = _validate_release_artifacts(release)
    if not checks["ok"]:
        raise DeploymentError(
            "content-addressed release artifact hashes do not match",
            code="release_invalid",
        )


def _validate_release_artifacts(release: Path) -> dict[str, Any]:
    receipt_path = release / "deployment-receipt.json"
    if not receipt_path.is_file():
        return {"ok": False, "missing": ["deployment-receipt.json"], "mismatched": []}
    receipt = _read_json_object(receipt_path)
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        return {"ok": False, "missing": [], "mismatched": ["receipt.artifacts"]}
    missing: list[str] = []
    mismatched: list[str] = []
    for relative, expected in artifacts.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            mismatched.append("receipt.artifacts")
            continue
        path = (release / relative).resolve(strict=False)
        try:
            path.relative_to(release)
        except ValueError:
            mismatched.append(relative)
            continue
        if not path.is_file():
            missing.append(relative)
        elif sha256_file(path) != expected:
            mismatched.append(relative)
    return {"ok": not missing and not mismatched, "missing": missing, "mismatched": mismatched}


def _activate_release(root: Path, release: Path, plan: DeploymentPlan) -> None:
    current = root / "current"
    temporary = root / f".current-{uuid4().hex}"
    relative = Path("releases") / release.name
    temporary.symlink_to(relative, target_is_directory=True)
    temporary.replace(current)
    release_receipt = _read_json_object(release / "deployment-receipt.json")
    _atomic_json(
        root / "deployment-receipt.json",
        {
            **release_receipt,
            "release_dir": str(release),
            "current": str(current),
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "profile_path": str(plan.profile.profile_path),
        },
    )


def _apply_result(
    plan: DeploymentPlan,
    release: Path,
    *,
    reused: bool,
) -> dict[str, Any]:
    return {
        "schema_version": DEPLOYMENT_SCHEMA_VERSION,
        "status": "installed",
        "reused": reused,
        "fingerprint": plan.fingerprint,
        "deployment_root": str(plan.profile.deployment_root),
        "release_dir": str(release),
        "setup": str(release / "setup.bash"),
        "bringup": str(release / "bin" / "fireclaw-bringup"),
        "gateway": str(release / "bin" / "fireclaw-gateway"),
        "mission_gateway": (
            str(release / "bin" / "fireclaw-mission-gateway")
            if plan.profile.mission_gateway.enabled
            else None
        ),
        "systemd_unit": str(
            release / "systemd" / systemd_unit_name(plan.profile)
        ),
        "runtime": str(release / "bin" / "fireclaw-runtime"),
    }


def _run_readiness_checks(
    plan: DeploymentPlan,
    environment: Mapping[str, str],
    runner: DeploymentCommandRunner,
) -> dict[str, Any]:
    probes = [
        (plugin, probe)
        for plugin in plan.plugins
        if plugin.descriptor is not None
        for probe in plugin.descriptor.readiness
    ]
    nodes: set[str] | None = None
    topics: set[str] | None = None
    node_error: str | None = None
    topic_error: str | None = None
    if any(probe.kind == "ros1_node" for _, probe in probes):
        nodes, node_error = _ros_graph_names("rosnode", environment, runner)
    if any(probe.kind in {"ros1_topic", "ros1_action"} for _, probe in probes):
        topics, topic_error = _ros_graph_names("rostopic", environment, runner)
    installed = _find_installed_ros_packages(
        environment,
        {probe.name for _, probe in probes if probe.kind == "ros1_package" and probe.name},
    )
    checks: list[dict[str, Any]] = []
    for plugin, probe in probes:
        value = _readiness_value(probe, plan.profile.bindings)
        ok = False
        detail: str | None = None
        if probe.kind == "ros1_package":
            ok = bool(probe.name and probe.name in installed)
        elif probe.kind == "ros1_node":
            ok = nodes is not None and value in nodes
            detail = node_error
        elif probe.kind == "ros1_topic":
            ok = topics is not None and value in topics
            detail = topic_error
        elif probe.kind == "ros1_action":
            namespace = value.rstrip("/")
            required = {f"{namespace}/goal", f"{namespace}/status", f"{namespace}/cancel"}
            ok = topics is not None and required.issubset(topics)
            detail = topic_error
        elif probe.kind == "ros1_tf":
            ok, detail = _check_tf(probe, plan.profile.bindings, environment, runner)
        checks.append(
            {
                "plugin_id": plugin.plugin_id,
                "kind": probe.kind,
                "target": value,
                "ok": ok,
                "detail": detail,
            }
        )
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def _ros_graph_names(
    executable_name: str,
    environment: Mapping[str, str],
    runner: DeploymentCommandRunner,
) -> tuple[set[str] | None, str | None]:
    executable = shutil.which(executable_name, path=environment.get("PATH"))
    if executable is None:
        return None, f"{executable_name} is unavailable"
    result = runner.run(
        (executable, "list"),
        timeout_seconds=5.0,
        max_output_bytes=512 * 1024,
        env=environment,
    )
    if not result.ok:
        return None, f"{executable_name} list failed"
    return {line.strip() for line in result.output.splitlines() if line.strip()}, None


def _readiness_value(
    probe: RuntimeReadinessProbe,
    bindings: Mapping[str, Any],
) -> str:
    if probe.name is not None:
        return probe.name
    if probe.binding is not None:
        value = bindings.get(probe.binding)
        return str(value) if value is not None else f"missing:{probe.binding}"
    if probe.kind == "ros1_tf":
        parent = bindings.get(probe.parent_binding or "")
        child = bindings.get(probe.child_binding or "")
        return f"{parent}->{child}"
    return "unknown"


def _check_tf(
    probe: RuntimeReadinessProbe,
    bindings: Mapping[str, Any],
    environment: Mapping[str, str],
    runner: DeploymentCommandRunner,
) -> tuple[bool, str | None]:
    parent = bindings.get(probe.parent_binding or "")
    child = bindings.get(probe.child_binding or "")
    if not isinstance(parent, str) or not isinstance(child, str):
        return False, "TF frame binding is missing"
    rosrun = shutil.which("rosrun", path=environment.get("PATH"))
    if rosrun is None:
        return False, "rosrun is unavailable"
    result = runner.run(
        (rosrun, "tf", "tf_echo", parent, child),
        timeout_seconds=2.5,
        max_output_bytes=128 * 1024,
        env=environment,
    )
    observed = "Translation:" in result.output and "Rotation:" in result.output
    return observed, None if observed else "TF transform was not observed"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DeploymentError(
            f"deployment JSON document is not an object: {path.name}",
            code="deployment_receipt_invalid",
        )
    return value


__all__ = [
    "DEPLOYMENT_SCHEMA_VERSION",
    "DeploymentError",
    "DeploymentPlan",
    "PluginDeploymentPlan",
    "RosPackageEvidence",
    "apply_deployment",
    "build_deployment_plan",
    "inspect_deployment_status",
]
