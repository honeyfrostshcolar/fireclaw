from __future__ import annotations

from pathlib import Path
import subprocess

from fireclaw_core.evaluation.provenance import repository_snapshot


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_repository_snapshot_hashes_tracked_and_untracked_dirty_files(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(
        tmp_path,
        "-c",
        "user.name=FireClaw Test",
        "-c",
        "user.email=fireclaw-test@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    tracked.write_text("v2\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("new\n", encoding="utf-8")

    snapshot = repository_snapshot(tmp_path)

    assert snapshot["dirty"] is True
    dirty = {item["path"]: item for item in snapshot["dirty_files"]}
    assert set(dirty) == {"tracked.txt", "untracked.txt"}
    assert all(len(item["sha256"]) == 64 for item in dirty.values())
    assert len(snapshot["tracked_diff_sha256"]) == 64
    assert len(snapshot["status_sha256"]) == 64
