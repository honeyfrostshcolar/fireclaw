"""Bounded proof-bundle writer used by the Gazebo acceptance lane."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Mapping
from uuid import uuid4


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ArtifactBundle:
    def __init__(self, run_dir: str | Path, *, run_id: str) -> None:
        if not _RUN_ID_RE.fullmatch(run_id):
            raise ValueError("acceptance run_id contains unsafe characters")
        target = Path(run_dir).resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.run_dir = target

    @classmethod
    def from_environment(cls, repo_root: Path) -> "ArtifactBundle":
        configured_run_dir = os.getenv("FIRECLAW_GAZEBO_ACCEPTANCE_RUN_DIR")
        configured_run_id = os.getenv("FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID")
        run_id = configured_run_id or (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + f"-{uuid4().hex[:8]}"
        )
        if configured_run_dir:
            run_dir = Path(configured_run_dir)
        else:
            run_dir = repo_root / "results" / "gazebo-acceptance" / run_id
        return cls(run_dir, run_id=run_id)

    def path(self, name: str) -> Path:
        if not _ARTIFACT_NAME_RE.fullmatch(name):
            raise ValueError("artifact name contains unsafe characters")
        return self.run_dir / name

    def write_json(self, name: str, value: Any) -> Path:
        target = self.path(name)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def write_jsonl(self, name: str, values: Iterable[Mapping[str, Any]]) -> Path:
        target = self.path(name)
        temporary = target.with_name(f".{target.name}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for value in values:
                handle.write(
                    json.dumps(
                        dict(value),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        temporary.replace(target)
        return target

    def manifest_files(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.run_dir))
            for path in self.run_dir.rglob("*")
            if path.is_file()
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_snapshot(repo_root: Path) -> dict[str, Any]:
    return {
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "branch": _git(repo_root, "branch", "--show-current"),
        "dirty": bool(_git(repo_root, "status", "--porcelain")),
    }


def asset_hashes(paths: Mapping[str, Path]) -> dict[str, dict[str, str]]:
    return {
        name: {"path": str(path), "sha256": sha256_file(path)}
        for name, path in sorted(paths.items())
    }


def _git(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


__all__ = [
    "ArtifactBundle",
    "asset_hashes",
    "repository_snapshot",
    "sha256_file",
]
