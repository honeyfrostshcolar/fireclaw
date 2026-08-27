"""Verified materialization and resumable build support for simulation releases."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import tarfile
from typing import Any, Mapping
from uuid import uuid4

from fireclaw_core import __version__
from fireclaw_core.deployment.command import (
    DeploymentCommandRunner,
    SubprocessDeploymentCommandRunner,
)
from fireclaw_core.evaluation.artifacts import canonical_json_sha256, sha256_file
from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root
from fireclaw_core.resources import (
    load_simulation_bundle_catalog,
    validate_simulation_bundle_catalog,
)


SIMULATION_BUNDLE_ID = "turtlebot3-burger-v1"
SIMULATION_BUNDLE_ENV = "FIRECLAW_SIMULATION_BUNDLE"
BUNDLE_RECEIPT_SCHEMA_VERSION = 1
WORKSPACE_RECEIPT_SCHEMA_VERSION = 1
WORKSPACE_BUILDER_VERSION = 2
DETERMINISTIC_BUNDLE_MTIME = 1700000000
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PROVENANCE_EVIDENCE_BYTES = 2 * 1024 * 1024
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class SimulationRuntimeError(RuntimeError):
    """A fail-closed simulation preparation error with an operator action."""

    def __init__(self, message: str, *, code: str, operator_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.operator_action = operator_action


@dataclass(frozen=True)
class SimulationBundleRelease:
    bundle_id: str
    bundle_version: str
    archive_sha256: str
    archive_path: Path
    release_dir: Path
    receipt_path: Path
    manifest: Mapping[str, Any]
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUNDLE_RECEIPT_SCHEMA_VERSION,
            "bundle_id": self.bundle_id,
            "bundle_version": self.bundle_version,
            "archive_sha256": self.archive_sha256,
            "archive_path": str(self.archive_path),
            "release_dir": str(self.release_dir),
            "receipt_path": str(self.receipt_path),
            "reused": self.reused,
        }


@dataclass(frozen=True)
class SimulationWorkspaceRelease:
    fingerprint: str
    release_dir: Path
    install_setup: Path
    receipt_path: Path
    reused: bool
    resumed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_RECEIPT_SCHEMA_VERSION,
            "fingerprint": self.fingerprint,
            "release_dir": str(self.release_dir),
            "install_setup": str(self.install_setup),
            "receipt_path": str(self.receipt_path),
            "reused": self.reused,
            "resumed": self.resumed,
        }


def prepare_simulation_runtime(
    *,
    runtime_root: str | Path | None,
    bundle_path: str | Path | None = None,
    source_root: str | Path | None = None,
    bundle_id: str = SIMULATION_BUNDLE_ID,
    catalog: Mapping[str, Any] | None = None,
    workspace_runner: DeploymentCommandRunner | None = None,
    catkin_executable: str | Path | None = None,
    ros_setup: str | Path = "/opt/ros/noetic/setup.bash",
) -> dict[str, Any]:
    """Resolve, verify, materialize, and build one versioned simulation runtime."""

    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    _ensure_private_directory(root)
    resolved_catalog = dict(catalog or load_simulation_bundle_catalog(bundle_id))
    validate_simulation_bundle_catalog(
        resolved_catalog,
        expected_bundle_id=bundle_id,
    )
    _validate_fireclaw_compatibility(resolved_catalog)
    archive = resolve_simulation_bundle(
        runtime_root=root,
        bundle_path=bundle_path,
        source_root=source_root,
        catalog=resolved_catalog,
    )
    bundle = materialize_simulation_bundle(
        archive,
        runtime_root=root,
        catalog=resolved_catalog,
    )
    workspace = build_simulation_workspace(
        bundle,
        runtime_root=root,
        ros_setup=ros_setup,
        runner=workspace_runner,
        catkin_executable=catkin_executable,
    )
    return {
        "schema_version": 1,
        "status": "ready",
        "bundle": bundle.to_dict(),
        "workspace": workspace.to_dict(),
        "bundle_root": str(bundle.release_dir),
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "bundle_sha256": bundle.archive_sha256,
        "bundle_receipt": str(bundle.receipt_path),
        "workspace_fingerprint": workspace.fingerprint,
        "workspace_receipt": str(workspace.receipt_path),
        "workspace_setup": str(workspace.install_setup),
    }


def resolve_simulation_bundle(
    *,
    runtime_root: str | Path,
    catalog: Mapping[str, Any],
    bundle_path: str | Path | None = None,
    source_root: str | Path | None = None,
) -> Path:
    """Resolve the companion archive without guessing an unverified download URL."""

    validate_simulation_bundle_catalog(catalog)
    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    artifact = catalog["artifact"]
    assert isinstance(artifact, Mapping)
    filename = str(artifact["filename"])

    explicit = bundle_path is not None
    candidates: list[Path] = []
    if bundle_path is not None:
        candidates.append(Path(bundle_path).expanduser())
    else:
        configured = os.environ.get(SIMULATION_BUNDLE_ENV)
        if configured:
            candidates.append(Path(configured).expanduser())
        candidates.extend((Path.cwd() / filename, root / "downloads" / filename))

    for candidate in candidates:
        if not candidate.exists():
            if explicit:
                raise SimulationRuntimeError(
                    f"Simulation bundle does not exist: {candidate}",
                    code="simulation_bundle_missing",
                    operator_action=(
                        "获取与当前 FireClaw wheel 同版本的 companion simulation bundle，"
                        "再使用 --simulation-bundle 指向该文件。"
                    ),
                )
            continue
        return _validate_bundle_artifact(candidate, catalog)

    if source_root is not None:
        generated = _build_source_checkout_bundle(
            Path(source_root).expanduser(),
            runtime_root=root,
            catalog=catalog,
        )
        return _validate_bundle_artifact(generated, catalog)

    raise SimulationRuntimeError(
        f"Could not find the versioned companion simulation bundle {filename}.",
        code="simulation_bundle_missing",
        operator_action=(
            f"将 {filename} 放在当前目录，设置 {SIMULATION_BUNDLE_ENV}，或运行 "
            f"fireclaw setup --simulation-bundle <{filename}>。"
        ),
    )


def materialize_simulation_bundle(
    archive_path: str | Path,
    *,
    runtime_root: str | Path,
    catalog: Mapping[str, Any],
) -> SimulationBundleRelease:
    """Verify every archive member and atomically publish an immutable release."""

    validate_simulation_bundle_catalog(catalog)
    archive = _validate_bundle_artifact(Path(archive_path), catalog)
    artifact = catalog["artifact"]
    assert isinstance(artifact, Mapping)
    archive_sha256 = str(artifact["sha256"])
    bundle_id = str(catalog["bundle_id"])
    bundle_version = str(catalog["bundle_version"])
    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    release_parent = root / "simulation-bundles" / bundle_id / "releases"
    _ensure_private_tree(root, release_parent)
    release_dir = release_parent / archive_sha256
    receipt_path = release_dir / "bundle-receipt.json"

    if release_dir.exists():
        manifest = _validate_materialized_bundle_release(
            release_dir,
            catalog=catalog,
            archive_sha256=archive_sha256,
        )
        _activate_current_release(release_parent.parent, release_dir)
        return SimulationBundleRelease(
            bundle_id=bundle_id,
            bundle_version=bundle_version,
            archive_sha256=archive_sha256,
            archive_path=archive,
            release_dir=release_dir,
            receipt_path=receipt_path,
            manifest=manifest,
            reused=True,
        )

    stage = release_parent / f".{archive_sha256}.{uuid4().hex}.stage"
    stage.mkdir(mode=0o700)
    try:
        manifest = _verify_and_extract_archive(
            archive,
            stage,
            catalog=catalog,
        )
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        manifest_path = stage / "manifest.json"
        _write_bytes_exclusive(manifest_path, manifest_bytes, mode=0o600)
        receipt = {
            "schema_version": BUNDLE_RECEIPT_SCHEMA_VERSION,
            "status": "materialized",
            "bundle_id": bundle_id,
            "bundle_version": bundle_version,
            "archive_filename": archive.name,
            "archive_sha256": archive_sha256,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "materialized_at": datetime.now(timezone.utc).isoformat(),
            "files": manifest["files"],
        }
        _atomic_json(stage / "bundle-receipt.json", receipt)
        _freeze_materialized_release(stage)
        try:
            stage.replace(release_dir)
        except FileExistsError:
            # Another setup process may have published the same immutable release.
            manifest = _validate_materialized_bundle_release(
                release_dir,
                catalog=catalog,
                archive_sha256=archive_sha256,
            )
    except BaseException:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise

    _activate_current_release(release_parent.parent, release_dir)
    return SimulationBundleRelease(
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        archive_sha256=archive_sha256,
        archive_path=archive,
        release_dir=release_dir,
        receipt_path=receipt_path,
        manifest=manifest,
        reused=False,
    )


def build_simulation_workspace(
    bundle: SimulationBundleRelease,
    *,
    runtime_root: str | Path,
    ros_setup: str | Path = "/opt/ros/noetic/setup.bash",
    runner: DeploymentCommandRunner | None = None,
    catkin_executable: str | Path | None = None,
) -> SimulationWorkspaceRelease:
    """Build a content-addressed Catkin install space and resume interrupted builds."""

    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    ros_setup_path = _regular_non_symlink_file(
        Path(ros_setup).expanduser(),
        code="simulation_ros_setup_missing",
        label="ROS setup file",
    )
    source_dir = (
        bundle.release_dir / "robots" / "turtlebot3_burger" / "ros_ws" / "src"
    )
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise SimulationRuntimeError(
            f"Materialized simulation workspace source is missing: {source_dir}",
            code="simulation_workspace_source_missing",
            operator_action="重新获取并校验 companion simulation bundle。",
        )

    fingerprint_payload = {
        "schema_version": WORKSPACE_RECEIPT_SCHEMA_VERSION,
        "builder_version": WORKSPACE_BUILDER_VERSION,
        "bundle_id": bundle.bundle_id,
        "bundle_version": bundle.bundle_version,
        "bundle_sha256": bundle.archive_sha256,
        "ros_distro": "noetic",
        "ros_setup_path": str(ros_setup_path),
        "ros_setup_sha256": sha256_file(ros_setup_path),
        "architecture": platform.machine(),
    }
    fingerprint = canonical_json_sha256(fingerprint_payload)
    workspace_root = root / "simulation-workspaces" / bundle.bundle_id
    releases = workspace_root / "releases"
    _ensure_private_tree(root, releases)
    release_dir = releases / fingerprint
    receipt_path = release_dir / "workspace-receipt.json"
    install_setup = release_dir / "install" / "setup.bash"

    if release_dir.exists() and _workspace_receipt_is_valid(
        receipt_path,
        fingerprint=fingerprint,
        bundle=bundle,
        install_setup=install_setup,
    ):
        _activate_current_release(workspace_root, release_dir)
        return SimulationWorkspaceRelease(
            fingerprint=fingerprint,
            release_dir=release_dir,
            install_setup=install_setup,
            receipt_path=receipt_path,
            reused=True,
            resumed=False,
        )

    resumed = release_dir.exists()
    if release_dir.is_symlink():
        raise SimulationRuntimeError(
            "Simulation workspace release path must not be a symbolic link.",
            code="simulation_workspace_path_unsafe",
            operator_action="移除 runtime root 中不受信任的符号链接后重试。",
        )
    release_dir.mkdir(mode=0o700, exist_ok=True)
    workspace_source = release_dir / "src"
    if workspace_source.is_symlink():
        raise SimulationRuntimeError(
            "Resumable simulation workspace source must not be a symbolic link.",
            code="simulation_workspace_source_changed",
            operator_action="保留日志并移走冲突的 workspace release 后重试。",
        )
    if workspace_source.exists():
        if not workspace_source.is_dir():
            raise SimulationRuntimeError(
                "Resumable simulation workspace source is not a directory.",
                code="simulation_workspace_source_changed",
                operator_action="保留日志并移走冲突的 workspace release 后重试。",
            )
        # catkin_make creates src/CMakeLists.txt when it initializes a workspace.
        # Rehydrate the private working copy on every build attempt so that a
        # failed/resumed build cannot mutate the immutable bundle release or
        # carry modified sources into the next attempt.
        shutil.rmtree(workspace_source)
    shutil.copytree(source_dir, workspace_source, symlinks=False)
    _make_workspace_source_writable(workspace_source)

    command_runner = runner or SubprocessDeploymentCommandRunner()
    environment = _load_ros_environment(ros_setup_path, command_runner)
    environment["SETUPTOOLS_USE_DISTUTILS"] = "stdlib"
    executable = (
        str(Path(catkin_executable).expanduser())
        if catkin_executable is not None
        else shutil.which("catkin_make", path=environment.get("PATH"))
    )
    if not executable:
        raise SimulationRuntimeError(
            "catkin_make is unavailable in the configured ROS environment.",
            code="simulation_catkin_make_missing",
            operator_action="安装 ROS Noetic catkin 工具链后重新运行同一 setup 命令。",
        )

    logs = release_dir / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    result = command_runner.run(
        (
            executable,
            "-C",
            str(release_dir),
            f"-DCMAKE_INSTALL_PREFIX={release_dir / 'install'}",
            "install",
        ),
        timeout_seconds=3600.0,
        max_output_bytes=16 * 1024 * 1024,
        env=environment,
        cwd=release_dir,
    )
    (logs / "catkin_make.log").write_text(result.output, encoding="utf-8")
    if not result.ok:
        _atomic_json(
            receipt_path,
            {
                "schema_version": WORKSPACE_RECEIPT_SCHEMA_VERSION,
                "status": "failed",
                "fingerprint": fingerprint,
                "bundle_sha256": bundle.archive_sha256,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "resumable": True,
                "command": list(result.argv),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "truncated": result.truncated,
                "error_code": result.error_code,
                "log_path": str(logs / "catkin_make.log"),
            },
        )
        raise SimulationRuntimeError(
            "Simulation Catkin workspace build did not complete.",
            code="simulation_workspace_build_failed",
            operator_action=(
                f"检查 {logs / 'catkin_make.log'}，修复依赖后重新运行同一 setup 命令；"
                "现有构建目录会继续使用。"
            ),
        )
    install_setup = _regular_non_symlink_file(
        install_setup,
        code="simulation_workspace_install_missing",
        label="Catkin install setup",
    )
    _atomic_json(
        receipt_path,
        {
            "schema_version": WORKSPACE_RECEIPT_SCHEMA_VERSION,
            "status": "installed",
            "builder_version": WORKSPACE_BUILDER_VERSION,
            "fingerprint": fingerprint,
            "fingerprint_inputs": fingerprint_payload,
            "bundle_id": bundle.bundle_id,
            "bundle_version": bundle.bundle_version,
            "bundle_sha256": bundle.archive_sha256,
            "bundle_release_dir": str(bundle.release_dir),
            "source_dir": str(source_dir),
            "workspace_source_dir": str(workspace_source),
            "ros_setup": str(ros_setup_path),
            "install_setup": str(install_setup),
            "install_setup_sha256": sha256_file(install_setup),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "resumed": resumed,
            "command": list(result.argv),
            "log_path": str(logs / "catkin_make.log"),
        },
    )
    _activate_current_release(workspace_root, release_dir)
    return SimulationWorkspaceRelease(
        fingerprint=fingerprint,
        release_dir=release_dir,
        install_setup=install_setup,
        receipt_path=receipt_path,
        reused=False,
        resumed=resumed,
    )


def _validate_bundle_artifact(
    candidate: Path,
    catalog: Mapping[str, Any],
) -> Path:
    if candidate.is_symlink():
        raise SimulationRuntimeError(
            "Simulation bundle path must not be a symbolic link.",
            code="simulation_bundle_path_unsafe",
            operator_action="使用下载后的普通 companion bundle 文件，不要使用符号链接。",
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SimulationRuntimeError(
            f"Simulation bundle does not exist: {candidate}",
            code="simulation_bundle_missing",
            operator_action="检查 --simulation-bundle 路径后重试。",
        ) from exc
    if not resolved.is_file() or not stat.S_ISREG(resolved.stat().st_mode):
        raise SimulationRuntimeError(
            "Simulation bundle must be a regular file.",
            code="simulation_bundle_path_unsafe",
            operator_action="使用普通 .tar.gz companion artifact。",
        )
    artifact = catalog["artifact"]
    assert isinstance(artifact, Mapping)
    expected_name = str(artifact["filename"])
    if resolved.name != expected_name:
        raise SimulationRuntimeError(
            f"Simulation bundle filename mismatch: expected {expected_name}, got {resolved.name}.",
            code="simulation_bundle_filename_mismatch",
            operator_action="使用 catalog 指定名称的 companion artifact。",
        )
    max_compressed = int(catalog["size_budget_compressed_bytes"])
    if resolved.stat().st_size > max_compressed:
        raise SimulationRuntimeError(
            "Simulation bundle exceeds its compressed size budget.",
            code="simulation_bundle_too_large",
            operator_action="删除该文件并重新获取官方 companion artifact。",
        )
    actual = sha256_file(resolved)
    expected = str(artifact["sha256"])
    if actual != expected:
        raise SimulationRuntimeError(
            f"Simulation bundle SHA-256 mismatch: expected {expected}, got {actual}.",
            code="simulation_bundle_digest_mismatch",
            operator_action="不要运行该归档；重新获取与当前 wheel 同版本的 companion artifact。",
        )
    return resolved


def _verify_and_extract_archive(
    archive: Path,
    stage: Path,
    *,
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    max_unpacked = int(catalog["size_budget_unpacked_bytes"])
    include_paths = list(catalog["include_paths"])
    required_paths = set(catalog["required_paths"]) | set(
        catalog["provenance_required_paths"]
    )
    forbidden_segments = set(catalog["forbidden_path_segments"])
    forbidden_suffixes = set(catalog["forbidden_file_suffixes"])
    evidence_paths = {
        value
        for source in catalog["upstream_sources"]
        for value in source["evidence_paths"]
    }

    try:
        tar = tarfile.open(archive, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise SimulationRuntimeError(
            "Simulation bundle is not a readable tar.gz archive.",
            code="simulation_bundle_archive_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        ) from exc
    with tar:
        members = tar.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise SimulationRuntimeError(
                "Simulation bundle contains too many archive members.",
                code="simulation_bundle_archive_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise SimulationRuntimeError(
                "Simulation bundle contains duplicate archive member names.",
                code="simulation_bundle_archive_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        if not members or names[0] != "manifest.json":
            raise SimulationRuntimeError(
                "Simulation bundle must begin with manifest.json.",
                code="simulation_bundle_manifest_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        if names != ["manifest.json", *sorted(names[1:])]:
            raise SimulationRuntimeError(
                "Simulation bundle member order is not deterministic.",
                code="simulation_bundle_archive_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        total_declared = 0
        for member in members:
            if not member.isfile() or not _safe_archive_path(member.name):
                raise SimulationRuntimeError(
                    f"Simulation bundle contains an unsafe member: {member.name!r}.",
                    code="simulation_bundle_archive_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            total_declared += member.size
            if member.size < 0 or total_declared > max_unpacked:
                raise SimulationRuntimeError(
                    "Simulation bundle exceeds its unpacked size budget.",
                    code="simulation_bundle_too_large",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            if (
                member.mtime != DETERMINISTIC_BUNDLE_MTIME
                or member.uid != 0
                or member.gid != 0
                or member.uname != "root"
                or member.gname != "root"
            ):
                raise SimulationRuntimeError(
                    f"Simulation bundle member metadata is not deterministic: {member.name}.",
                    code="simulation_bundle_archive_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )

        manifest_member = members[0]
        if manifest_member.size > min(MAX_MANIFEST_BYTES, max_unpacked):
            raise SimulationRuntimeError(
                "Simulation bundle manifest is too large.",
                code="simulation_bundle_manifest_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        manifest_raw = _read_tar_member(tar, manifest_member, MAX_MANIFEST_BYTES)
        manifest = _load_json_object(manifest_raw, label="simulation bundle manifest")
        _validate_manifest_header(manifest, catalog, payload_members=members[1:])
        manifest_files = manifest["files"]
        assert isinstance(manifest_files, Mapping)
        payload_names = set(names[1:])
        if set(manifest_files) != payload_names:
            raise SimulationRuntimeError(
                "Simulation bundle manifest and archive file sets differ.",
                code="simulation_bundle_manifest_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        if not required_paths.issubset(payload_names):
            raise SimulationRuntimeError(
                "Simulation bundle is missing required runtime or provenance files.",
                code="simulation_bundle_required_files_missing",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )

        provenance: dict[str, bytes] = {}
        captured_evidence_bytes = 0
        payload_bytes = 0
        for member in members[1:]:
            name = member.name
            if not _path_is_included(name, include_paths) or _path_is_forbidden(
                name,
                forbidden_segments,
                forbidden_suffixes,
            ):
                raise SimulationRuntimeError(
                    f"Simulation bundle member is outside the catalog allowlist: {name}.",
                    code="simulation_bundle_archive_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            metadata = manifest_files.get(name)
            if not isinstance(metadata, Mapping):
                raise SimulationRuntimeError(
                    f"Simulation bundle manifest metadata is invalid for {name}.",
                    code="simulation_bundle_manifest_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            expected_size = metadata.get("size")
            expected_mode = metadata.get("mode")
            expected_sha = metadata.get("sha256")
            if (
                not isinstance(expected_size, int)
                or isinstance(expected_size, bool)
                or expected_size != member.size
                or expected_mode not in {"0o644", "0o755"}
                or oct(member.mode & 0o777) != expected_mode
                or not isinstance(expected_sha, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
            ):
                raise SimulationRuntimeError(
                    f"Simulation bundle manifest metadata mismatch for {name}.",
                    code="simulation_bundle_manifest_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            source = tar.extractfile(member)
            if source is None:
                raise SimulationRuntimeError(
                    f"Simulation bundle member cannot be read: {name}.",
                    code="simulation_bundle_archive_invalid",
                    operator_action="重新获取经过发布门禁的 companion artifact。",
                )
            destination = stage.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            digest = hashlib.sha256()
            captured = bytearray()
            written = 0
            with destination.open("xb") as output:
                while True:
                    chunk = source.read(min(1024 * 1024, member.size - written + 1))
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > member.size:
                        raise SimulationRuntimeError(
                            f"Simulation bundle member exceeded its declared size: {name}.",
                            code="simulation_bundle_archive_invalid",
                            operator_action="重新获取经过发布门禁的 companion artifact。",
                        )
                    digest.update(chunk)
                    output.write(chunk)
                    if name in evidence_paths:
                        captured.extend(chunk)
                output.flush()
                os.fsync(output.fileno())
            if written != member.size or digest.hexdigest() != expected_sha:
                raise SimulationRuntimeError(
                    f"Simulation bundle content digest mismatch for {name}.",
                    code="simulation_bundle_digest_mismatch",
                    operator_action="不要使用该归档；重新获取官方 companion artifact。",
                )
            destination.chmod(int(str(expected_mode), 8))
            payload_bytes += written
            if name in evidence_paths:
                captured_evidence_bytes += len(captured)
                if captured_evidence_bytes > MAX_PROVENANCE_EVIDENCE_BYTES:
                    raise SimulationRuntimeError(
                        "Simulation bundle provenance evidence exceeds its capture budget.",
                        code="simulation_bundle_archive_invalid",
                        operator_action="重新获取经过发布门禁的 companion artifact。",
                    )
                provenance[name] = bytes(captured)

        if manifest.get("unpacked_bytes") != payload_bytes:
            raise SimulationRuntimeError(
                "Simulation bundle manifest unpacked_bytes does not match payload.",
                code="simulation_bundle_manifest_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        _validate_provenance(catalog, provenance)
        return dict(manifest)


def _validate_manifest_header(
    manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    payload_members: list[tarfile.TarInfo],
) -> None:
    files = manifest.get("files")
    valid = (
        manifest.get("schema_version") == 1
        and not isinstance(manifest.get("schema_version"), bool)
        and manifest.get("bundle_id") == catalog["bundle_id"]
        and manifest.get("bundle_version") == catalog["bundle_version"]
        and manifest.get("total_files") == len(payload_members)
        and isinstance(files, Mapping)
    )
    if not valid:
        raise SimulationRuntimeError(
            "Simulation bundle manifest identity or file count is invalid.",
            code="simulation_bundle_manifest_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        )


def _validate_provenance(
    catalog: Mapping[str, Any],
    evidence: Mapping[str, bytes],
) -> None:
    for source in catalog["upstream_sources"]:
        blobs = [evidence.get(path, b"") for path in source["evidence_paths"]]
        combined = b"\n".join(blobs)
        if not all(blob.strip() for blob in blobs):
            raise SimulationRuntimeError(
                f"Simulation bundle provenance evidence is missing for {source['source_id']}.",
                code="simulation_bundle_provenance_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        if (
            str(source["revision"]).encode("ascii") not in combined
            or str(source["repository"]).encode("utf-8") not in combined
        ):
            raise SimulationRuntimeError(
                f"Simulation bundle provenance does not bind {source['source_id']} to its catalog revision.",
                code="simulation_bundle_provenance_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )


def _validate_materialized_bundle_release(
    release_dir: Path,
    *,
    catalog: Mapping[str, Any],
    archive_sha256: str,
) -> dict[str, Any]:
    if release_dir.is_symlink() or not release_dir.is_dir():
        raise SimulationRuntimeError(
            "Materialized simulation release is not a trusted directory.",
            code="simulation_bundle_release_invalid",
            operator_action="移走冲突路径后重新运行 setup。",
        )
    receipt_path = release_dir / "bundle-receipt.json"
    manifest_path = release_dir / "manifest.json"
    receipt = _read_json_file(receipt_path, label="simulation bundle receipt")
    manifest = _read_json_file(manifest_path, label="materialized simulation manifest")
    if (
        receipt.get("schema_version") != BUNDLE_RECEIPT_SCHEMA_VERSION
        or receipt.get("status") != "materialized"
        or receipt.get("bundle_id") != catalog["bundle_id"]
        or receipt.get("bundle_version") != catalog["bundle_version"]
        or receipt.get("archive_sha256") != archive_sha256
        or receipt.get("files") != manifest.get("files")
    ):
        raise SimulationRuntimeError(
            "Materialized simulation bundle receipt does not match the catalog.",
            code="simulation_bundle_release_invalid",
            operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
        )
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, Mapping):
        raise SimulationRuntimeError(
            "Materialized simulation bundle manifest is invalid.",
            code="simulation_bundle_release_invalid",
            operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
        )
    if stat.S_IMODE(release_dir.stat().st_mode) & 0o222:
        raise SimulationRuntimeError(
            "Materialized simulation bundle release is writable.",
            code="simulation_bundle_release_invalid",
            operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
        )
    observed: set[str] = set()
    for path in release_dir.rglob("*"):
        if path.is_symlink():
            raise SimulationRuntimeError(
                "Materialized simulation bundle unexpectedly contains a symbolic link.",
                code="simulation_bundle_release_invalid",
                operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
            )
        if path.is_dir():
            if stat.S_IMODE(path.stat().st_mode) & 0o222:
                raise SimulationRuntimeError(
                    "Materialized simulation bundle directory became writable.",
                    code="simulation_bundle_release_invalid",
                    operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
                )
            continue
        if path.is_file():
            if stat.S_IMODE(path.stat().st_mode) & 0o222:
                raise SimulationRuntimeError(
                    "Materialized simulation bundle file became writable.",
                    code="simulation_bundle_release_invalid",
                    operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
                )
            relative = path.relative_to(release_dir).as_posix()
            if relative not in {"manifest.json", "bundle-receipt.json"}:
                observed.add(relative)
    if observed != set(manifest_files):
        raise SimulationRuntimeError(
            "Materialized simulation bundle file set changed after verification.",
            code="simulation_bundle_release_invalid",
            operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
        )
    for relative, metadata in manifest_files.items():
        path = release_dir.joinpath(*PurePosixPath(relative).parts)
        if (
            not path.is_file()
            or not isinstance(metadata, Mapping)
            or sha256_file(path) != metadata.get("sha256")
            or path.stat().st_size != metadata.get("size")
            or oct(stat.S_IMODE(path.stat().st_mode))
            != _read_only_mode(metadata.get("mode"))
        ):
            raise SimulationRuntimeError(
                f"Materialized simulation bundle file changed: {relative}.",
                code="simulation_bundle_release_invalid",
                operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
            )
    return manifest


def _read_only_mode(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0o[0-7]{3}", value):
        return "invalid"
    return oct(int(value, 8) & ~0o222)


def _freeze_materialized_release(release_dir: Path) -> None:
    """Remove write bits after verification so runtime imports cannot mutate a bundle."""

    entries = sorted(release_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in entries:
        if path.is_symlink():
            raise SimulationRuntimeError(
                "Verified simulation release unexpectedly contains a symbolic link.",
                code="simulation_bundle_release_invalid",
                operator_action="重新获取经过发布门禁的 companion artifact。",
            )
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    release_dir.chmod(stat.S_IMODE(release_dir.stat().st_mode) & ~0o222)


def _make_workspace_source_writable(source_dir: Path) -> None:
    """Restore owner writes only on the Catkin-private copy of immutable sources."""

    for path in (source_dir, *source_dir.rglob("*")):
        if path.is_symlink():
            raise SimulationRuntimeError(
                "Simulation workspace source copy unexpectedly contains a symbolic link.",
                code="simulation_workspace_source_changed",
                operator_action="保留日志并移走冲突的 workspace release 后重试。",
            )
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        elif path.is_file():
            path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR)


def _workspace_receipt_is_valid(
    receipt_path: Path,
    *,
    fingerprint: str,
    bundle: SimulationBundleRelease,
    install_setup: Path,
) -> bool:
    if receipt_path.is_symlink() or not receipt_path.is_file():
        return False
    try:
        receipt = _read_json_file(receipt_path, label="simulation workspace receipt")
    except SimulationRuntimeError:
        return False
    if (
        receipt.get("schema_version") != WORKSPACE_RECEIPT_SCHEMA_VERSION
        or receipt.get("status") != "installed"
        or receipt.get("builder_version") != WORKSPACE_BUILDER_VERSION
        or receipt.get("fingerprint") != fingerprint
        or receipt.get("bundle_sha256") != bundle.archive_sha256
        or receipt.get("bundle_release_dir") != str(bundle.release_dir)
        or install_setup.is_symlink()
        or not install_setup.is_file()
    ):
        return False
    return sha256_file(install_setup) == receipt.get("install_setup_sha256")


def _load_ros_environment(
    ros_setup: Path,
    runner: DeploymentCommandRunner,
) -> dict[str, str]:
    environment = dict(os.environ)
    for key in ("BASH_ENV", "ENV", "CDPATH", "PROMPT_COMMAND"):
        environment.pop(key, None)
    script = 'set -eo pipefail\nsource "$1"\nexec env -0'
    result = runner.run(
        ("/bin/bash", "-c", script, "fireclaw-simulation-setup", str(ros_setup)),
        timeout_seconds=15.0,
        max_output_bytes=2 * 1024 * 1024,
        env=environment,
    )
    if not result.ok:
        raise SimulationRuntimeError(
            "ROS environment setup failed before the simulation workspace build.",
            code="simulation_ros_environment_failed",
            operator_action="检查 ROS Noetic 安装与 setup.bash 后重新运行同一 setup 命令。",
        )
    parsed: dict[str, str] = {}
    for record in result.output.split("\x00"):
        key, separator, value = record.partition("=")
        if separator and key:
            parsed[key] = value
    if parsed.get("ROS_DISTRO") != "noetic":
        raise SimulationRuntimeError(
            "The simulation bundle requires ROS_DISTRO=noetic.",
            code="simulation_ros_distro_mismatch",
            operator_action="使用 ROS Noetic 环境后重新运行 setup。",
        )
    return parsed


def _build_source_checkout_bundle(
    source_root: Path,
    *,
    runtime_root: Path,
    catalog: Mapping[str, Any],
) -> Path:
    try:
        resolved_source = source_root.resolve(strict=True)
        from tools.release.simulation_bundle import build_simulation_bundle
    except (OSError, ImportError) as exc:
        raise SimulationRuntimeError(
            "The source-checkout simulation bundle builder is unavailable.",
            code="simulation_bundle_builder_unavailable",
            operator_action="使用发布版本附带的 companion simulation bundle。",
        ) from exc
    output_dir = runtime_root / "downloads"
    _ensure_private_tree(runtime_root, output_dir)
    try:
        result = build_simulation_bundle(resolved_source, output_dir, catalog)
    except Exception as exc:
        raise SimulationRuntimeError(
            f"Could not build the source-checkout simulation bundle: {exc}",
            code="simulation_bundle_build_failed",
            operator_action="检查源码 checkout 的仿真资源与 release catalog 后重试。",
        ) from exc
    expected = str(catalog["artifact"]["sha256"])
    if result.sha256 != expected:
        raise SimulationRuntimeError(
            "Source-checkout simulation bundle does not match the packaged catalog SHA-256.",
            code="simulation_bundle_catalog_stale",
            operator_action="更新并审查 release catalog，或使用匹配当前 wheel 的 companion artifact。",
        )
    return result.archive_path


def _validate_fireclaw_compatibility(catalog: Mapping[str, Any]) -> None:
    compatibility = str(catalog["compatible_fireclaw_versions"])
    if not compatibility.startswith(">="):
        raise SimulationRuntimeError(
            "Simulation bundle compatibility expression is unsupported.",
            code="simulation_bundle_compatibility_invalid",
            operator_action="使用当前 FireClaw 发布附带的 catalog。",
        )
    minimum = compatibility[2:]
    current = __version__.split("-", 1)[0].split("+", 1)[0]
    if not _SEMVER_RE.fullmatch(minimum) or not _SEMVER_RE.fullmatch(current):
        raise SimulationRuntimeError(
            "Simulation bundle compatibility version is invalid.",
            code="simulation_bundle_compatibility_invalid",
            operator_action="使用当前 FireClaw 发布附带的 companion artifact。",
        )
    if tuple(map(int, current.split("."))) < tuple(map(int, minimum.split("."))):
        raise SimulationRuntimeError(
            f"Simulation bundle requires FireClaw {compatibility}; running {__version__}.",
            code="simulation_bundle_incompatible",
            operator_action="升级 FireClaw，或使用与当前 wheel 匹配的旧版 companion artifact。",
        )


def _safe_archive_path(value: str) -> bool:
    if (
        not value
        or value != value.strip()
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or "//" in value
    ):
        return False
    parsed = PurePosixPath(value)
    return parsed.as_posix() == value and all(
        part not in {"", ".", ".."} for part in parsed.parts
    )


def _path_is_included(value: str, includes: list[str]) -> bool:
    return any(
        value == included or (included.endswith("/") and value.startswith(included))
        for included in includes
    )


def _path_is_forbidden(
    value: str,
    forbidden_segments: set[str],
    forbidden_suffixes: set[str],
) -> bool:
    return bool(set(value.split("/")).intersection(forbidden_segments)) or any(
        value.endswith(suffix) for suffix in forbidden_suffixes
    )


def _read_tar_member(tar: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    source = tar.extractfile(member)
    if source is None:
        raise SimulationRuntimeError(
            f"Could not read archive member {member.name}.",
            code="simulation_bundle_archive_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        )
    data = source.read(limit + 1)
    if len(data) != member.size or len(data) > limit:
        raise SimulationRuntimeError(
            f"Archive member size mismatch for {member.name}.",
            code="simulation_bundle_archive_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        )
    return data


def _load_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SimulationRuntimeError(
            f"{label} is not valid canonical JSON.",
            code="simulation_bundle_manifest_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        ) from exc
    if not isinstance(parsed, dict):
        raise SimulationRuntimeError(
            f"{label} must be a JSON object.",
            code="simulation_bundle_manifest_invalid",
            operator_action="重新获取经过发布门禁的 companion artifact。",
        )
    return parsed


def _read_json_file(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SimulationRuntimeError(
            f"{label} is missing or unsafe: {path}.",
            code="simulation_bundle_release_invalid",
            operator_action="移走损坏的内容寻址 release 后重新运行 setup。",
        )
    try:
        return _load_json_object(path.read_bytes(), label=label)
    except OSError as exc:
        raise SimulationRuntimeError(
            f"Could not read {label}.",
            code="simulation_bundle_release_invalid",
            operator_action="检查 runtime root 权限后重试。",
        ) from exc


def _regular_non_symlink_file(path: Path, *, code: str, label: str) -> Path:
    if path.is_symlink():
        raise SimulationRuntimeError(
            f"{label} must not be a symbolic link: {path}.",
            code=code,
            operator_action="使用受信任的普通文件路径后重试。",
        )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SimulationRuntimeError(
            f"{label} does not exist: {path}.",
            code=code,
            operator_action="安装或恢复所需文件后重试。",
        ) from exc
    if not resolved.is_file():
        raise SimulationRuntimeError(
            f"{label} must be a regular file: {path}.",
            code=code,
            operator_action="使用受信任的普通文件路径后重试。",
        )
    return resolved


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise SimulationRuntimeError(
            f"Runtime directory must not be a symbolic link: {path}.",
            code="simulation_runtime_path_unsafe",
            operator_action="移除 runtime root 中不受信任的符号链接后重试。",
        )
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise SimulationRuntimeError(
            f"Runtime path is not a directory: {path}.",
            code="simulation_runtime_path_unsafe",
            operator_action="移动冲突路径后重试。",
        )


def _ensure_private_tree(root: Path, target: Path) -> None:
    canonical_root = root.resolve(strict=False)
    try:
        relative = target.resolve(strict=False).relative_to(canonical_root)
    except ValueError as exc:
        raise SimulationRuntimeError(
            "Simulation runtime path escapes FIRECLAW_HOME.",
            code="simulation_runtime_path_unsafe",
            operator_action="使用 FIRECLAW_HOME 内的默认路径。",
        ) from exc
    current = canonical_root
    _ensure_private_directory(current)
    for part in relative.parts:
        current = current / part
        _ensure_private_directory(current)


def _write_bytes_exclusive(path: Path, content: bytes, *, mode: int) -> None:
    with path.open("xb") as handle:
        path.chmod(mode)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _activate_current_release(owner_root: Path, release_dir: Path) -> None:
    current = owner_root / "current"
    if current.exists() and not current.is_symlink():
        raise SimulationRuntimeError(
            f"Simulation current path is not a symbolic link: {current}.",
            code="simulation_runtime_path_unsafe",
            operator_action="移动冲突路径后重新运行 setup。",
        )
    temporary = owner_root / f".current.{uuid4().hex}.tmp"
    relative = Path("releases") / release_dir.name
    temporary.symlink_to(relative, target_is_directory=True)
    temporary.replace(current)


__all__ = [
    "BUNDLE_RECEIPT_SCHEMA_VERSION",
    "SIMULATION_BUNDLE_ENV",
    "SIMULATION_BUNDLE_ID",
    "SimulationBundleRelease",
    "SimulationRuntimeError",
    "SimulationWorkspaceRelease",
    "WORKSPACE_RECEIPT_SCHEMA_VERSION",
    "build_simulation_workspace",
    "materialize_simulation_bundle",
    "prepare_simulation_runtime",
    "resolve_simulation_bundle",
]
