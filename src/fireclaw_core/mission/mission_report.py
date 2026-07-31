"""Final Mission report generation.

The report is deliberately downstream of execution.  It receives the
authoritative Mission trace after every Robot task has reached a terminal
state; it cannot change a task status or authorize another action.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


def generate_mission_final_report(
    *,
    mission_agent: Any,
    mission_id: str,
    command: str,
    status: str,
    trace: dict[str, Any],
    corrections: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Generate an advisory operator report from the completed Mission trace.

    A planner may expose ``generate_final_report`` to use its configured LLM.
    If it does not, or the LLM call fails, a deterministic report is returned.
    The fallback is intentional: reporting must not turn an already completed
    physical execution into an error merely because the model is unavailable.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    robot_results = [
        dict(item)
        for item in trace.get("subtasks", [])
        if isinstance(item, dict)
    ]
    planner = getattr(mission_agent, "planner", None)
    generator = getattr(planner, "generate_final_report", None)
    llm_error: str | None = None
    if callable(generator):
        try:
            value = generator(
                mission_id=mission_id,
                command=command,
                mission_status=status,
                trace=dict(trace),
                corrections=[dict(item) for item in corrections],
            )
            normalized = _normalize_model_report(value)
            normalized.update(
                {
                    "mission_id": mission_id,
                    "status": status,
                    "generated_at": generated_at,
                    "generated_by": "llm",
                    "robot_results": robot_results,
                    "corrections": [dict(item) for item in corrections],
                }
            )
            return normalized
        except Exception as exc:  # pragma: no cover - provider-specific
            llm_error = f"{type(exc).__name__}: {exc}"

    completed = sum(
        1 for item in robot_results if item.get("status") in {"completed", "succeeded"}
    )
    failed = sum(
        1
        for item in robot_results
        if item.get("status") in {"blocked", "escalated", "failed", "timed_out", "lost"}
    )
    summary = (
        f"Mission {mission_id} ended with status {status}. "
        f"Robot subtasks: {completed} completed, {failed} requiring attention, "
        f"{len(robot_results)} total."
    )
    report: dict[str, Any] = {
        "mission_id": mission_id,
        "status": status,
        "generated_at": generated_at,
        "generated_by": "deterministic_fallback",
        "summary": summary,
        "command": command,
        "robot_results": robot_results,
        "corrections": [dict(item) for item in corrections],
    }
    if llm_error is not None:
        report["llm_error"] = llm_error
    return report


def _normalize_model_report(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        report = dict(value)
        if not isinstance(report.get("summary"), str):
            report["summary"] = json.dumps(report, ensure_ascii=False, sort_keys=True)
        return report
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"summary": text}
        if isinstance(parsed, dict):
            return _normalize_model_report(parsed)
        return {"summary": text}
    raise ValueError("Mission final report must be a non-empty object or string.")
