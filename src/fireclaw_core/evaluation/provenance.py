"""Reproducibility snapshots shared by all evaluation lanes."""

from __future__ import annotations

from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Mapping

from fireclaw_core.evaluation.artifacts import canonical_json_sha256, sha256_file


def repository_snapshot(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=False)
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = _git_bytes(root, "diff", "--binary", "HEAD")
    changed_paths = _nul_paths(
        _git_bytes(root, "diff", "--name-only", "-z", "HEAD")
        + _git_bytes(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        )
    )
    dirty_files = []
    for relative in sorted(set(changed_paths)):
        target = root / relative
        dirty_files.append({
            "path": relative,
            "sha256": sha256_file(target) if target.is_file() else None,
            "missing_or_deleted": not target.is_file(),
        })
    return {
        "root": str(root),
        "commit": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "branch", "--show-current"),
        "dirty": bool(status),
        "status_lines": status.splitlines(),
        "status_sha256": sha256(status.encode("utf-8")).hexdigest(),
        "tracked_diff_sha256": sha256(diff).hexdigest(),
        "dirty_files": dirty_files,
    }


def runtime_snapshot() -> dict[str, Any]:
    try:
        fireclaw_version = version("fireclaw")
    except PackageNotFoundError:
        fireclaw_version = "source-tree"
    return {
        "fireclaw_version": fireclaw_version,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }


def ros_gazebo_runtime_snapshot() -> dict[str, Any]:
    """Capture bounded ROS/Gazebo version evidence without requiring ROS."""

    return {
        "ros_distro_environment": os.getenv("ROS_DISTRO"),
        "ros_master_uri_recorded": bool(os.getenv("ROS_MASTER_URI")),
        "gazebo_master_uri_recorded": bool(os.getenv("GAZEBO_MASTER_URI")),
        "commands": {
            "ros_distro": _version_command(("rosversion", "-d")),
            "ros_core": _version_command(("rosversion", "roscpp")),
            "move_base": _version_command(("rosversion", "move_base")),
            "gazebo_ros": _version_command(("rosversion", "gazebo_ros")),
            "gazebo": _version_command(("gazebo", "--version")),
            "gazebo_library": _version_command(
                ("pkg-config", "--modversion", "gazebo")
            ),
        },
    }


def extension_inventory_snapshot(
    agent: Any,
    *,
    repo_root: str | Path,
    deployment_profile: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Capture stable Plugin and Tool inventories from one loaded Agent."""

    from fireclaw_core.agent.tool_runtime import AgentToolRuntime

    root = Path(repo_root).resolve(strict=False)
    report = agent.extension_report
    candidates_by_id: dict[str, Any] = {}
    if report is not None:
        candidates_by_id = {
            candidate.manifest.plugin_id: candidate
            for candidate in report.discovered
        }

    plugins: list[dict[str, Any]] = []
    for record in agent.plugin_host.records():
        candidate = candidates_by_id.get(record.plugin_id)
        source_files: dict[str, dict[str, str]] = {}
        if candidate is not None:
            for label, path in (
                ("manifest", candidate.manifest_path),
                ("entrypoint", candidate.entrypoint_path),
            ):
                source_files[label] = {
                    "path": _portable_path(path, root),
                    "sha256": sha256_file(path),
                }
        stable = {
            "plugin_id": record.plugin_id,
            "name": record.name,
            "version": record.version,
            "description": record.description,
            "source": record.source,
            "api_version": record.api_version,
            "trust_level": record.trust_level,
            "status": record.status,
            "contributions": [
                {"kind": kind, "contribution_id": contribution_id}
                for kind, contribution_id in record.contribution_keys
            ],
            "source_files": source_files,
        }
        stable["digest"] = canonical_json_sha256(stable)
        plugins.append(stable)

    contributions = [
        {
            "kind": contribution.kind,
            "contribution_id": contribution.contribution_id,
            "owner_plugin_id": contribution.owner_plugin_id,
            "metadata": dict(contribution.metadata),
        }
        for contribution in sorted(
            agent.plugin_host.contributions(),
            key=lambda item: item.key,
        )
    ]
    plugin_inventory = {
        "api_versions": sorted(agent.plugin_host.SUPPORTED_API_VERSIONS),
        "plugins": plugins,
        "contributions": contributions,
        "disabled_plugin_ids": (
            list(report.disabled_plugin_ids) if report is not None else []
        ),
        "diagnostics": (
            [item.to_dict() for item in report.diagnostics]
            if report is not None
            else []
        ),
    }
    plugin_inventory["inventory_sha256"] = canonical_json_sha256(
        plugin_inventory
    )

    agent_tools = AgentToolRuntime(
        plugin_host=agent.plugin_host,
        profile=deployment_profile,
    ).manifest(include_blocked=True)
    tool_inventory = {
        "physical_tools": agent.registry.list_metadata(),
        "agent_tools": agent_tools,
    }
    tool_inventory["inventory_sha256"] = canonical_json_sha256(tool_inventory)
    return plugin_inventory, tool_inventory


def file_identity(path: str | Path, *, repo_root: str | Path) -> dict[str, str]:
    source = Path(path).resolve(strict=False)
    return {
        "path": _portable_path(source, Path(repo_root).resolve(strict=False)),
        "sha256": sha256_file(source),
    }


def environment_allowlist_snapshot(
    names: tuple[str, ...],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Capture only explicitly safe, non-secret experiment environment keys."""

    source = os.environ if environment is None else environment
    return {
        name: source[name]
        for name in names
        if name in source
    }


def _portable_path(path: str | Path, repo_root: Path) -> str:
    candidate = Path(path).resolve(strict=False)
    try:
        return candidate.relative_to(repo_root).as_posix()
    except ValueError:
        return str(candidate)


def _git(repo_root: Path, *arguments: str) -> str:
    return _git_bytes(repo_root, *arguments).decode("utf-8").strip()


def _git_bytes(repo_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=True,
        capture_output=True,
        timeout=15,
    )
    return completed.stdout


def _version_command(arguments: tuple[str, ...]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "available": False,
            "command": list(arguments),
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "available": completed.returncode == 0,
        "command": list(arguments),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _nul_paths(value: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in value.split(b"\0")
        if item
    ]


__all__ = [
    "environment_allowlist_snapshot",
    "extension_inventory_snapshot",
    "file_identity",
    "repository_snapshot",
    "ros_gazebo_runtime_snapshot",
    "runtime_snapshot",
]
