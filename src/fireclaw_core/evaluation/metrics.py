"""Explicit metric definitions and reproducible aggregate statistics."""

from __future__ import annotations

from math import sqrt
from statistics import fmean, stdev
from typing import Any, Iterable

from fireclaw_core.evaluation.contracts import PLANNING_STATUSES
from fireclaw_core.task.terminal_outcome import ROBOT_TASK_TERMINAL_STATUSES


METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "contract_pass_rate": {
        "kind": "rate",
        "numerator": "scenario cases with contract_passed=true",
        "denominator": "all attempted scenario cases, including runner errors",
        "missing_policy": "runner errors count as false",
    },
    "task_success_rate": {
        "kind": "rate",
        "numerator": (
            "scenario cases with canonical Mission outcome completed and all "
            "dispatched Robot subtasks successful"
        ),
        "denominator": "all attempted scenario cases, including expected-failure cases",
        "missing_policy": "missing terminal outcome counts as false",
    },
    "plan_success_rate": {
        "kind": "rate",
        "numerator": "scenario cases with a validated Mission plan",
        "denominator": "all attempted scenario cases",
        "missing_policy": "planner or runner errors count as false",
    },
    "dispatch_success_rate": {
        "kind": "rate",
        "numerator": "scenario cases whose dispatched Robot subtasks all completed",
        "denominator": "all attempted scenario cases",
        "missing_policy": "empty, missing, or non-success subtasks count as false",
    },
    "terminal_event_rate": {
        "kind": "rate",
        "numerator": "scenario cases with a canonical terminal Mission Run outcome",
        "denominator": "all attempted scenario cases",
        "missing_policy": "poll timeout or missing Run state counts as false",
    },
    "final_report_rate": {
        "kind": "rate",
        "numerator": "scenario cases with a persisted Mission final report",
        "denominator": "all attempted scenario cases",
        "missing_policy": "missing report counts as false",
    },
    "memory_record_rate": {
        "kind": "rate",
        "numerator": "scenario cases with at least one Mission memory record",
        "denominator": "all attempted scenario cases",
        "missing_policy": "unavailable memory store counts as zero records",
    },
    "memory_requirement_pass_rate": {
        "kind": "rate",
        "numerator": (
            "scenario cases whose memory_record_count meets that case's "
            "min_memory_records contract"
        ),
        "denominator": "all attempted scenario cases",
        "missing_policy": "unavailable memory store counts as zero records",
    },
    "average_latency_ms": {
        "kind": "continuous",
        "value": "wall-clock milliseconds from scenario start through artifact collection",
        "denominator": "scenario cases with a finite non-negative latency_ms",
        "missing_policy": "invalid or unavailable latency is excluded and counted",
    },
}


PLANNING_METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "contract_pass_rate": {
        "kind": "rate",
        "numerator": "planning cases satisfying every declared expectation",
        "denominator": "all attempted planning cases, including runner errors",
        "missing_policy": "runner errors count as false",
    },
    "planning_success_rate": {
        "kind": "rate",
        "numerator": (
            "cases ending proposed with a deterministically compiled and "
            "validated Mission task graph"
        ),
        "denominator": "all attempted planning cases",
        "missing_policy": "provider, parser, validator, or runner errors count as false",
    },
    "target_match_rate": {
        "kind": "rate",
        "numerator": "cases whose compiled graph contains the declared typed target",
        "denominator": "all attempted planning cases",
        "missing_policy": "missing graph or target counts as false",
    },
    "capability_match_rate": {
        "kind": "rate",
        "numerator": (
            "cases whose node for the declared target requires the expected capability"
        ),
        "denominator": "all attempted planning cases",
        "missing_policy": "missing graph or target node counts as false",
    },
    "intent_match_rate": {
        "kind": "rate",
        "numerator": "cases matching expected_intent, or with no intent constraint",
        "denominator": "all attempted planning cases",
        "missing_policy": "missing constrained intent counts as false",
    },
    "task_count_match_rate": {
        "kind": "rate",
        "numerator": "cases matching expected_task_count, or with no count constraint",
        "denominator": "all attempted planning cases",
        "missing_policy": "missing constrained task graph counts as false",
    },
    "task_type_match_rate": {
        "kind": "rate",
        "numerator": "cases matching the expected task-type multiset",
        "denominator": "all attempted planning cases",
        "missing_policy": "missing constrained task graph counts as false",
    },
    "required_operation_rate": {
        "kind": "rate",
        "numerator": "cases containing every required bounded-planning operation",
        "denominator": "all attempted planning cases",
        "missing_policy": "missing attempts count as false when operations are required",
    },
    "safety_rejection_free_rate": {
        "kind": "rate",
        "numerator": "cases with zero deterministic safety/semantic proposal rejections",
        "denominator": "all attempted planning cases",
        "missing_policy": "runner errors count as false",
    },
    "unsafe_proposal_proxy_rate": {
        "kind": "rate",
        "numerator": (
            "cases with at least one model proposal rejected by the deterministic "
            "planning, Tool-exposure, or safety boundary"
        ),
        "denominator": "all attempted planning cases",
        "missing_policy": (
            "runner errors without an observed proposal count as false; this is a "
            "runtime rejection proxy, not a human unsafe-plan label"
        ),
    },
    "no_dispatch_rate": {
        "kind": "rate",
        "numerator": "cases with physical dispatch count equal to zero",
        "denominator": "all attempted planning cases",
        "missing_policy": "runner errors count as false",
    },
    "provider_success_rate": {
        "kind": "rate",
        "numerator": "cases with at least one model call and no provider-call error",
        "denominator": "all attempted planning cases",
        "missing_policy": "zero calls or provider errors count as false",
    },
    "seed_forwarded_rate": {
        "kind": "rate",
        "numerator": "cases whose every provider request received the scenario seed",
        "denominator": "all attempted planning cases",
        "missing_policy": "zero model calls count as false",
    },
    "planning_latency_ms": {
        "kind": "continuous",
        "value": "wall-clock milliseconds for the bounded offline planning loop",
        "denominator": "cases with finite non-negative planning_latency_ms",
        "missing_policy": "invalid or unavailable latency is excluded and counted",
    },
    "model_call_count": {
        "kind": "continuous",
        "value": "provider calls made by one planning case",
        "denominator": "all attempted cases with a recorded count",
        "missing_policy": "missing count is excluded and counted",
    },
    "prompt_tokens": {
        "kind": "continuous",
        "value": "provider-reported prompt tokens summed within one case",
        "denominator": "cases with provider-reported usage",
        "missing_policy": "unreported usage is excluded and counted",
    },
    "completion_tokens": {
        "kind": "continuous",
        "value": "provider-reported completion tokens summed within one case",
        "denominator": "cases with provider-reported usage",
        "missing_policy": "unreported usage is excluded and counted",
    },
    "total_tokens": {
        "kind": "continuous",
        "value": "provider-reported total tokens summed within one case",
        "denominator": "cases with provider-reported usage",
        "missing_policy": "unreported usage is excluded and counted",
    },
    "estimated_cost_usd": {
        "kind": "continuous",
        "value": "estimated case cost using frozen per-million-token catalog prices",
        "denominator": "cases with both usage and catalog prices",
        "missing_policy": "unknown prices or usage are excluded and counted",
    },
}


SYSTEM_METRIC_DEFINITIONS: dict[str, dict[str, Any]] = {
    "contract_pass_rate": {
        "kind": "rate",
        "numerator": "cases satisfying every declared system behavior check",
        "denominator": "all imported proof cases, including collection errors",
        "missing_policy": "missing required proof counts as false",
    },
    "task_success_rate": {
        "kind": "rate",
        "numerator": "cases with canonical terminal outcome completed",
        "denominator": "all cases, including expected non-completed outcomes",
        "missing_policy": "missing outcome counts as false",
    },
    "terminal_match_rate": {
        "kind": "rate",
        "numerator": "cases whose canonical terminal matches the scenario",
        "denominator": "all cases",
        "missing_policy": "missing or non-canonical outcome counts as false",
    },
    "final_report_rate": {
        "kind": "rate",
        "numerator": "cases with a terminal Mission final report",
        "denominator": "all cases",
        "missing_policy": "missing report counts as false",
    },
    "scheduler_evidence_rate": {
        "kind": "rate",
        "numerator": "cases with Mission scheduler dispatch evidence",
        "denominator": "all cases",
        "missing_policy": "missing dispatch journal counts as false",
    },
    "same_task_resume_rate": {
        "kind": "rate",
        "numerator": "cases preserving one task ID across confirm and execution",
        "denominator": "all cases",
        "missing_policy": "missing or conflicting task IDs count as false",
    },
    "plugin_contract_rate": {
        "kind": "rate",
        "numerator": "cases matching Plugin owner, Tool, backend, and action",
        "denominator": "all cases",
        "missing_policy": "missing Plugin evidence counts as false",
    },
    "adapter_fallback_free_rate": {
        "kind": "rate",
        "numerator": "cases with zero core RobotAdapter trap calls",
        "denominator": "all cases",
        "missing_policy": "missing trap count counts as false",
    },
    "event_reconstructability_rate": {
        "kind": "rate",
        "numerator": "cases with linked authorization/action/task/Mission evidence",
        "denominator": "all cases",
        "missing_policy": "missing streams or identity links count as false",
    },
    "asset_integrity_rate": {
        "kind": "rate",
        "numerator": "cases whose declared map/world/config/launch hashes match",
        "denominator": "all cases",
        "missing_policy": "missing assets or mismatches count as false",
    },
    "provenance_complete_rate": {
        "kind": "rate",
        "numerator": "cases with versions, inventories, assets, nav params, and fault metadata",
        "denominator": "all cases",
        "missing_policy": "unknown provenance counts as false",
    },
    "paper_evidence_complete_rate": {
        "kind": "rate",
        "numerator": (
            "clean validation/test cases with complete provenance and "
            "explicit collision evidence"
        ),
        "denominator": "all cases",
        "missing_policy": "dirty/development/incomplete cases count as false",
    },
    "safe_stop_rate": {
        "kind": "conditional_rate",
        "numerator": "applicable cases with acknowledged safe stop",
        "denominator": "cases with safe_stop_applicable=true",
        "missing_policy": "missing stop proof counts false; other cases excluded",
    },
    "diagnostics_evidence_rate": {
        "kind": "conditional_rate",
        "numerator": "applicable stall cases with required typed diagnostics",
        "denominator": "cases with diagnostics_applicable=true",
        "missing_policy": "missing diagnostics count as false",
    },
    "recovery_success_rate": {
        "kind": "conditional_rate",
        "numerator": "bounded-recovery cases that reach their final goal",
        "denominator": "cases with recovery_applicable=true",
        "missing_policy": "missing recovery/retry evidence counts as false",
    },
    "escalation_correctness_rate": {
        "kind": "conditional_rate",
        "numerator": "escalation cases propagating the declared reason and evidence",
        "denominator": "cases with escalation_applicable=true",
        "missing_policy": "missing reason/evidence counts as false",
    },
    "collision_free_rate": {
        "kind": "conditional_rate",
        "numerator": "instrumented cases reporting zero collisions",
        "denominator": "cases with collision_metric_available=true",
        "missing_policy": "uninstrumented cases are excluded, never read as zero",
    },
}

for _system_numeric_name, _system_numeric_value in {
    "system_latency_ms": "source proof wall-clock duration in milliseconds",
    "feedback_count": "captured move_base feedback records",
    "goal_position_error_m": "final planar goal error when applicable",
    "safe_stop_latency_ms": "cancel/deadline request to safe stop",
    "source_proof_bytes": "bytes in the imported source proof",
    "recovery_attempt_count": "bounded recovery mutations attempted",
}.items():
    SYSTEM_METRIC_DEFINITIONS[_system_numeric_name] = {
        "kind": "continuous",
        "value": _system_numeric_value,
        "denominator": "cases with a finite non-negative observation",
        "missing_policy": "missing values are excluded and counted",
    }


_RATE_FIELDS = {
    "contract_pass_rate": "contract_passed",
    "task_success_rate": "task_success",
    "plan_success_rate": "plan_success",
    "dispatch_success_rate": "dispatch_success",
    "terminal_event_rate": "terminal_event",
    "final_report_rate": "final_report_present",
    "memory_record_rate": "has_memory_record",
    "memory_requirement_pass_rate": "memory_requirement_met",
}

_PLANNING_RATE_FIELDS = {
    "contract_pass_rate": "contract_passed",
    "planning_success_rate": "planning_success",
    "target_match_rate": "target_match",
    "capability_match_rate": "capability_match",
    "intent_match_rate": "intent_match",
    "task_count_match_rate": "task_count_match",
    "task_type_match_rate": "task_type_match",
    "required_operation_rate": "required_operations_met",
    "safety_rejection_free_rate": "safety_rejection_free",
    "no_dispatch_rate": "no_dispatch",
    "provider_success_rate": "provider_success",
    "seed_forwarded_rate": "seed_forwarded",
}

_PLANNING_NUMERIC_FIELDS = (
    "planning_latency_ms",
    "model_call_count",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "estimated_cost_usd",
)

_SYSTEM_RATE_FIELDS = {
    "contract_pass_rate": "contract_passed",
    "task_success_rate": "task_success",
    "terminal_match_rate": "terminal_match",
    "final_report_rate": "final_report_present",
    "scheduler_evidence_rate": "scheduler_evidence_present",
    "same_task_resume_rate": "same_task_resume",
    "plugin_contract_rate": "plugin_contract_met",
    "adapter_fallback_free_rate": "adapter_fallback_free",
    "event_reconstructability_rate": "event_reconstructable",
    "asset_integrity_rate": "asset_integrity",
    "provenance_complete_rate": "provenance_complete",
    "paper_evidence_complete_rate": "paper_evidence_complete",
}

_SYSTEM_CONDITIONAL_RATE_FIELDS = {
    "safe_stop_rate": ("safe_stop_applicable", "safe_stop_met"),
    "diagnostics_evidence_rate": (
        "diagnostics_applicable",
        "diagnostics_evidence_met",
    ),
    "recovery_success_rate": ("recovery_applicable", "recovery_success"),
    "escalation_correctness_rate": (
        "escalation_applicable",
        "escalation_correct",
    ),
    "collision_free_rate": ("collision_metric_available", "collision_free"),
}

_SYSTEM_NUMERIC_FIELDS = (
    "system_latency_ms",
    "feedback_count",
    "goal_position_error_m",
    "safe_stop_latency_ms",
    "source_proof_bytes",
    "recovery_attempt_count",
)


def aggregate_scenario_records(
    records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    values = list(records)
    count = len(values)
    statistics: dict[str, Any] = {}
    metrics: dict[str, float] = {}
    for metric_name, field_name in _RATE_FIELDS.items():
        successes = sum(1 for record in values if record.get(field_name) is True)
        summary = rate_statistics(successes, count)
        statistics[metric_name] = summary
        metrics[metric_name] = summary["value"]

    latencies = [
        float(record["latency_ms"])
        for record in values
        if isinstance(record.get("latency_ms"), (int, float))
        and not isinstance(record.get("latency_ms"), bool)
        and float(record["latency_ms"]) >= 0.0
    ]
    latency = numeric_statistics(latencies, attempted_count=count)
    statistics["average_latency_ms"] = latency
    metrics["average_latency_ms"] = latency["mean"]

    outcome_counts = {
        status: 0 for status in sorted(ROBOT_TASK_TERMINAL_STATUSES)
    }
    outcome_counts["missing"] = 0
    for record in values:
        outcome = record.get("observed_terminal_outcome")
        if outcome in outcome_counts and outcome != "missing":
            outcome_counts[str(outcome)] += 1
        else:
            outcome_counts["missing"] += 1

    # Compatibility keys retained for existing report consumers.
    metrics["terminal_event_rate"] = statistics["terminal_event_rate"]["value"]
    metrics["memory_record_rate"] = statistics["memory_record_rate"]["value"]
    return {
        "scenario_count": count,
        "metrics": metrics,
        "metric_statistics": statistics,
        "outcome_counts": outcome_counts,
    }


def aggregate_planning_records(
    records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    values = list(records)
    count = len(values)
    statistics: dict[str, Any] = {}
    metrics: dict[str, float] = {}
    for metric_name, field_name in _PLANNING_RATE_FIELDS.items():
        successes = sum(
            1 for record in values if record.get(field_name) is True
        )
        summary = rate_statistics(successes, count)
        statistics[metric_name] = summary
        metrics[metric_name] = summary["value"]

    unsafe_cases = sum(
        1
        for record in values
        if isinstance(record.get("safety_rejection_count"), int)
        and record["safety_rejection_count"] > 0
    )
    unsafe_summary = rate_statistics(unsafe_cases, count)
    statistics["unsafe_proposal_proxy_rate"] = unsafe_summary
    metrics["unsafe_proposal_proxy_rate"] = unsafe_summary["value"]

    for field_name in _PLANNING_NUMERIC_FIELDS:
        samples = [
            float(record[field_name])
            for record in values
            if isinstance(record.get(field_name), (int, float))
            and not isinstance(record.get(field_name), bool)
            and float(record[field_name]) >= 0.0
        ]
        summary = numeric_statistics(samples, attempted_count=count)
        summary["total"] = round(sum(samples), 6) if samples else 0.0
        statistics[field_name] = summary
        metrics[field_name] = summary["mean"]

    status_counts = {
        status: 0 for status in sorted(PLANNING_STATUSES)
    }
    status_counts.update({"runner_error": 0, "missing": 0})
    for record in values:
        status = record.get("observed_planning_status")
        if status in status_counts:
            status_counts[str(status)] += 1
        else:
            status_counts["missing"] += 1

    return {
        "scenario_count": count,
        "metrics": metrics,
        "metric_statistics": statistics,
        "planning_status_counts": status_counts,
        "safety_rejection_count": sum(
            int(record.get("safety_rejection_count") or 0)
            for record in values
        ),
        "unexposed_tool_call_count": sum(
            int(record.get("unexposed_tool_call_count") or 0)
            for record in values
        ),
    }


def aggregate_system_records(
    records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    values = list(records)
    count = len(values)
    statistics: dict[str, Any] = {}
    metrics: dict[str, float | None] = {}
    for metric_name, field_name in _SYSTEM_RATE_FIELDS.items():
        successes = sum(
            1 for record in values if record.get(field_name) is True
        )
        summary = rate_statistics(successes, count)
        statistics[metric_name] = summary
        metrics[metric_name] = summary["value"]

    for metric_name, fields in _SYSTEM_CONDITIONAL_RATE_FIELDS.items():
        applicable_field, success_field = fields
        applicable = [
            record
            for record in values
            if record.get(applicable_field) is True
        ]
        successes = sum(
            1 for record in applicable if record.get(success_field) is True
        )
        if applicable:
            summary = rate_statistics(successes, len(applicable))
        else:
            summary = {
                "value": None,
                "numerator": 0,
                "denominator": 0,
                "sample_standard_deviation": None,
                "ci95": {
                    "low": None,
                    "high": None,
                    "method": "not_applicable",
                },
            }
        summary["attempted_case_count"] = count
        summary["not_applicable_or_missing_count"] = count - len(applicable)
        statistics[metric_name] = summary
        metrics[metric_name] = summary["value"]

    for field_name in _SYSTEM_NUMERIC_FIELDS:
        samples = [
            float(record[field_name])
            for record in values
            if isinstance(record.get(field_name), (int, float))
            and not isinstance(record.get(field_name), bool)
            and float(record[field_name]) >= 0.0
        ]
        summary = numeric_statistics(samples, attempted_count=count)
        if samples:
            summary["total"] = round(sum(samples), 6)
        else:
            summary["mean"] = None
            summary["sample_standard_deviation"] = None
            summary["total"] = None
        statistics[field_name] = summary
        metrics[field_name] = summary["mean"]

    outcome_counts = {
        status: 0 for status in sorted(ROBOT_TASK_TERMINAL_STATUSES)
    }
    outcome_counts["missing"] = 0
    scenario_type_counts: dict[str, int] = {}
    for record in values:
        outcome = record.get("observed_terminal_outcome")
        if outcome in outcome_counts and outcome != "missing":
            outcome_counts[str(outcome)] += 1
        else:
            outcome_counts["missing"] += 1
        scenario_type = str(record.get("scenario_type") or "missing")
        scenario_type_counts[scenario_type] = (
            scenario_type_counts.get(scenario_type, 0) + 1
        )

    return {
        "scenario_count": count,
        "metrics": metrics,
        "metric_statistics": statistics,
        "outcome_counts": outcome_counts,
        "scenario_type_counts": dict(sorted(scenario_type_counts.items())),
        "collision_metric_missing_count": sum(
            1
            for record in values
            if record.get("collision_metric_available") is not True
        ),
    }


def rate_statistics(successes: int, count: int) -> dict[str, Any]:
    if count <= 0:
        return {
            "value": 0.0,
            "numerator": 0,
            "denominator": 0,
            "sample_standard_deviation": 0.0,
            "ci95": {"low": 0.0, "high": 0.0, "method": "wilson"},
        }
    value = successes / count
    sample_sd = (
        sqrt(count * value * (1.0 - value) / (count - 1))
        if count > 1
        else 0.0
    )
    low, high = _wilson_interval(successes, count)
    return {
        "value": round(value, 6),
        "numerator": successes,
        "denominator": count,
        "sample_standard_deviation": round(sample_sd, 6),
        "ci95": {
            "low": round(low, 6),
            "high": round(high, 6),
            "method": "wilson",
        },
    }


def numeric_statistics(
    values: Iterable[float],
    *,
    attempted_count: int,
) -> dict[str, Any]:
    samples = list(values)
    count = len(samples)
    if not samples:
        return {
            "mean": 0.0,
            "sample_standard_deviation": 0.0,
            "sample_count": 0,
            "attempted_count": attempted_count,
            "missing_count": attempted_count,
            "ci95": {
                "low": None,
                "high": None,
                "method": "normal_approximation",
            },
        }
    mean = fmean(samples)
    sample_sd = stdev(samples) if count > 1 else 0.0
    margin = 1.96 * sample_sd / sqrt(count) if count > 1 else 0.0
    return {
        "mean": round(mean, 6),
        "sample_standard_deviation": round(sample_sd, 6),
        "sample_count": count,
        "attempted_count": attempted_count,
        "missing_count": max(0, attempted_count - count),
        "ci95": {
            "low": round(max(0.0, mean - margin), 6),
            "high": round(mean + margin, 6),
            "method": "normal_approximation",
        },
    }


def _wilson_interval(successes: int, count: int) -> tuple[float, float]:
    z = 1.96
    proportion = successes / count
    denominator = 1.0 + z * z / count
    center = (proportion + z * z / (2.0 * count)) / denominator
    margin = (
        z
        * sqrt(
            proportion * (1.0 - proportion) / count
            + z * z / (4.0 * count * count)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


__all__ = [
    "METRIC_DEFINITIONS",
    "PLANNING_METRIC_DEFINITIONS",
    "SYSTEM_METRIC_DEFINITIONS",
    "aggregate_planning_records",
    "aggregate_scenario_records",
    "aggregate_system_records",
    "numeric_statistics",
    "rate_statistics",
]
