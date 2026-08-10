from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireclaw_core.evaluation.contracts import (
    SCENARIO_SUITE_SCHEMA_VERSION,
    load_evaluation_suite,
)


PLANNING_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "embodied_eval"
    / "planning_scenarios.json"
)


def _write_suite(path: Path, scenarios: list[dict]) -> Path:
    path.write_text(
        json.dumps({
            "schema_version": SCENARIO_SUITE_SCHEMA_VERSION,
            "suite_id": "target-contracts",
            "suite_version": "1.0.0",
            "lane": "deterministic_integration",
            "split": "test",
            "defaults": {
                "expected_capability": "inspect_target",
                "expected_terminal_outcomes": ["completed"],
                "seeds": [3, 5],
                "repetitions": 2,
            },
            "scenarios": scenarios,
        }),
        encoding="utf-8",
    )
    return path


def test_suite_expands_seeds_repetitions_and_target_kinds(tmp_path: Path) -> None:
    path = _write_suite(tmp_path / "suite.json", [
        {
            "scenario_id": "point-a",
            "scenario_version": "1.0.0",
            "command": "导航到点位",
            "target_type": "point",
            "target": {
                "frame_id": "map",
                "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
            },
        },
        {
            "scenario_id": "area-a",
            "scenario_version": "1.0.0",
            "command": "搜索西侧走廊",
            "target_type": "area",
            "target": {"frame_id": "map", "area_id": "west-corridor"},
        },
        {
            "scenario_id": "entity-a",
            "scenario_version": "1.0.0",
            "command": "接近目标实体",
            "target_type": "entity",
            "target": {"frame_id": "map", "entity_id": "victim-7"},
        },
    ])

    suite = load_evaluation_suite(path)

    assert suite.schema_version == SCENARIO_SUITE_SCHEMA_VERSION
    assert len(suite.scenarios) == 12
    assert {scenario.target_type for scenario in suite.scenarios} == {
        "point",
        "area",
        "entity",
    }
    assert {scenario.seed for scenario in suite.scenarios} == {3, 5}
    assert {scenario.repeat_index for scenario in suite.scenarios} == {0, 1}
    assert len({scenario.case_id for scenario in suite.scenarios}) == 12
    assert len(suite.source_sha256) == 64


def test_suite_normalizes_legacy_fixture_and_terminal_alias(tmp_path: Path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps([{
        "scenario_id": "point-a",
        "command": "导航到点位",
        "expected_target": {
            "frame_id": "map",
            "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        },
        "expected_capability": "navigate_to_point",
        "expected_terminal_outcomes": ["succeeded"],
    }]), encoding="utf-8")

    suite = load_evaluation_suite(path)

    assert suite.legacy_source_format is True
    assert suite.scenarios[0].target_type == "point"
    assert suite.scenarios[0].expected_terminal_outcomes == ("completed",)


@pytest.mark.parametrize(
    ("target_type", "target", "message"),
    [
        ("point", {"frame_id": "map"}, "requires pose"),
        ("area", {"frame_id": "map", "area_id": ""}, "area_id"),
        ("entity", {"frame_id": "map"}, "requires entity_id"),
    ],
)
def test_suite_rejects_invalid_typed_targets(
    tmp_path: Path,
    target_type: str,
    target: dict,
    message: str,
) -> None:
    path = _write_suite(tmp_path / "invalid.json", [{
        "scenario_id": "invalid-target",
        "command": "test",
        "target_type": target_type,
        "target": target,
    }])

    with pytest.raises(ValueError, match=message):
        load_evaluation_suite(path)


def test_suite_rejects_unknown_fields_instead_of_ignoring_typos(
    tmp_path: Path,
) -> None:
    path = _write_suite(tmp_path / "invalid.json", [{
        "scenario_id": "point-a",
        "command": "test",
        "target_type": "point",
        "target": {
            "frame_id": "map",
            "pose": {"x": 1.0, "y": 2.0},
        },
        "expected_terminal_outcome": "completed",
    }])

    with pytest.raises(ValueError, match="expected_terminal_outcome"):
        load_evaluation_suite(path)


def test_planning_suite_freezes_context_and_disables_dispatch_contract() -> None:
    suite = load_evaluation_suite(PLANNING_FIXTURE)

    assert suite.lane == "llm_planning"
    assert len(suite.scenarios) == 3
    assert {scenario.target_type for scenario in suite.scenarios} == {
        "point",
        "area",
        "entity",
    }
    for scenario in suite.scenarios:
        assert scenario.requires_terminal_status is False
        assert scenario.expected_dispatch_success is None
        assert scenario.expected_planning_statuses == ("proposed",)
        assert scenario.max_safety_rejections == 0
        assert scenario.max_model_calls == 4
        assert scenario.planning_context["captured_at"] == (
            "2026-08-10T08:00:00+00:00"
        )


def test_planning_suite_rejects_dispatch_expectation(tmp_path: Path) -> None:
    payload = json.loads(PLANNING_FIXTURE.read_text(encoding="utf-8"))
    payload["defaults"]["expected_dispatch_success"] = True
    path = tmp_path / "planning.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="expected_dispatch_success=null"):
        load_evaluation_suite(path)
