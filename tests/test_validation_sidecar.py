"""Tests for the post-ready validation sidecar."""
from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.safety.validation_sidecar import ValidationSidecar


def test_validation_sidecar_runs_once_and_writes_report(tmp_path: Path):
    """Sidecar calls run(), writes redacted JSON, returns result."""
    calls: list[Path] = []

    def run(output_dir: Path) -> dict:
        calls.append(output_dir)
        return {"status": "ok", "checks": [{"name": "runtime_paths", "status": "ok"}]}

    sidecar = ValidationSidecar(output_dir=tmp_path, run=run)

    result = sidecar.run_once()

    assert result["status"] == "ok"
    assert calls == [tmp_path]
    assert (tmp_path / "validation-report.json").exists()

    # Verify the written file is valid JSON with expected content
    written = json.loads((tmp_path / "validation-report.json").read_text(encoding="utf-8"))
    assert written["status"] == "ok"
    assert written["checks"][0]["name"] == "runtime_paths"


def test_validation_sidecar_redacts_secrets(tmp_path: Path):
    """Secrets in the report are redacted before writing."""

    def run(output_dir: Path) -> dict:
        return {"status": "ok", "token": "sk-abcdefghij1234", "nested": {"api_key": "api_key=secret12345"}}

    sidecar = ValidationSidecar(output_dir=tmp_path, run=run)
    result = sidecar.run_once()

    # In-memory result is already redacted
    assert "sk-***" in result["token"]
    assert "api_key=***" in result["nested"]["api_key"]

    # On-disk report is also redacted
    written = json.loads((tmp_path / "validation-report.json").read_text(encoding="utf-8"))
    assert "sk-***" in written["token"]
    assert "api_key=***" in written["nested"]["api_key"]


def test_validation_sidecar_creates_output_dir(tmp_path: Path):
    """Sidecar creates the output directory if it does not exist."""
    nested = tmp_path / "a" / "b" / "c"
    calls: list[Path] = []

    def run(output_dir: Path) -> dict:
        calls.append(output_dir)
        return {"status": "ok"}

    sidecar = ValidationSidecar(output_dir=nested, run=run)
    sidecar.run_once()

    assert nested.exists()
    assert (nested / "validation-report.json").exists()
