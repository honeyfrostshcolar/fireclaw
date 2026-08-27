from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import tarfile

import pytest

from fireclaw_core.deployment.command import DeploymentCommandResult
from fireclaw_core.infra.simulation_runtime import (
    SimulationRuntimeError,
    prepare_simulation_runtime,
)


_REPOSITORY = "https://example.invalid/simulation.git"
_REVISION = "0123456789abcdef0123456789abcdef01234567"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_bundle(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    bundle_id = "test-bundle-v1"
    filename = f"fireclaw-sim-{bundle_id}.tar.gz"
    source_prefix = "robots/turtlebot3_burger/ros_ws/src"
    files = {
        f"{source_prefix}/fixture_pkg/CMakeLists.txt": b"cmake_minimum_required(VERSION 3.0)\n",
        f"{source_prefix}/fixture_pkg/LICENSE": b"MIT fixture license\n",
        f"{source_prefix}/fixture_pkg/UPSTREAM.md": (
            f"Repository: {_REPOSITORY}\nRevision: {_REVISION}\n"
        ).encode("utf-8"),
    }
    manifest_files = {
        name: {"sha256": _sha256(data), "size": len(data), "mode": "0o644"}
        for name, data in files.items()
    }
    manifest = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "bundle_version": "1.0.0",
        "total_files": len(files),
        "unpacked_bytes": sum(len(data) for data in files.values()),
        "files": manifest_files,
    }
    archive_path = tmp_path / filename
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=1700000000) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                manifest_data = json.dumps(manifest, sort_keys=True).encode("utf-8")
                _add_member(archive, "manifest.json", manifest_data)
                for name in sorted(files):
                    _add_member(archive, name, files[name])

    catalog: dict[str, object] = {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "bundle_version": "1.0.0",
        "compatible_fireclaw_versions": ">=0.1.0",
        "artifact": {
            "filename": filename,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        },
        "description": "runtime fixture",
        "include_paths": ["robots/"],
        "required_paths": [f"{source_prefix}/fixture_pkg/CMakeLists.txt"],
        "provenance_required_paths": [
            f"{source_prefix}/fixture_pkg/LICENSE",
            f"{source_prefix}/fixture_pkg/UPSTREAM.md",
        ],
        "upstream_sources": [
            {
                "source_id": "fixture",
                "repository": _REPOSITORY,
                "revision": _REVISION,
                "license": "MIT",
                "evidence_paths": [
                    f"{source_prefix}/fixture_pkg/LICENSE",
                    f"{source_prefix}/fixture_pkg/UPSTREAM.md",
                ],
            }
        ],
        "forbidden_path_segments": ["build", ".git", "__pycache__"],
        "forbidden_file_suffixes": [".pyc", ".so"],
        "size_budget_compressed_bytes": 1024 * 1024,
        "size_budget_unpacked_bytes": 2 * 1024 * 1024,
    }
    return archive_path, catalog


def _add_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 1700000000
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    archive.addfile(info, io.BytesIO(data))


class FakeWorkspaceRunner:
    def __init__(self, *, fail_builds: int = 0) -> None:
        self.fail_builds = fail_builds
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout_seconds, max_output_bytes, env=None, cwd=None):
        command = tuple(str(item) for item in argv)
        self.calls.append(command)
        if command[:2] == ("/bin/bash", "-c"):
            return DeploymentCommandResult(
                argv=command,
                exit_code=0,
                output="ROS_DISTRO=noetic\x00PATH=/usr/bin\x00",
                duration_seconds=0.01,
            )
        if self.fail_builds:
            workspace_cmake = Path(cwd) / "src" / "CMakeLists.txt"
            if not workspace_cmake.exists() and not workspace_cmake.is_symlink():
                workspace_cmake.symlink_to("/fixture/catkin/toplevel.cmake")
            self.fail_builds -= 1
            return DeploymentCommandResult(
                argv=command,
                exit_code=2,
                output="fixture build failed",
                duration_seconds=0.01,
            )
        workspace_cmake = Path(cwd) / "src" / "CMakeLists.txt"
        if not workspace_cmake.exists() and not workspace_cmake.is_symlink():
            workspace_cmake.symlink_to("/fixture/catkin/toplevel.cmake")
        install_setup = Path(cwd) / "install/setup.bash"
        install_setup.parent.mkdir(parents=True, exist_ok=True)
        install_setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        return DeploymentCommandResult(
            argv=command,
            exit_code=0,
            output="fixture build complete",
            duration_seconds=0.01,
        )


def _prepare(
    tmp_path: Path,
    *,
    runner: FakeWorkspaceRunner,
    archive_path: Path,
    catalog: dict[str, object],
):
    ros_setup = tmp_path / "ros-noetic-setup.bash"
    ros_setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")
    return prepare_simulation_runtime(
        runtime_root=tmp_path / "runtime",
        bundle_path=archive_path,
        bundle_id="test-bundle-v1",
        catalog=catalog,
        workspace_runner=runner,
        catkin_executable="/fixture/catkin_make",
        ros_setup=ros_setup,
    )


def test_prepare_materializes_content_addressed_bundle_and_reuses_workspace(
    tmp_path: Path,
) -> None:
    archive, catalog = _write_bundle(tmp_path)
    first = _prepare(
        tmp_path,
        runner=FakeWorkspaceRunner(),
        archive_path=archive,
        catalog=catalog,
    )
    second_runner = FakeWorkspaceRunner()
    second = _prepare(
        tmp_path,
        runner=second_runner,
        archive_path=archive,
        catalog=catalog,
    )

    assert first["status"] == "ready"
    assert first["bundle_sha256"] in first["bundle_root"]
    assert first["workspace_fingerprint"] in first["workspace_setup"]
    assert "/current/" not in first["bundle_root"]
    assert "/current/" not in first["workspace_setup"]
    assert second["bundle"]["reused"] is True
    assert second["workspace"]["reused"] is True
    assert second["workspace_setup"] == first["workspace_setup"]
    assert second_runner.calls == []
    immutable_source = (
        Path(first["bundle_root"])
        / "robots"
        / "turtlebot3_burger"
        / "ros_ws"
        / "src"
        / "CMakeLists.txt"
    )
    workspace_source = Path(first["workspace"]["release_dir"]) / "src" / "CMakeLists.txt"
    assert not immutable_source.exists()
    assert not immutable_source.is_symlink()
    assert workspace_source.is_symlink()
    for path in (Path(first["bundle_root"]), *Path(first["bundle_root"]).rglob("*")):
        assert stat.S_IMODE(path.stat().st_mode) & 0o222 == 0


def test_failed_workspace_build_is_resumed_with_same_fingerprint(tmp_path: Path) -> None:
    archive, catalog = _write_bundle(tmp_path)
    with pytest.raises(SimulationRuntimeError) as failure:
        _prepare(
            tmp_path,
            runner=FakeWorkspaceRunner(fail_builds=1),
            archive_path=archive,
            catalog=catalog,
        )
    assert failure.value.code == "simulation_workspace_build_failed"

    resumed = _prepare(
        tmp_path,
        runner=FakeWorkspaceRunner(),
        archive_path=archive,
        catalog=catalog,
    )
    assert resumed["workspace"]["resumed"] is True
    receipt = json.loads(Path(resumed["workspace_receipt"]).read_text(encoding="utf-8"))
    assert receipt["resumed"] is True
    assert receipt["fingerprint"] == resumed["workspace_fingerprint"]


def test_bundle_digest_and_materialized_tampering_fail_closed(tmp_path: Path) -> None:
    archive, catalog = _write_bundle(tmp_path)
    original = archive.read_bytes()
    archive.write_bytes(original + b"tampered")
    with pytest.raises(SimulationRuntimeError) as digest_failure:
        _prepare(
            tmp_path,
            runner=FakeWorkspaceRunner(),
            archive_path=archive,
            catalog=catalog,
        )
    assert digest_failure.value.code == "simulation_bundle_digest_mismatch"

    archive.write_bytes(original)
    prepared = _prepare(
        tmp_path,
        runner=FakeWorkspaceRunner(),
        archive_path=archive,
        catalog=catalog,
    )
    payload = next(
        path
        for path in Path(prepared["bundle_root"]).rglob("*")
        if path.is_file() and path.name == "CMakeLists.txt"
    )
    payload.chmod(stat.S_IMODE(payload.stat().st_mode) | stat.S_IWUSR)
    payload.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(SimulationRuntimeError) as release_failure:
        _prepare(
            tmp_path,
            runner=FakeWorkspaceRunner(),
            archive_path=archive,
            catalog=catalog,
        )
    assert release_failure.value.code == "simulation_bundle_release_invalid"
