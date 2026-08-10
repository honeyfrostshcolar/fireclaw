"""Deterministic, scheduler-backed Mission/Robot integration evaluation.

This is one of three intentionally separate evaluation lanes. It validates
FireClaw control-plane contracts with deterministic planning and a simulated
Robot Adapter. It does not measure LLM planning quality or ROS/Gazebo system
performance. Every invocation writes a versioned, non-overwriting run bundle
whose raw records can be re-aggregated for paper analysis.

Usage:
    python -m fireclaw_core.devtools.embodied_eval \\
      --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \\
      --output-dir results/embodied-eval/local-sim \\
      --adapter simulator
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from math import isfinite
import time
from pathlib import Path
from typing import Any
from urllib import request

from fireclaw_core.evaluation.artifacts import (
    EvaluationRunBundle,
    canonical_json_sha256,
    make_run_id,
)
from fireclaw_core.evaluation.contracts import (
    EVALUATION_RUN_SCHEMA_VERSION,
    EvaluationScenario,
    load_evaluation_suite,
)
from fireclaw_core.evaluation.metrics import (
    METRIC_DEFINITIONS,
    aggregate_scenario_records,
)
from fireclaw_core.evaluation.provenance import (
    extension_inventory_snapshot,
    repository_snapshot,
    runtime_snapshot,
)
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)
from fireclaw_core.mission.mission_runtime import (
    MissionRuntimePaths,
    build_mission_agent_from_paths,
)
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile
from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_SUCCESS_STATUSES,
    ROBOT_TASK_TERMINAL_STATUSES,
    normalize_robot_task_terminal_status,
)


# ---------------------------------------------------------------------------
# Scenario-aware planner
# ---------------------------------------------------------------------------


class ScenarioPlanner:
    """Deterministic planner for one fully specified evaluation case."""

    def __init__(self, scenario: EvaluationScenario) -> None:
        self.scenario = scenario

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
            intent=self.scenario.task_type,
            plan=MissionPlan(
                intent=self.scenario.task_type,
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=robot.robot_id,
                        command=command,
                        floor=None,
                        capability_required=(
                            self.scenario.expected_capability
                        ),
                        execution_group=0,
                        task_type=self.scenario.task_type,
                        target=self.scenario.target,
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
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=15) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Single scenario runner
# ---------------------------------------------------------------------------


def _write_artifact(path: Path, data: Any) -> None:
    """Write a JSON artifact file for proof-bundle consumption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


_SUCCESS_SUBTASK_STATUSES = {"succeeded", "completed"}


def _run_scenario(
    scenario: EvaluationScenario,
    *,
    adapter: str,
    tmp_dir: Path,
    output_dir: Path,
    repo_root: Path,
    poll_timeout: float = 15.0,
    ros1_config_path: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run one concrete case and return its record plus inventories."""
    scenario_id = scenario.scenario_id
    case_id = scenario.case_id
    command = scenario.command
    expected_target = scenario.target
    expected_capability = scenario.expected_capability
    started_at = datetime.now(timezone.utc).isoformat()
    start_time = time.monotonic()
    terminal_elapsed_ms: float | None = None
    stable_robot_id = f"eval-{canonical_json_sha256(scenario.to_dict())[:16]}"

    robot_gw = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter=adapter,
            robot_id=stable_robot_id,
            ros1_config_path=ros1_config_path,
            memory_path=str(tmp_dir / "robot_memory.jsonl"),
            event_path=str(tmp_dir / "robot_events.jsonl"),
            task_queue_path=str(tmp_dir / "robot_tasks.jsonl"),
            deployment_profile=DeploymentProfile(
                mode="simulation",
                role="robot_agent",
                sandbox=SandboxProfile(),
            ),
        )
    )
    robot_gw.start()
    try:
        robot_base_url = robot_gw.base_url
        inventory_agent = robot_gw._create_agent(session_id=case_id)
        try:
            plugin_inventory, tool_inventory = extension_inventory_snapshot(
                inventory_agent,
                repo_root=repo_root,
                deployment_profile=robot_gw.config.deployment_profile,
            )
        finally:
            inventory_agent.plugin_host.dispose()

        registry_path = tmp_dir / "robots.json"
        registry_path.write_text(
            json.dumps({
                "robots": [
                    {
                        "robot_id": stable_robot_id,
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
            planner=ScenarioPlanner(scenario),
            resume_dispatches=False,
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

            # Submit through the production-default scheduler-backed,
            # background Mission Run path.
            _http_status, body = _json_request(base, "POST", "/missions", {
                "command": command,
                "session_id": f"eval-{case_id}",
                "use_scheduler": True,
                "background": True,
            })
            mission_id = body.get("mission_id", "")
            background_run = (
                body.get("status") == "accepted"
                and isinstance(body.get("run_id"), str)
                and body.get("run_id") == mission_id
            )

            # Poll the Mission Run endpoint because it preserves all canonical
            # outcomes. The compatibility trace may intentionally project
            # several failure states to a legacy `failed` status.
            run_snapshot: dict[str, Any] = {}
            runner_error_code: str | None = None
            deadline = time.monotonic() + poll_timeout
            if mission_id:
                while time.monotonic() < deadline:
                    _, run_snapshot = _json_request(
                        base,
                        "GET",
                        f"/missions/{mission_id}/run",
                    )
                    observed = normalize_robot_task_terminal_status(
                        run_snapshot.get("run_status", run_snapshot.get("status"))
                    )
                    if run_snapshot.get("terminal") is True and observed is not None:
                        terminal_elapsed_ms = (
                            time.monotonic() - start_time
                        ) * 1000.0
                        break
                    time.sleep(0.2)
            if terminal_elapsed_ms is None:
                runner_error_code = "mission_run_poll_timeout"
                if mission_id:
                    try:
                        _json_request(
                            base,
                            "POST",
                            f"/missions/{mission_id}/cancel",
                            {},
                        )
                    except Exception:
                        pass
                terminal_elapsed_ms = (
                    time.monotonic() - start_time
                ) * 1000.0

            trace: dict[str, Any] | None = None
            if mission_id:
                _, trace = _json_request(
                    base,
                    "GET",
                    f"/missions/{mission_id}/trace",
                )
            run_result = (
                run_snapshot.get("result")
                if isinstance(run_snapshot.get("result"), dict)
                else {}
            )
            plan_success = (
                isinstance(run_result.get("plan"), dict)
                and run_result.get("status") not in {
                    "no_planner",
                    "no_robots",
                    "unauthorized",
                    "denied",
                    "error",
                }
            )
            observed_terminal_outcome = normalize_robot_task_terminal_status(
                run_snapshot.get("run_status", run_snapshot.get("status"))
            )
            terminal_event = (
                run_snapshot.get("terminal") is True
                and observed_terminal_outcome is not None
            )

            # A terminal failure is not a successful dispatch.
            subtasks_list = trace.get("subtasks", []) if trace else []
            dispatch_success = _dispatch_succeeded(subtasks_list)

            # Extract structured task metadata from trace
            structured_task = None
            for subtask in subtasks_list:
                if not isinstance(subtask, dict):
                    continue
                # structured_task may be at top level or nested in robot_trace
                if isinstance(subtask.get("structured_task"), dict):
                    structured_task = subtask["structured_task"]
                    break
                robot_trace = subtask.get("robot_trace")
                if isinstance(robot_trace, dict) and isinstance(
                    robot_trace.get("structured_task"),
                    dict,
                ):
                    structured_task = robot_trace["structured_task"]
                    break

            # Check memory records
            memory_store = agent.mission_memory
            memory_records = (
                memory_store.list_records(mission_id=mission_id)
                if memory_store
                else []
            )
            memory_record_count = len(memory_records)
            target_contract_ok = (
                isinstance(structured_task, dict)
                and _mapping_contains(
                    structured_task.get("target"),
                    expected_target,
                )
                and expected_capability
                in structured_task.get("required_skills", [])
            )

            final_report: dict[str, Any] | None = None
            if mission_id:
                try:
                    _, report_body = _json_request(
                        base,
                        "GET",
                        f"/missions/{mission_id}/report",
                    )
                    if report_body.get("status") not in {"pending", "not_found"}:
                        final_report = report_body
                except Exception:
                    embedded = run_snapshot.get("final_report")
                    if isinstance(embedded, dict):
                        final_report = embedded

            # --- Collect complete, per-case proof artifacts ---
            scenario_prefix = output_dir / "cases" / case_id
            scenario_prefix.mkdir(parents=True, exist_ok=True)
            _write_artifact(
                scenario_prefix / "scenario.json",
                scenario.to_dict(),
            )
            _write_artifact(
                scenario_prefix / "mission-run.json",
                redact_dict(run_snapshot),
            )

            if trace is not None:
                _write_artifact(
                    scenario_prefix / "mission-trace.json",
                    redact_dict(trace),
                )
            if final_report is not None:
                _write_artifact(
                    scenario_prefix / "final-report.json",
                    redact_dict(final_report),
                )

            try:
                _, events_body = _json_request(base, "GET", f"/missions/{mission_id}/events")
                _write_artifact(
                    scenario_prefix / "mission-events.json",
                    redact_dict(events_body),
                )
            except Exception:
                pass

            robot_events = [
                event
                for event in robot_gw.events.list_events()
                if event.get("session_id") == mission_id
            ]
            _write_artifact(
                scenario_prefix / "robot-events.json",
                redact_dict({"mission_id": mission_id, "events": robot_events}),
            )
            robot_task_ids = sorted({
                str(event.get("task_id"))
                for event in robot_events
                if isinstance(event.get("task_id"), str)
                and event.get("task_id")
            })
            _write_artifact(
                scenario_prefix / "robot-task-traces.json",
                redact_dict({
                    "mission_id": mission_id,
                    "tasks": [
                        robot_gw.task_trace(task_id)
                        for task_id in robot_task_ids
                    ],
                }),
            )

            # task-flow.json (from agent store)
            if agent._task_flow_store is not None:
                try:
                    flows = agent._task_flow_store.list_recent(limit=50)
                    mission_flows = [f.to_dict() for f in flows if f.mission_id == mission_id]
                    _write_artifact(
                        scenario_prefix / "task-flow.json",
                        redact_dict({"flows": mission_flows}),
                    )
                except Exception:
                    pass

            # session-lineage.json (from agent store)
            if agent._session_lineage_store is not None:
                try:
                    lineage = agent._session_lineage_store.get(mission_id)
                    if lineage is not None:
                        _write_artifact(
                            scenario_prefix / "session-lineage.json",
                            redact_dict(lineage.to_dict()),
                        )
                except Exception:
                    pass

            # memory-eval.json (from memory store)
            if memory_records:
                _write_artifact(
                    scenario_prefix / "memory-eval.json",
                    redact_dict(
                        {
                            "mission_id": mission_id,
                            "record_count": memory_record_count,
                            "records": [
                                record.to_dict()
                                for record in memory_records
                            ],
                        }
                    ),
                )

            final_report_present = final_report is not None
            terminal_outcome_match = (
                observed_terminal_outcome
                in scenario.expected_terminal_outcomes
            )
            task_success = (
                observed_terminal_outcome in ROBOT_TASK_SUCCESS_STATUSES
                and dispatch_success
            )
            memory_requirement_met = (
                memory_record_count >= scenario.min_memory_records
            )
            plan_expectation_met = (
                scenario.expected_plan_success is None
                or plan_success is scenario.expected_plan_success
            )
            dispatch_expectation_met = (
                scenario.expected_dispatch_success is None
                or dispatch_success is scenario.expected_dispatch_success
            )
            target_expectation_met = (
                not scenario.requires_target_contract
                or target_contract_ok
            )
            terminal_expectation_met = (
                not scenario.requires_terminal_status
                or (terminal_event and terminal_outcome_match)
            )
            report_expectation_met = (
                not scenario.requires_terminal_status
                or final_report_present
            )
            contract_passed = all((
                background_run,
                run_snapshot.get("use_scheduler") is True,
                plan_expectation_met,
                dispatch_expectation_met,
                target_expectation_met,
                memory_requirement_met,
                terminal_expectation_met,
                report_expectation_met,
                runner_error_code is None,
            ))
            missing_data: list[str] = []
            if not run_snapshot:
                missing_data.append("mission_run")
            if trace is None:
                missing_data.append("mission_trace")
            if structured_task is None:
                missing_data.append("structured_task")
            if final_report is None:
                missing_data.append("final_report")

            result: dict[str, Any] = {
                "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
                "case_id": case_id,
                "scenario_id": scenario_id,
                "scenario_version": scenario.scenario_version,
                "suite_id": scenario.suite_id,
                "suite_version": scenario.suite_version,
                "lane": scenario.lane,
                "split": scenario.split,
                "seed": scenario.seed,
                "repeat_index": scenario.repeat_index,
                "target_type": scenario.target_type,
                "target": scenario.target,
                "command": command,
                "background_run": background_run,
                "scheduler_backed": (
                    run_snapshot.get("use_scheduler") is True
                ),
                "plan_success": plan_success,
                "dispatch_success": dispatch_success,
                "task_success": task_success,
                "terminal_event": terminal_event,
                "expected_terminal_outcomes": list(
                    scenario.expected_terminal_outcomes
                ),
                "observed_terminal_outcome": observed_terminal_outcome,
                "terminal_outcome_match": terminal_outcome_match,
                "final_report_present": final_report_present,
                "memory_record_count": memory_record_count,
                "has_memory_record": memory_record_count >= 1,
                "memory_requirement_met": memory_requirement_met,
                "target_contract_ok": target_contract_ok,
                "latency_ms": round(terminal_elapsed_ms, 2),
                "mission_status": (
                    observed_terminal_outcome
                    or str(run_snapshot.get("run_status") or "unknown")
                ),
                "trace_status": trace.get("status") if trace else "unknown",
                "mission_id": mission_id,
                "mission_run_id": body.get("run_id"),
                "structured_task": structured_task,
                "runner_error_code": runner_error_code,
                "missing_data": missing_data,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            result["contract_passed"] = contract_passed
            result["passed"] = contract_passed
            redacted = redact_dict(result)
            _write_artifact(
                scenario_prefix / "scenario-record.json",
                redacted,
            )
            return redacted, plugin_inventory, tool_inventory

        finally:
            mission_gw.mission_run_manager.shutdown(wait=True)
            mission_gw.stop()
    finally:
        robot_gw.stop()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _dispatch_succeeded(subtasks: Any) -> bool:
    return (
        isinstance(subtasks, list)
        and bool(subtasks)
        and all(
            isinstance(subtask, dict)
            and subtask.get("status") in _SUCCESS_SUBTASK_STATUSES
            for subtask in subtasks
        )
    )


def _mapping_contains(actual: Any, expected: Any) -> bool:
    """Return whether actual recursively contains the expected contract."""

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _mapping_contains(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    return actual == expected


def _aggregate_metrics(scenario_results: list[dict[str, Any]]) -> dict[str, Any]:
    return aggregate_scenario_records(scenario_results)["metrics"]


_ARTIFACT_NAMES = [
    "mission-run.json",
    "mission-trace.json",
    "mission-events.json",
    "robot-events.json",
    "robot-task-traces.json",
    "final-report.json",
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
            case_id = r.get("case_id", "")
            artifact_path = output_dir / "cases" / case_id / artifact_name
            if artifact_path.is_file():
                try:
                    data = json.loads(artifact_path.read_text(encoding="utf-8"))
                    merged.append(data)
                except Exception:
                    pass
        if merged:
            payload: dict[str, Any] = {"cases": merged}
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
    ros1_config_path: str | None = None,
    run_id: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run a deterministic integration suite and persist a complete bundle.

    Returns:
        Versioned summary with exact denominators and the immutable run ID.
    """
    resolved_run_id = run_id or make_run_id("deterministic-integration")
    bundle = EvaluationRunBundle(output_dir, run_id=resolved_run_id)
    root = (
        Path(repo_root).resolve(strict=False)
        if repo_root is not None
        else Path(__file__).resolve().parents[3]
    )
    started_at = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "lane": "deterministic_integration",
        "status": "running",
        "started_at": started_at,
        "scenario_source": str(Path(scenarios_path).resolve(strict=False)),
        "runner": {
            "name": "fireclaw_core.devtools.embodied_eval",
            "adapter": adapter,
            "poll_timeout_seconds": poll_timeout,
            "scheduler_backed": True,
            "background_mission_run": True,
            "canonical_terminal_source": "GET /missions/{mission_id}/run",
        },
        "selection": {
            "exclusion_rule": "none",
            "retention_rule": (
                "retain every attempted case, including errors and failures"
            ),
        },
    }
    bundle.write_json("run-manifest.json", redact_dict(manifest))
    base_provenance: dict[str, Any] = {
        "repository": repository_snapshot(root),
        "runtime": runtime_snapshot(),
        "models": {
            "mission_planner": {
                "kind": "deterministic_scenario_planner",
                "provider": None,
                "model": None,
                "temperature": None,
                "model_seed": None,
                "prompt_template_sha256": None,
                "model_invoked": False,
            },
            "robot_agent": {
                "kind": "deterministic",
                "provider": None,
                "model": None,
                "model_invoked": False,
            },
        },
        "system_context": {
            "applicable": False,
            "reason": (
                "ROS/Gazebo state belongs to the separate "
                "ros_gazebo_system evaluation lane"
            ),
            "map": None,
            "world": None,
            "navigation_parameters": None,
            "ros_version": None,
            "gazebo_version": None,
        },
    }

    try:
        if (
            isinstance(poll_timeout, bool)
            or not isinstance(poll_timeout, (int, float))
            or not isfinite(float(poll_timeout))
            or poll_timeout <= 0
        ):
            raise ValueError("poll_timeout must be a positive finite number")
        if adapter not in {"dry-run", "simulator"}:
            raise ValueError(
                "deterministic_integration does not accept ROS adapters; "
                "use the ros_gazebo_system evaluation lane"
            )
        if ros1_config_path is not None:
            raise ValueError(
                "ros1_config_path belongs to the ros_gazebo_system "
                "evaluation lane"
            )
        suite = load_evaluation_suite(scenarios_path)
        if suite.lane != "deterministic_integration":
            raise ValueError(
                "embodied_eval only runs lane=deterministic_integration; "
                f"received {suite.lane!r}"
            )
    except Exception as exc:
        error = redact_dict({
            "phase": "configuration",
            "error_type": type(exc).__name__,
            "message": str(exc),
        })
        aggregate = aggregate_scenario_records([])
        completed_at = datetime.now(timezone.utc).isoformat()
        summary = {
            "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "lane": "deterministic_integration",
            "status": "error",
            "scenario_count": 0,
            "metrics": aggregate["metrics"],
            "metric_statistics": aggregate["metric_statistics"],
            "outcome_counts": aggregate["outcome_counts"],
            "started_at": started_at,
            "completed_at": completed_at,
            "error": error,
        }
        bundle.write_json("summary.json", summary)
        bundle.write_json("metric-definitions.json", METRIC_DEFINITIONS)
        bundle.write_jsonl("scenarios.jsonl", [])
        bundle.write_jsonl("errors.jsonl", [error])
        bundle.write_json("provenance.json", redact_dict(base_provenance))
        manifest.update({
            "status": "error",
            "completed_at": completed_at,
            "scenario_count": 0,
            "error": error,
        })
        bundle.replace_json("run-manifest.json", redact_dict(manifest))
        artifact_manifest = bundle.finalize_artifact_manifest()
        return {
            **summary,
            "output_dir": str(bundle.run_dir),
            "artifact_count": artifact_manifest["artifact_count"],
        }

    bundle.write_json("scenario-suite.json", redact_dict(suite.to_dict()))
    scenario_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    plugin_inventory: dict[str, Any] | None = None
    tool_inventory: dict[str, Any] | None = None
    for scenario in suite.scenarios:
        tmp_dir = bundle.run_dir / "runtime" / scenario.case_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            result, current_plugins, current_tools = _run_scenario(
                scenario,
                adapter=adapter,
                tmp_dir=tmp_dir,
                output_dir=bundle.run_dir,
                repo_root=root,
                poll_timeout=poll_timeout,
                ros1_config_path=ros1_config_path,
            )
            if plugin_inventory is None:
                plugin_inventory = current_plugins
                tool_inventory = current_tools
            elif (
                current_plugins.get("inventory_sha256")
                != plugin_inventory.get("inventory_sha256")
                or current_tools.get("inventory_sha256")
                != tool_inventory.get("inventory_sha256")
            ):
                result["contract_passed"] = False
                result["passed"] = False
                result["runner_error_code"] = "inventory_drift"
                drift = {
                    "phase": "scenario",
                    "case_id": scenario.case_id,
                    "error_type": "InventoryDrift",
                    "message": (
                        "Plugin or Tool inventory changed within one "
                        "evaluation run."
                    ),
                }
                errors.append(drift)
            scenario_results.append(result)
        except Exception as exc:
            error = redact_dict({
                "phase": "scenario",
                "case_id": scenario.case_id,
                "error_type": type(exc).__name__,
                "message": str(exc),
            })
            errors.append(error)
            failed_record = redact_dict({
                "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
                **scenario.to_dict(),
                "plan_success": False,
                "dispatch_success": False,
                "task_success": False,
                "terminal_event": False,
                "observed_terminal_outcome": None,
                "terminal_outcome_match": False,
                "final_report_present": False,
                "memory_record_count": 0,
                "has_memory_record": False,
                "memory_requirement_met": (
                    scenario.min_memory_records == 0
                ),
                "target_contract_ok": False,
                "latency_ms": 0.0,
                "mission_status": "error",
                "runner_error_code": "scenario_runner_exception",
                "missing_data": [
                    "mission_run",
                    "mission_trace",
                    "structured_task",
                    "final_report",
                ],
                "contract_passed": False,
                "passed": False,
                "error": error,
            })
            scenario_results.append(failed_record)
            case_dir = bundle.run_dir / "cases" / scenario.case_id
            _write_artifact(case_dir / "scenario.json", scenario.to_dict())
            _write_artifact(case_dir / "scenario-record.json", failed_record)

    aggregate = aggregate_scenario_records(scenario_results)
    metrics = aggregate["metrics"]

    all_passed = bool(scenario_results) and all(
        result.get("contract_passed", False)
        for result in scenario_results
    )
    status = "pass" if all_passed else "warn"
    completed_at = datetime.now(timezone.utc).isoformat()
    summary = {
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "lane": suite.lane,
        "status": status,
        "scenario_count": len(scenario_results),
        "metrics": metrics,
        "metric_statistics": aggregate["metric_statistics"],
        "outcome_counts": aggregate["outcome_counts"],
        "suite": {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "source_sha256": suite.source_sha256,
        },
        "started_at": started_at,
        "completed_at": completed_at,
        "error_count": len(errors),
    }
    bundle.write_json("summary.json", redact_dict(summary))
    bundle.write_json("metric-definitions.json", METRIC_DEFINITIONS)
    bundle.write_jsonl("scenarios.jsonl", scenario_results)
    bundle.write_jsonl("errors.jsonl", errors)

    _consolidate_artifacts(bundle.run_dir, scenario_results)

    doctor_report = {
        "status": "warn",
        "adapter": adapter,
        "scenario_count": len(scenario_results),
        "metrics": metrics,
        "findings": [
            {
                "severity": "warn",
                "code": "readiness_not_probed",
                "message": (
                    "Deterministic integration evaluation completed; ROS, "
                    "Gazebo, Plugin backend readiness, and physical execution "
                    "were not probed by this report."
                ),
            }
        ],
    }
    bundle.write_json("doctor-report.json", redact_dict(doctor_report))

    resolved_plugins = plugin_inventory or {
        "plugins": [],
        "contributions": [],
        "inventory_sha256": canonical_json_sha256({}),
        "missing_reason": "no scenario reached inventory capture",
    }
    resolved_tools = tool_inventory or {
        "physical_tools": [],
        "agent_tools": {"tools": []},
        "inventory_sha256": canonical_json_sha256({}),
        "missing_reason": "no scenario reached inventory capture",
    }
    bundle.write_json("plugin-inventory.json", redact_dict(resolved_plugins))
    bundle.write_json("tool-inventory.json", redact_dict(resolved_tools))
    provenance = {
        **base_provenance,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "source_path": suite.source_path,
            "source_sha256": suite.source_sha256,
            "legacy_source_format": suite.legacy_source_format,
        },
        "randomness": {
            "scenario_seeds": sorted({
                scenario.seed for scenario in suite.scenarios
            }),
            "note": (
                "Seeds identify matched cases and are reserved for stochastic "
                "fixtures; this deterministic planner does not consume RNG."
            ),
        },
        "inventories": {
            "plugin_inventory_sha256": resolved_plugins["inventory_sha256"],
            "tool_inventory_sha256": resolved_tools["inventory_sha256"],
        },
    }
    bundle.write_json("provenance.json", redact_dict(provenance))
    bundle.write_json("paper-summary.json", redact_dict({
        "schema_version": EVALUATION_RUN_SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "lane": suite.lane,
        "suite_id": suite.suite_id,
        "suite_version": suite.suite_version,
        "scenario_count": len(scenario_results),
        "status": status,
        "metrics": metrics,
        "metric_statistics": aggregate["metric_statistics"],
        "outcome_counts": aggregate["outcome_counts"],
        "exclusions": [],
        "recompute_from": "scenarios.jsonl + metric-definitions.json",
    }))

    manifest.update({
        "status": status,
        "completed_at": completed_at,
        "suite": {
            "suite_id": suite.suite_id,
            "suite_version": suite.suite_version,
            "source_sha256": suite.source_sha256,
        },
        "scenario_count": len(scenario_results),
        "error_count": len(errors),
        "outcome_counts": aggregate["outcome_counts"],
        "inventories": provenance["inventories"],
    })
    bundle.replace_json("run-manifest.json", redact_dict(manifest))
    artifact_manifest = bundle.finalize_artifact_manifest()
    return {
        **summary,
        "output_dir": str(bundle.run_dir),
        "artifact_count": artifact_manifest["artifact_count"],
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic integration lane and write an immutable "
            "evaluation bundle."
        ),
    )
    parser.add_argument(
        "--scenarios",
        required=True,
        help="Path to rescue_scenarios.json fixture.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="A new or empty directory for this unique run.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Unique run ID. Defaults to a timestamp plus random suffix.",
    )
    parser.add_argument(
        "--adapter",
        default="simulator",
        choices=["dry-run", "simulator", "ros1"],
        help="Robot adapter to use (default: simulator).",
    )
    parser.add_argument(
        "--ros1-config",
        default=None,
        help=(
            "Compatibility argument; ROS1 inputs are rejected here and must "
            "use the ros_gazebo_system lane."
        ),
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=15.0,
        help="Seconds to poll each mission for terminal status (default: 15).",
    )
    args = parser.parse_args(argv)

    try:
        result = run_embodied_eval(
            scenarios_path=Path(args.scenarios),
            output_dir=Path(args.output_dir),
            adapter=args.adapter,
            poll_timeout=args.poll_timeout,
            ros1_config_path=args.ros1_config,
            run_id=args.run_id,
        )
    except (FileExistsError, ValueError) as exc:
        print(json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "error":
        return 1
    if result["status"] == "warn":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
