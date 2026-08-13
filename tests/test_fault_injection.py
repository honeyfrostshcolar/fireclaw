from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

import pytest

from fireclaw_core.evaluation.artifacts import sha256_file
from fireclaw_core.infra.fault_injection import (
    SCENARIOS,
    run_fault_injection_suite,
    verify_fault_injection_run,
)


class _PassingRunner:
    def __init__(self) -> None:
        self.calls: list[
            tuple[tuple[str, ...], Path, dict[str, str] | None]
        ] = []

    def __call__(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str] | None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            (
                tuple(command),
                cwd,
                dict(environment) if environment is not None else None,
            )
        )
        return subprocess.CompletedProcess(
            list(command),
            0,
            stdout="1 passed\n",
            stderr="",
        )


def test_fault_suite_writes_verifiable_non_overwriting_bundle(
    tmp_path: Path,
) -> None:
    runner = _PassingRunner()
    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path / "results",
        scenario_ids=["database_lock", "sensor_failure"],
        command_runner=runner,
    )

    assert result["status"] == "passed"
    assert result["scenario_count"] == 2
    run_dir = Path(str(result["run_directory"]))
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "artifact-manifest.json").is_file()
    assert verify_fault_injection_run(run_dir)["status"] == "valid"
    assert len(runner.calls) == 2
    assert all(call[2] is None for call in runner.calls)


def test_fault_suite_marks_ros_boundary_prepared_without_live_process(
    tmp_path: Path,
) -> None:
    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path,
        scenario_ids=["ros_master_restart"],
        command_runner=_PassingRunner(),
    )

    assert result["status"] == "prepared"
    assert result["results"][0]["live_ros_exercised"] is False
    assert result["remaining_limits"]


def test_fault_suite_deduplicates_selected_scenarios(tmp_path: Path) -> None:
    runner = _PassingRunner()

    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path,
        scenario_ids=["database_lock", "database_lock"],
        command_runner=runner,
    )

    assert result["scenario_count"] == 1
    assert len(runner.calls) == 1


def test_fault_suite_rejects_explicit_empty_selection(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="At least one fault scenario"):
        run_fault_injection_suite(
            repository_root=Path.cwd(),
            artifact_dir=tmp_path,
            scenario_ids=[],
            command_runner=_PassingRunner(),
        )


def test_fault_suite_live_ros_adds_private_process_node_and_opt_in(
    tmp_path: Path,
) -> None:
    runner = _PassingRunner()
    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path,
        scenario_ids=["ros_master_restart"],
        live_ros=True,
        command_runner=runner,
    )

    assert result["status"] == "passed"
    assert result["results"][0]["live_ros_exercised"] is True
    command, _, environment = runner.calls[0]
    assert SCENARIOS["ros_master_restart"].live_ros_test_nodes[0] in command
    assert environment == {"FIRECLAW_RUN_LIVE_ROS_FAULT_TEST": "1"}


def test_fault_suite_manifest_detects_tampering(tmp_path: Path) -> None:
    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path,
        scenario_ids=["duplicate_command"],
        command_runner=_PassingRunner(),
    )
    run_dir = Path(str(result["run_directory"]))
    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "passed-after-tamper"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    verification = verify_fault_injection_run(run_dir)

    assert verification["status"] == "invalid"
    assert "artifact digest mismatch: report.json" in verification["errors"]


def test_fault_suite_verifier_rejects_rehashed_inconsistent_report(
    tmp_path: Path,
) -> None:
    result = run_fault_injection_suite(
        repository_root=Path.cwd(),
        artifact_dir=tmp_path,
        scenario_ids=["duplicate_command"],
        command_runner=_PassingRunner(),
    )
    run_dir = Path(str(result["run_directory"]))
    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["status"] = "prepared"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = run_dir / "artifact-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_entry = next(
        item for item in manifest["files"] if item["path"] == "report.json"
    )
    report_entry["bytes"] = report_path.stat().st_size
    report_entry["sha256"] = sha256_file(report_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verification = verify_fault_injection_run(run_dir)

    assert verification["status"] == "invalid"
    assert (
        "report status is inconsistent with scenario results"
        in verification["errors"]
    )
