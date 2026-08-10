from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.evaluation.artifacts import EvaluationRunBundle


def test_evaluation_bundle_is_write_once_and_content_addressed(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run-a"
    bundle = EvaluationRunBundle(run_dir, run_id="run-a")
    bundle.write_json("summary.json", {"status": "pass"})
    bundle.write_jsonl("raw/scenarios.jsonl", [{"scenario_id": "a"}])

    with pytest.raises(FileExistsError):
        bundle.write_json("summary.json", {"status": "rewritten"})

    manifest = bundle.finalize_artifact_manifest()
    paths = {item["path"] for item in manifest["files"]}
    assert ".evaluation-run.json" in paths
    assert "summary.json" in paths
    assert "raw/scenarios.jsonl" in paths
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    persisted = json.loads(
        (run_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert persisted["artifact_count"] == manifest["artifact_count"]


def test_evaluation_bundle_refuses_nonempty_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "old-result.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not empty"):
        EvaluationRunBundle(run_dir, run_id="run-a")


@pytest.mark.parametrize("name", ["../escape.json", "/tmp/escape.json"])
def test_evaluation_bundle_rejects_path_escape(tmp_path: Path, name: str) -> None:
    bundle = EvaluationRunBundle(tmp_path / "run", run_id="run-a")

    with pytest.raises(ValueError):
        bundle.write_json(name, {})


def test_evaluation_bundle_copies_binary_evidence_without_overwrite(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    source.write_bytes(b"SQLite format 3\x00proof")
    bundle = EvaluationRunBundle(tmp_path / "run", run_id="copy-test")

    copied = bundle.copy_file("raw/state.sqlite3", source)

    assert copied.read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError, match="already exists"):
        bundle.copy_file("raw/state.sqlite3", source)


def test_evaluation_bundle_rejects_symlink_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.log"
    source.write_text("proof", encoding="utf-8")
    link = tmp_path / "source-link.log"
    link.symlink_to(source)
    bundle = EvaluationRunBundle(tmp_path / "run", run_id="symlink-test")

    with pytest.raises(ValueError, match="non-symlink"):
        bundle.copy_file("raw/source.log", link)
