"""Scenario-level evaluation harness for embodied rescue missions.

Runs rescue scenarios through the real mission/gateway chain and produces
metrics suitable for paper/demo reporting.

Usage:
    python -m fireclaw_core.embodied_eval \\
      --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \\
      --output-dir results/embodied-eval/local-sim \\
      --adapter simulator
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib import request

from fireclaw_core.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.log_redaction import redact_dict
from fireclaw_core.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.subagent_client import RobotSubagentClient


# ---------------------------------------------------------------------------
# Scenario-aware planner
# ---------------------------------------------------------------------------


class ScenarioPlanner:
    """Deterministic planner that uses scenario metadata to build subtasks."""

    def __init__(self, expected_floor: int, expected_capability: str) -> None:
        self.expected_floor = expected_floor
        self.expected_capability = expected_capability

    def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
        available = context.available_robots
        if not available:
            return MissionPlanningResult(
                status="no_robots",
                message="No online robots available.",
                intent=None,
                plan=None,
            )
        robot = available[0]
        return MissionPlanningResult(
            status="planned",
            message="Plan created.",
            intent="rescue",
            plan=MissionPlan(
                intent="rescue",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command=command,
                        floor=self.expected_floor,
                        capability_required=self.expected_capability,
                        execution_group=0,
                    )
                ],
            ),
        )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _json_request(
    base_url: str,
    method: str,
    path: str,
    payload: dict | None = None,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "X-Operator-Scopes": "admin"},
    )
    with request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Single scenario runner
# ---------------------------------------------------------------------------


def _write_artifact(path: Path, data: Any) -> None:
    """Write a JSON artifact file for proof-bundle consumption."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

_TERMINAL_MISSION_STATUSES = {"succeeded", "completed", "failed"}


def _run_scenario(
    scenario: dict[str, Any],
    *,
    adapter: str,
    tmp_dir: Path,
    output_dir: Path,
    poll_timeout: float = 15.0,
) -> dict[str, Any]:
    """Run a single rescue scenario and return collected metrics."""
    scenario_id = scenario["scenario_id"]
    command = scenario["command"]
    expected_floor = scenario.get("expected_floor", 1)
    expected_capability = scenario.get("expected_capability", "search_for_victims")
    requires_terminal = scenario.get("requires_terminal_status", True)

    start_time = time.monotonic()

    robot_gw = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter=adapter,
            robot_id=f"eval-{scenario_id}",
            memory_path=str(tmp_dir / "robot_memory.jsonl"),
            event_path=str(tmp_dir / "robot_events.jsonl"),
            task_queue_path=str(tmp_dir / "robot_tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    robot_gw.start()
    try:
        robot_base_url = robot_gw.base_url

        registry_path = tmp_dir / "robots.json"
        registry_path.write_text(
            json.dumps({
                "robots": [
                    {
                        "robot_id": f"eval-{scenario_id}",
                        "base_url": robot_base_url,
                        "capabilities": [expected_capability],
                    }
                ]
            }),
            encoding="utf-8",
        )

        paths = MissionRuntimePaths(
            robot_registry=registry_path,
            mission_registry=tmp_dir / "missions.jsonl",
            mission_memory=tmp_dir / "memory.jsonl",
            memory_index=tmp_dir / "memory.sqlite",
            task_registry=tmp_dir / "tasks.jsonl",
            subagent_registry=tmp_dir / "subagents.jsonl",
            session_lineage=tmp_dir / "lineage.jsonl",
            task_flow=tmp_dir / "flows.jsonl",
            approvals=tmp_dir / "approvals.jsonl",
        )

        agent = build_mission_agent_from_paths(
            paths,
            operator_id="eval-op",
            role="operator",
            planner=ScenarioPlanner(expected_floor, expected_capability),
        )

        mission_config = MissionGatewayConfig(port=0)
        mission_gw = MissionGateway(
            mission_config,
            mission_agent=agent,
            registry=agent.registry,
            subagent_client=agent.subagent_client,
            task_registry=agent.task_registry,
            subagent_registry=agent.subagent_registry,
            session_lineage_store=agent._session_lineage_store,
        )
        mission_gw.start()
        try:
            base = mission_gw.base_url

            # Submit mission
            status, body = _json_request(base, "POST", "/missions", {
                "command": command,
                "session_id": f"eval-{scenario_id}",
                "use_scheduler": False,
            })

            plan_success = body.get("status") == "planned"
            mission_id = body.get("mission_id", "")

            # Poll trace until terminal
            trace: dict | None = None
            deadline = time.monotonic() + poll_timeout
            while time.monotonic() < deadline:
                _, trace = _json_request(base, "GET", f"/missions/{mission_id}/trace")
                mission_status = trace.get("status", "unknown")
                if mission_status in _TERMINAL_MISSION_STATUSES:
                    break
                time.sleep(0.2)

            elapsed_ms = (time.monotonic() - start_time) * 1000

            terminal_event = trace is not None and trace.get("status") in _TERMINAL_MISSION_STATUSES

            # Check dispatch success via subtask results (trace uses "subtasks" key)
            _TERMINAL_SUBTASK = {"succeeded", "completed", "cancelled", "failed", "block", "denied", "lost"}
            subtasks_list = trace.get("subtasks", []) if trace else []
            dispatch_success = any(
                isinstance(s, dict) and s.get("status") in _TERMINAL_SUBTASK
                for s in subtasks_list
            ) if isinstance(subtasks_list, list) else False

            # Check memory records
            memory_store = agent.mission_memory
            memory_records = memory_store.list_records(mission_id=mission_id) if memory_store else []
            memory_record_count = len(memory_records)

            # --- Collect proof-bundle-ready artifacts ---
            scenario_prefix = output_dir / scenario_id
            scenario_prefix.mkdir(parents=True, exist_ok=True)

            # mission-trace.json (from the HTTP endpoint, already fetched)
            if trace is not None:
                _write_artifact(scenario_prefix / "mission-trace.json", redact_dict(trace))

            # mission-events.json (from the HTTP endpoint)
            try:
                _, events_body = _json_request(base, "GET", f"/missions/{mission_id}/events")
                _write_artifact(scenario_prefix / "mission-events.json", redact_dict(events_body))
            except Exception:
                pass

            # task-flow.json (from agent store)
            if agent._task_flow_store is not None:
                try:
                    flows = agent._task_flow_store.list_recent(limit=50)
                    mission_flows = [f.to_dict() for f in flows if f.mission_id == mission_id]
                    _write_artifact(scenario_prefix / "task-flow.json", redact_dict({"flows": mission_flows}))
                except Exception:
                    pass

            # session-lineage.json (from agent store)
            if agent._session_lineage_store is not None:
                try:
                    lineage = agent._session_lineage_store.get(mission_id)
                    if lineage is not None:
                        _write_artifact(scenario_prefix / "session-lineage.json", redact_dict(lineage.to_dict()))
                except Exception:
                    pass

            # memory-eval.json (from memory store)
            if memory_records:
                _write_artifact(
                    scenario_prefix / "memory-eval.json",
                    redact_dict({"mission_id": mission_id, "record_count": memory_record_count, "records": [r.to_dict() for r in memory_records]}),
                )

            result: dict[str, Any] = {
                "scenario_id": scenario_id,
                "command": command,
                "plan_success": plan_success,
                "dispatch_success": dispatch_success,
                "terminal_event": terminal_event,
                "memory_record_count": memory_record_count,
                "latency_ms": round(elapsed_ms, 2),
                "mission_status": trace.get("status") if trace else "unknown",
            }

            # Evaluate pass/fail
            passed = plan_success
            if requires_terminal:
                passed = passed and terminal_event

            result["passed"] = passed
            return redact_dict(result)

        finally:
            mission_gw.stop()
    finally:
        robot_gw.stop()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_metrics(scenario_results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(scenario_results)
    if n == 0:
        return {
            "plan_success_rate": 0.0,
            "dispatch_success_rate": 0.0,
            "terminal_event_rate": 0.0,
            "memory_record_rate": 0.0,
            "average_latency_ms": 0.0,
        }

    plan_successes = sum(1 for r in scenario_results if r["plan_success"])
    dispatch_successes = sum(1 for r in scenario_results if r["dispatch_success"])
    terminal_events = sum(1 for r in scenario_results if r["terminal_event"])
    memory_hits = sum(1 for r in scenario_results if r["memory_record_count"] >= 1)
    total_latency = sum(r["latency_ms"] for r in scenario_results)

    return {
        "plan_success_rate": round(plan_successes / n, 4),
        "dispatch_success_rate": round(dispatch_successes / n, 4),
        "terminal_event_rate": round(terminal_events / n, 4),
        "memory_record_rate": round(memory_hits / n, 4),
        "average_latency_ms": round(total_latency / n, 2),
    }


_ARTIFACT_NAMES = [
    "mission-trace.json",
    "mission-events.json",
    "task-flow.json",
    "session-lineage.json",
    "memory-eval.json",
]


def _consolidate_artifacts(output_dir: Path, scenario_results: list[dict[str, Any]]) -> None:
    """Merge per-scenario artifacts into top-level proof-bundle-ready files.

    Each scenario writes artifacts under ``output_dir / {scenario_id}/``.
    This function collects them and writes consolidated files at ``output_dir/``
    so the proof-bundle CLI can consume them directly.
    """
    for artifact_name in _ARTIFACT_NAMES:
        merged: list[dict[str, Any]] = []
        for r in scenario_results:
            sid = r.get("scenario_id", "")
            artifact_path = output_dir / sid / artifact_name
            if artifact_path.is_file():
                try:
                    data = json.loads(artifact_path.read_text(encoding="utf-8"))
                    merged.append(data)
                except Exception:
                    pass
        if merged:
            # Wrap in dict so redact_dict always receives a dict
            payload: dict[str, Any] = {"scenarios": merged} if len(merged) > 1 else merged[0]
            _write_artifact(output_dir / artifact_name, redact_dict(payload))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_embodied_eval(
    *,
    scenarios_path: Path,
    output_dir: Path,
    adapter: str = "simulator",
    poll_timeout: float = 15.0,
) -> dict[str, Any]:
    """Run all scenarios from a JSON fixture and produce summary metrics.

    Returns:
        Summary dict with ``status`` (pass/warn/error), ``scenario_count``,
        ``metrics``, and ``scenarios`` list.
    """
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))
    if not isinstance(scenarios, list) or not scenarios:
        return {"status": "error", "message": "Fixture must be a non-empty JSON array."}

    output_dir.mkdir(parents=True, exist_ok=True)

    scenario_results: list[dict[str, Any]] = []
    for idx, scenario in enumerate(scenarios):
        tmp_dir = output_dir / f"_tmp_{scenario.get('scenario_id', idx)}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = _run_scenario(
                scenario,
                adapter=adapter,
                tmp_dir=tmp_dir,
                output_dir=output_dir,
                poll_timeout=poll_timeout,
            )
            scenario_results.append(result)
        except Exception as exc:
            scenario_results.append(redact_dict({
                "scenario_id": scenario.get("scenario_id", f"scenario-{idx}"),
                "command": scenario.get("command", ""),
                "plan_success": False,
                "dispatch_success": False,
                "terminal_event": False,
                "memory_record_count": 0,
                "latency_ms": 0.0,
                "mission_status": "error",
                "passed": False,
                "error": str(exc),
            }))

    metrics = _aggregate_metrics(scenario_results)

    # Determine overall status
    all_passed = all(r.get("passed", False) for r in scenario_results)
    status = "pass" if all_passed else "warn"

    summary = {
        "status": status,
        "scenario_count": len(scenario_results),
        "metrics": metrics,
    }

    # Write outputs
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(redact_dict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    jsonl_path = output_dir / "scenarios.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in scenario_results:
            f.write(json.dumps(redact_dict(r), ensure_ascii=False) + "\n")

    # Consolidate per-scenario artifacts into top-level proof-bundle-ready files
    _consolidate_artifacts(output_dir, scenario_results)

    return summary


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run embodied rescue scenario evaluation harness.",
    )
    parser.add_argument(
        "--scenarios",
        required=True,
        help="Path to rescue_scenarios.json fixture.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for summary.json and scenarios.jsonl.",
    )
    parser.add_argument(
        "--adapter",
        default="simulator",
        choices=["dry-run", "simulator", "ros1"],
        help="Robot adapter to use (default: simulator).",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=15.0,
        help="Seconds to poll each mission for terminal status (default: 15).",
    )
    args = parser.parse_args(argv)

    scenarios_path = Path(args.scenarios)
    if not scenarios_path.is_file():
        print(json.dumps({"status": "error", "message": f"Fixture not found: {scenarios_path}"}))
        return 1

    output_dir = Path(args.output_dir)
    result = run_embodied_eval(
        scenarios_path=scenarios_path,
        output_dir=output_dir,
        adapter=args.adapter,
        poll_timeout=args.poll_timeout,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "error":
        return 1
    if result["status"] == "warn":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
