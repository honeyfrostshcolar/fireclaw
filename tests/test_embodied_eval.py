"""Tests for embodied rescue scenario evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.devtools.embodied_eval import run_embodied_eval


def test_run_embodied_eval_produces_summary_and_metrics(tmp_path: Path):
    """Eval harness runs scenarios and produces summary.json with metrics."""
    fixture_path = tmp_path / "scenarios.json"
    fixture_path.write_text(
        json.dumps([
            {
                "scenario_id": "rescue-floor-2",
                "command": "去二楼救人",
                "expected_floor": 2,
                "expected_capability": "search_for_victims",
                "min_memory_records": 1,
                "requires_terminal_status": True,
            },
            {
                "scenario_id": "inspect-floor-1-smoke",
                "command": "检查一楼烟雾",
                "expected_floor": 1,
                "expected_capability": "monitor_environment",
                "min_memory_records": 1,
                "requires_terminal_status": True,
            },
        ]),
        encoding="utf-8",
    )

    output_dir = tmp_path / "results"
    result = run_embodied_eval(
        scenarios_path=fixture_path,
        output_dir=output_dir,
        adapter="simulator",
    )

    assert result["status"] == "pass"
    assert result["scenario_count"] == 2
    assert result["metrics"]["plan_success_rate"] == 1.0
    assert result["metrics"]["dispatch_success_rate"] == 1.0
    assert result["metrics"]["terminal_event_rate"] == 1.0
    assert result["metrics"]["memory_record_rate"] == 1.0
    assert result["metrics"]["average_latency_ms"] >= 0.0

    assert (output_dir / "summary.json").exists()
    assert (output_dir / "scenarios.jsonl").exists()

    # Verify summary.json is valid JSON with expected fields
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert "scenario_count" in summary
    assert "metrics" in summary


def test_run_embodied_eval_returns_exit_code(tmp_path: Path):
    """CLI entry returns 0 for pass, 2 for warn."""
    fixture_path = tmp_path / "scenarios.json"
    fixture_path.write_text(
        json.dumps([
            {
                "scenario_id": "rescue-floor-2",
                "command": "去二楼救人",
                "expected_floor": 2,
                "expected_capability": "search_for_victims",
                "min_memory_records": 1,
                "requires_terminal_status": True,
            },
        ]),
        encoding="utf-8",
    )

    from fireclaw_core.devtools.embodied_eval import main as embodied_eval_main
    exit_code = embodied_eval_main([
        "--scenarios", str(fixture_path),
        "--output-dir", str(tmp_path / "results"),
        "--adapter", "simulator",
    ])
    assert exit_code == 0


def test_run_embodied_eval_writes_doctor_report_for_bundle(tmp_path: Path):
    """Eval harness emits doctor-report.json for proof bundle acceptance chain."""
    fixture_path = tmp_path / "scenarios.json"
    fixture_path.write_text(
        json.dumps([
            {
                "scenario_id": "rescue-floor-2",
                "command": "去二楼救人",
                "expected_floor": 2,
                "expected_capability": "search_for_victims",
                "min_memory_records": 1,
                "requires_terminal_status": True,
            }
        ]),
        encoding="utf-8",
    )

    output_dir = tmp_path / "results"
    run_embodied_eval(scenarios_path=fixture_path, output_dir=output_dir, adapter="simulator")

    doctor = json.loads((output_dir / "doctor-report.json").read_text(encoding="utf-8"))
    assert doctor["status"] in {"ok", "warn", "fail"}
    assert "findings" in doctor


def test_run_embodied_eval_rejects_missing_fixture(tmp_path: Path):
    """Returns exit code 1 for missing fixture."""
    from fireclaw_core.devtools.embodied_eval import main as embodied_eval_main
    exit_code = embodied_eval_main([
        "--scenarios", str(tmp_path / "nonexistent.json"),
        "--output-dir", str(tmp_path / "results"),
        "--adapter", "simulator",
    ])
    assert exit_code == 1


def test_embodied_eval_records_structured_task_metadata(tmp_path: Path):
    """Eval harness records structured_task metadata from trace subtasks."""
    from fireclaw_core.devtools.embodied_eval import run_embodied_eval

    output_dir = tmp_path / "eval"
    result = run_embodied_eval(
        scenarios_path=Path("tests/fixtures/embodied_eval/rescue_scenarios.json"),
        output_dir=output_dir,
        adapter="simulator",
    )

    assert result["status"] == "pass"
    scenario_lines = (output_dir / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
    assert scenario_lines
    first = json.loads(scenario_lines[0])
    assert "structured_task" in first
    assert first["structured_task"]["required_skills"]
