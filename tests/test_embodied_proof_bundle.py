"""Tests for embodied experiment proof bundle."""
from __future__ import annotations

from pathlib import Path

from fireclaw_core.devtools.embodied_proof_bundle import create_embodied_proof_bundle


def test_embodied_proof_bundle_writes_redacted_summary(tmp_path: Path):
    """Bundle creates redacted summary.json and all artifact files."""
    output_dir = tmp_path / "bundle"
    result = create_embodied_proof_bundle(
        output_dir=output_dir,
        run_id="local-sim-1",
        mission_trace={"mission_id": "m1", "status": "succeeded"},
        mission_events={"events": [{"type": "mission.succeeded"}]},
        task_flow={"mission_id": "m1"},
        session_lineage={"session_id": "m1"},
        memory_eval={"hit_rate": 1.0},
        doctor_report={"status": "pass"},
        notes="api_key=sk-abcdef1234567890",
    )

    assert result["status"] == "created"
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "mission-trace.json").exists()
    assert (output_dir / "mission-events.json").exists()
    assert (output_dir / "task-flow.json").exists()
    assert (output_dir / "session-lineage.json").exists()
    assert (output_dir / "memory-eval.json").exists()
    assert (output_dir / "doctor-report.json").exists()
    assert (output_dir / "README.md").exists()

    # Verify secrets are redacted
    summary_text = (output_dir / "summary.json").read_text(encoding="utf-8")
    assert "sk-abcdef" not in summary_text


def test_embodied_proof_bundle_accepts_none_fields(tmp_path: Path):
    """Bundle handles None optional fields gracefully."""
    output_dir = tmp_path / "bundle"
    result = create_embodied_proof_bundle(
        output_dir=output_dir,
        run_id="test-run",
        mission_trace={"mission_id": "m1"},
    )

    assert result["status"] == "created"
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "mission-trace.json").exists()
    # Optional fields not written when None
    assert not (output_dir / "memory-eval.json").exists()


def test_embodied_proof_bundle_redacts_nested_secrets(tmp_path: Path):
    """Bundle redacts secret patterns in nested string values."""
    output_dir = tmp_path / "bundle"
    result = create_embodied_proof_bundle(
        output_dir=output_dir,
        run_id="test-run",
        mission_trace={"mission_id": "m1", "config": {"raw": "api_key=secret12345678"}},
        notes="password=hunter2",
    )

    assert result["status"] == "created"
    trace_text = (output_dir / "mission-trace.json").read_text(encoding="utf-8")
    assert "secret12345678" not in trace_text
