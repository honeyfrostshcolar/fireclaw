from __future__ import annotations

import json
from pathlib import Path

from fireclaw_core.ros.ros1_proof_bundle import create_ros1_proof_bundle


def test_ros1_proof_bundle_writes_redacted_summary(tmp_path: Path):
    output_dir = tmp_path / "bundle"
    result = create_ros1_proof_bundle(
        output_dir=output_dir,
        robot_id="fireclaw-01",
        environment="sim",
        doctor_report={"status": "pass", "checks": []},
        smoke_artifacts=[{"run_id": "run-1", "passed": True, "error_summary": None}],
        notes="no secrets here",
    )

    assert result["status"] == "created"
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "README.md").exists()
    assert (output_dir / "doctor-report.json").exists()
    assert (output_dir / "ros1-smoke-artifacts.json").exists()

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["robot_id"] == "fireclaw-01"
    assert summary["environment"] == "sim"


def test_ros1_proof_bundle_redacts_secrets_in_notes(tmp_path: Path):
    output_dir = tmp_path / "bundle"
    create_ros1_proof_bundle(
        output_dir=output_dir,
        robot_id="fireclaw-01",
        environment="sim",
        doctor_report={"status": "pass", "checks": []},
        smoke_artifacts=[],
        notes="api_key=sk-abcdef1234567890 deployed",
    )

    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert "sk-***" in summary["notes"]
    assert "sk-abcdef1234567890" not in summary["notes"]


def test_ros1_proof_bundle_cli(tmp_path: Path):
    """Test CLI entry point."""
    import subprocess

    output_dir = tmp_path / "cli-bundle"
    doctor_path = tmp_path / "doctor.json"
    smoke_path = tmp_path / "smoke.jsonl"

    doctor_path.write_text(json.dumps({"status": "pass", "checks": []}), encoding="utf-8")
    smoke_path.write_text(json.dumps({"run_id": "run-1", "passed": True}) + "\n", encoding="utf-8")

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.ros.ros1_proof_bundle",
            "--output-dir", str(output_dir),
            "--robot-id", "fireclaw-01",
            "--environment", "sim",
            "--doctor-report", str(doctor_path),
            "--smoke-artifacts", str(smoke_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0
    body = json.loads(completed.stdout)
    assert body["status"] == "created"
