from __future__ import annotations

from fireclaw_core.evaluation.metrics import (
    aggregate_planning_records,
    aggregate_scenario_records,
    aggregate_system_records,
    numeric_statistics,
)
from fireclaw_core.task.terminal_outcome import ROBOT_TASK_TERMINAL_STATUSES


def test_aggregate_preserves_every_canonical_terminal_outcome() -> None:
    records = [
        {
            "observed_terminal_outcome": status,
            "contract_passed": True,
            "task_success": status == "completed",
            "plan_success": True,
            "dispatch_success": status == "completed",
            "terminal_event": True,
            "final_report_present": True,
            "has_memory_record": True,
            "memory_requirement_met": True,
            "latency_ms": 10.0,
        }
        for status in sorted(ROBOT_TASK_TERMINAL_STATUSES)
    ]

    aggregate = aggregate_scenario_records(records)

    assert aggregate["outcome_counts"] == {
        **{status: 1 for status in sorted(ROBOT_TASK_TERMINAL_STATUSES)},
        "missing": 0,
    }
    assert aggregate["metrics"]["contract_pass_rate"] == 1.0
    assert aggregate["metrics"]["task_success_rate"] == round(
        1 / len(ROBOT_TASK_TERMINAL_STATUSES),
        6,
    )
    assert (
        aggregate["metric_statistics"]["task_success_rate"]["denominator"]
        == len(ROBOT_TASK_TERMINAL_STATUSES)
    )


def test_aggregate_counts_missing_outcome_and_reports_latency_missingness() -> None:
    aggregate = aggregate_scenario_records([{
        "observed_terminal_outcome": None,
        "contract_passed": False,
        "task_success": False,
        "plan_success": False,
        "dispatch_success": False,
        "terminal_event": False,
        "final_report_present": False,
        "has_memory_record": False,
        "memory_requirement_met": False,
        "latency_ms": None,
    }])

    assert aggregate["outcome_counts"]["missing"] == 1
    latency = aggregate["metric_statistics"]["average_latency_ms"]
    assert latency["sample_count"] == 0
    assert latency["missing_count"] == 1


def test_planning_aggregate_keeps_safety_tokens_and_status_denominators() -> None:
    records = [
        {
            "observed_planning_status": "proposed",
            "contract_passed": True,
            "planning_success": True,
            "first_try_clean": True,
            "tool_protocol_valid_first_try": True,
            "planning_recovery_applicable": False,
            "planning_recovered": False,
            "target_match": True,
            "capability_match": True,
            "intent_match": True,
            "task_count_match": True,
            "task_type_match": True,
            "required_operations_met": True,
            "safety_rejection_free": True,
            "no_dispatch": True,
            "provider_success": True,
            "seed_forwarded": True,
            "safety_rejection_count": 0,
            "unexposed_tool_call_count": 0,
            "planning_latency_ms": 20.0,
            "model_call_count": 1,
            "tool_protocol_violation_count": 0,
            "tool_protocol_repair_count": 0,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "estimated_cost_usd": 0.001,
        },
        {
            "observed_planning_status": "blocked",
            "contract_passed": False,
            "planning_success": False,
            "first_try_clean": False,
            "tool_protocol_valid_first_try": False,
            "planning_recovery_applicable": True,
            "planning_recovered": False,
            "target_match": False,
            "capability_match": False,
            "intent_match": False,
            "task_count_match": False,
            "task_type_match": False,
            "required_operations_met": False,
            "safety_rejection_free": False,
            "no_dispatch": True,
            "provider_success": True,
            "seed_forwarded": True,
            "safety_rejection_count": 1,
            "unexposed_tool_call_count": 1,
            "planning_latency_ms": 30.0,
            "model_call_count": 2,
            "tool_protocol_violation_count": 1,
            "tool_protocol_repair_count": 1,
            "prompt_tokens": 200,
            "completion_tokens": 30,
            "total_tokens": 230,
            "estimated_cost_usd": 0.002,
        },
    ]

    aggregate = aggregate_planning_records(records)

    assert aggregate["scenario_count"] == 2
    assert aggregate["metrics"]["planning_success_rate"] == 0.5
    assert aggregate["metrics"]["first_try_clean_rate"] == 0.5
    assert aggregate["metrics"]["tool_protocol_valid_first_try_rate"] == 0.5
    assert aggregate["metrics"]["planning_recovery_rate"] == 0.0
    assert (
        aggregate["metric_statistics"]["planning_recovery_rate"][
            "denominator"
        ]
        == 1
    )
    assert aggregate["metrics"]["unsafe_proposal_proxy_rate"] == 0.5
    assert aggregate["planning_status_counts"]["proposed"] == 1
    assert aggregate["planning_status_counts"]["blocked"] == 1
    assert aggregate["safety_rejection_count"] == 1
    assert aggregate["unexposed_tool_call_count"] == 1
    assert aggregate["tool_protocol_violation_count"] == 1
    assert aggregate["tool_protocol_repair_count"] == 1
    assert aggregate["metric_statistics"]["total_tokens"]["total"] == 350.0


def test_system_aggregate_does_not_encode_missing_as_zero() -> None:
    aggregate = aggregate_system_records([{
        "observed_terminal_outcome": "completed",
        "scenario_type": "success",
        "safe_stop_applicable": False,
        "diagnostics_applicable": False,
        "recovery_applicable": False,
        "escalation_applicable": False,
        "collision_metric_available": False,
    }])

    assert aggregate["metrics"]["safe_stop_rate"] is None
    assert aggregate["metrics"]["collision_free_rate"] is None
    assert (
        aggregate["metric_statistics"]["collision_free_rate"]["denominator"]
        == 0
    )
    assert aggregate["metrics"]["safe_stop_latency_ms"] is None
    assert (
        aggregate["metric_statistics"]["safe_stop_latency_ms"]["sample_count"]
        == 0
    )


def test_nonnegative_numeric_metric_interval_never_crosses_below_zero() -> None:
    summary = numeric_statistics([0.0, 1.0], attempted_count=2)

    assert summary["ci95"]["low"] == 0.0
