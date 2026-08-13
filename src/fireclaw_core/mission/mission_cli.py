from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_runtime import MissionRuntimePaths, build_mission_agent_from_paths
from fireclaw_core.planner.planner_builder import build_planner as _build_planner_shared
from fireclaw_core.rag.runtime_retrieval import RagRuntimeConfig


SUCCESS_STATUSES = {"accepted", "duplicate", "running", "succeeded"}
CANCEL_SUCCESS_STATUSES = {"cancel_requested", "already_terminal", "empty"}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "robot-gateway":
        from fireclaw_core.gateway.gateway import main as gateway_main
        return gateway_main(sys.argv[2:])

    parser = argparse.ArgumentParser(description="Run FireClaw mission-control commands.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    setup = subparsers.add_parser(
        "setup",
        help="Prepare a safe first-run Profile and remember it for later commands.",
    )
    setup.add_argument(
        "--mode",
        choices=["simulation", "real"],
        default=None,
        help="Setup mode; the interactive default is the safe simulation path.",
    )
    setup.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Validate and activate an existing Profile instead of generating one.",
    )
    setup.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Advanced override for FIRECLAW_HOME and generated user files.",
    )
    setup.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="Advanced override for the FireClaw source tree containing simulation assets.",
    )
    setup.add_argument(
        "--no-deploy",
        action="store_true",
        help="Validate and activate the Profile without preparing Runtime files.",
    )
    setup.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output and never prompt.",
    )

    submit = subparsers.add_parser("submit-subtask", help="Submit an explicit subtask to a Robot Agent.")
    submit.add_argument("--robot", required=True, help="Target robot_id from the robot registry.")
    submit.add_argument("--command", required=True, help="Natural-language command for the Robot Agent.")
    submit.add_argument("--session-id", default=None, help="Mission/session id.")
    submit.add_argument("--dedupe-key", default=None, help="Idempotency key for robot task submission.")
    _add_shared_paths(submit)

    trace = subparsers.add_parser("trace", help="Read and aggregate a mission trace.")

    trace.add_argument("mission_id", help="Mission id to inspect.")
    _add_shared_paths(trace)
    _add_runtime_paths(trace)

    cancel = subparsers.add_parser("cancel", help="Cancel all active subtasks for a mission.")
    cancel.add_argument("mission_id", help="Mission id to cancel.")
    _add_shared_paths(cancel)
    _add_runtime_paths(cancel)

    plan = subparsers.add_parser("plan-mission", help="Plan and submit mission subtasks from a natural-language command.")
    plan.add_argument("--command", required=True, help="Natural-language mission command.")
    plan.add_argument("--session-id", default=None, help="Mission/session id.")
    plan.add_argument("--planner", choices=["deterministic", "llm"], default="deterministic",
                       help="Planner backend: 'deterministic' (regex rules) or 'llm' (LLM with tool calling).")
    plan.add_argument("--provider-base-url", default=None, help="LLM provider base URL (required when --planner=llm).")
    plan.add_argument("--provider-api-key", default=None, help="LLM provider API key (required when --planner=llm).")
    plan.add_argument("--model", default=None, help="LLM model id (required when --planner=llm).")
    plan.add_argument("--catalog", default=None, help="Path to model catalog JSON file.")
    plan.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
    plan.add_argument("--use-scheduler", dest="use_scheduler", action="store_true", default=True,
                       help="Use MissionScheduler for execution group ordering (default).")
    plan.add_argument("--no-use-scheduler", dest="use_scheduler", action="store_false",
                       help="Disable MissionScheduler, submit subtasks directly.")
    _add_shared_paths(plan)
    _add_runtime_paths(plan)

    events = subparsers.add_parser("events", help="Aggregate and list mission events from Robot Agents.")
    events.add_argument("mission_id", help="Mission id to collect events for.")
    events.add_argument("--robot-id", default=None, help="Filter events by robot id.")
    events.add_argument("--type", dest="event_type", default=None, help="Filter events by event type.")
    events.add_argument("--limit", type=int, default=200, help="Maximum number of events to return.")
    _add_shared_paths(events)
    _add_runtime_paths(events)

    corrections = subparsers.add_parser("corrections", help="List operator correction records for a mission.")
    corrections.add_argument("mission_id", help="Mission id to list corrections for.")
    corrections.add_argument("--memory-path", default="mission_memory.jsonl", help="Path to mission memory JSONL file.")
    corrections.add_argument("--limit", type=int, default=10, help="Maximum number of corrections to return.")

    memory = subparsers.add_parser("memory", help="Manage mission memory records.")
    memory.add_argument("--memory-path", default="mission_memory.jsonl", help="Path to mission memory JSONL file.")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)

    mem_list = memory_sub.add_parser("list", help="List mission memory records.")
    mem_list.add_argument("--mission-id", required=True, help="Filter by mission id.")
    mem_list.add_argument("--type", dest="record_type", default=None, help="Filter by record type (outcome, observation, correction, lesson).")
    mem_list.add_argument("--limit", type=int, default=None, help="Max records to return.")

    mem_add = memory_sub.add_parser("add", help="Add a mission memory record.")
    mem_add.add_argument("--mission-id", required=True, help="Mission id.")
    mem_add.add_argument("--type", dest="record_type", required=True, help="Record type (outcome, observation, correction, lesson).")
    mem_add.add_argument("--content", required=True, help="JSON content string.")
    mem_add.add_argument("--robot-id", default=None, help="Robot id.")
    mem_add.add_argument("--subtask-id", default=None, help="Subtask id.")

    mem_summary = memory_sub.add_parser("summary", help="Show mission memory summary.")
    mem_summary.add_argument("--mission-id", default=None, help="Filter by mission id.")

    replay = subparsers.add_parser("replay", help="Reconstruct a mission timeline from persistent data.")
    replay.add_argument("mission_id", help="Mission id to replay.")
    _add_shared_paths(replay)
    _add_runtime_paths(replay)

    approval = subparsers.add_parser("approval", help="Manage approval requests.")
    approval.add_argument("--approval-path", default="mission_approvals.jsonl", help="Path to approval store JSONL file.")
    approval_sub = approval.add_subparsers(dest="approval_command", required=True)

    app_list = approval_sub.add_parser("list", help="List approval requests.")
    app_list.add_argument("--mission-id", default=None, help="Filter by mission id.")
    app_list.add_argument("--status", default=None, help="Filter by status (pending, approved, denied, expired).")

    app_request = approval_sub.add_parser("request", help="Create an approval request.")
    app_request.add_argument("--mission-id", required=True, help="Mission id.")
    app_request.add_argument("--action", required=True, help="Action requiring approval.")
    app_request.add_argument("--risk-level", required=True, help="Risk level (low, medium, high, critical).")
    app_request.add_argument("--command", required=True, help="Command to execute.")

    app_decide = approval_sub.add_parser("decide", help="Approve or deny an approval request.")
    app_decide.add_argument("request_id", help="Request id to decide on.")
    app_decide.add_argument("--decision", required=True, choices=["approve", "deny"], help="Decision: approve or deny.")
    app_decide.add_argument("--reason", default=None, help="Reason for the decision.")

    lifecycle = subparsers.add_parser("lifecycle-check", help="Check task/Robot Agent lifecycle consistency.")
    lifecycle.add_argument("--task-registry", required=True, help="Path to task registry JSONL.")
    lifecycle.add_argument(
        "--subagent-registry",
        required=True,
        help="Path to the legacy-named Robot Agent run registry JSONL.",
    )
    lifecycle.add_argument("--stale-threshold-seconds", type=float, default=300.0, help="Seconds before a task is considered stale.")

    security_audit = subparsers.add_parser(
        "security-audit",
        help="Audit deployment security without starting either Gateway.",
    )
    security_audit.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the fireclaw.toml used for deployment.",
    )
    security_audit.add_argument("--runtime-root", default=None)
    security_audit.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        help="Plugin descriptor directory to audit. Repeat as needed.",
    )
    security_audit.add_argument(
        "--deep",
        action="store_true",
        help="Recursively inspect configured plugin descriptor directories.",
    )
    security_audit.add_argument(
        "--fail-on",
        choices=("critical", "warn", "never"),
        default="critical",
    )

    serve = subparsers.add_parser("serve", help="Start a persistent MissionGateway server.")
    serve.add_argument("--config", type=Path, default=None, help="Path to fireclaw.toml config file.")
    serve.add_argument(
        "--runtime-root",
        default=None,
        help=(
            "Stable process working directory. Defaults to the config "
            "directory, FIRECLAW_HOME, or ~/.fireclaw."
        ),
    )
    serve.add_argument("--adapter", default=None, help="Robot adapter label for deployment metadata.")
    serve.add_argument("--ros1-config", default=None, help="Path to ROS1 adapter config for robot gateways.")
    serve.add_argument("--host", default=None, help="Host to bind.")
    serve.add_argument("--port", type=int, default=None, help="Port to bind.")
    serve.add_argument(
        "--api-token",
        default=None,
        help=(
            "Mission Gateway bearer token. Prefer [server].api_token or "
            "FIRECLAW_GATEWAY_TOKEN so the secret is not exposed in process args."
        ),
    )
    serve.add_argument(
        "--robot-gateway-api-token",
        default=None,
        help=(
            "Bearer token used for Robot Gateways. Prefer "
            "[mission].robot_gateway_api_token or FIRECLAW_ROBOT_GATEWAY_TOKEN."
        ),
    )
    serve.add_argument(
        "--tls",
        action="store_true",
        default=None,
        help="Enable TLS for the Mission Gateway listener.",
    )
    serve.add_argument("--tls-cert-file", default=None)
    serve.add_argument("--tls-key-file", default=None)
    serve.add_argument("--tls-ca-file", default=None)
    serve.add_argument(
        "--tls-require-client-cert",
        action="store_true",
        default=None,
        help="Require Mission Gateway client certificates.",
    )
    serve.add_argument("--robot-gateway-ca-file", default=None)
    serve.add_argument("--robot-gateway-client-cert-file", default=None)
    serve.add_argument("--robot-gateway-client-key-file", default=None)
    serve.add_argument("--data-dir", type=Path, default=None, help="Persistent data directory.")
    serve.add_argument(
        "--embodied-runtime-mode",
        choices=("real", "simulation", "replay"),
        default=None,
        help="Enable embodied memory in an explicit runtime domain.",
    )
    _add_memory_rag_options(serve, optional_defaults=True)
    _add_external_knowledge_rag_options(serve, optional_defaults=True)
    serve.add_argument("--planner", choices=["deterministic", "llm"], default=None, help="Planner backend.")
    serve.add_argument("--provider-base-url", default=None, help="LLM provider base URL.")
    serve.add_argument("--provider-api-key", default=None, help="LLM provider API key.")
    serve.add_argument("--model", default=None, help="LLM model id.")
    serve.add_argument("--catalog", default=None, help="Path to model catalog JSON file.")
    serve.add_argument("--llm-trace-path", default=None, help="Path to LLM trace JSONL file.")
    serve.add_argument("--robot-agent", action="store_true", default=None, help="Enable robot-local agent planning.")
    serve.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default=None, help="Robot-local agent planner backend.")
    serve.add_argument("--robot-agent-provider-base-url", default=None, help="Robot-local agent LLM provider base URL.")
    serve.add_argument("--robot-agent-provider-api-key", default=None, help="Robot-local agent LLM provider API key.")
    serve.add_argument("--robot-agent-model", default=None, help="Robot-local agent LLM model id.")
    serve.add_argument("--robot-agent-catalog", default=None, help="Path to robot-local model catalog JSON file.")
    serve.add_argument(
        "--robot-profile",
        action="append",
        default=None,
        help="Path to robot profile TOML. Repeat for multiple robots.",
    )

    mission = subparsers.add_parser("mission", help="Open the interactive mission console.")
    mission.add_argument("--server", default="http://127.0.0.1:8766", help="MissionGateway base URL.")
    mission.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    mission.add_argument(
        "--api-token",
        default=None,
        help="Mission Gateway bearer token; FIRECLAW_GATEWAY_TOKEN is the preferred fallback.",
    )
    mission.add_argument("--tls-ca-file", default=None)
    mission.add_argument("--tls-client-cert-file", default=None)
    mission.add_argument("--tls-client-key-file", default=None)

    status = subparsers.add_parser(
        "status",
        help="Show actionable robot readiness, not just process liveness.",
    )
    status.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Robot/deployment Profile TOML; defaults to the Profile selected by setup.",
    )
    status.add_argument("--output-root", type=Path, default=None)
    status.add_argument(
        "--gateway",
        default=None,
        help="Override robot.base_url from the Profile.",
    )
    status.add_argument(
        "--api-token",
        default=None,
        help=(
            "Robot Gateway bearer token; defaults to "
            "FIRECLAW_ROBOT_GATEWAY_TOKEN."
        ),
    )
    status.add_argument(
        "--server",
        default=None,
        help=(
            "Override the Mission Gateway URL used for the integrated Fleet "
            "Doctor probe; otherwise resolve it from the Profile."
        ),
    )
    status.add_argument(
        "--mission-api-token",
        default=None,
        help=(
            "Mission Gateway bearer token; defaults to "
            "FIRECLAW_GATEWAY_TOKEN."
        ),
    )
    status.add_argument("--timeout", type=float, default=10.0)
    status.add_argument(
        "--no-runtime-check",
        action="store_true",
        help="Skip live Runtime probes; the result cannot be READY.",
    )
    status.add_argument("--tls-ca-file", default=None)
    status.add_argument("--tls-client-cert-file", default=None)
    status.add_argument("--tls-client-key-file", default=None)
    status.add_argument("--mission-tls-ca-file", default=None)
    status.add_argument("--mission-tls-client-cert-file", default=None)
    status.add_argument("--mission-tls-client-key-file", default=None)
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit the complete machine-readable evidence envelope.",
    )

    doctor = subparsers.add_parser(
        "doctor",
        help="Run fleet diagnostics through the Mission Gateway.",
    )
    doctor.add_argument(
        "--server",
        default="http://127.0.0.1:8766",
        help="Mission Gateway base URL.",
    )
    doctor.add_argument("--api-token", default=None)
    doctor.add_argument("--timeout", type=float, default=10.0)
    doctor.add_argument("--tls-ca-file", default=None)
    doctor.add_argument("--tls-client-cert-file", default=None)
    doctor.add_argument("--tls-client-key-file", default=None)
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit the complete machine-readable diagnostic envelope.",
    )

    recover = subparsers.add_parser(
        "recover",
        help="Guide the two-stage resource-admission recovery protocol.",
    )
    recover.add_argument(
        "--profile",
        type=Path,
        default=None,
        help="Robot Profile TOML; defaults to the Profile selected by setup.",
    )
    recover.add_argument(
        "--gateway",
        default=None,
        help="Override robot.base_url from the Profile.",
    )
    recover.add_argument("--api-token", default=None)
    recover.add_argument("--timeout", type=float, default=15.0)
    recover.add_argument(
        "--reason",
        default=None,
        help="Operator-audited reason for requesting recovery.",
    )
    recover.add_argument(
        "--request-id",
        default=None,
        help="Confirm an existing pending request instead of creating one.",
    )
    recover.add_argument(
        "--confirmation-phrase",
        default=None,
        help=(
            "Exact phrase returned by the request. Omitting it uses the "
            "interactive prompt; there is intentionally no --yes shortcut."
        ),
    )
    recover.add_argument(
        "--request-only",
        action="store_true",
        help="Collect stop evidence and create a request without confirming it.",
    )
    recover.add_argument("--tls-ca-file", default=None)
    recover.add_argument("--tls-client-cert-file", default=None)
    recover.add_argument("--tls-client-key-file", default=None)
    recover.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable output and never prompt for confirmation.",
    )

    from fireclaw_core.infra.hardware_safety_acceptance import (
        SCENARIOS,
        SIGNAL_STALE_TARGETS,
    )

    hardware_safety = subparsers.add_parser(
        "hardware-safety",
        help="Prepare and run real-robot hardware safety acceptance.",
    )
    hardware_safety_sub = hardware_safety.add_subparsers(
        dest="hardware_safety_command",
        required=True,
    )
    safety_preflight = hardware_safety_sub.add_parser(
        "preflight",
        help="Validate the Profile and live ROS contract without actuation.",
    )
    safety_preflight.add_argument("--profile", type=Path, required=True)
    safety_preflight.add_argument(
        "--offline",
        action="store_true",
        help="Run static preparation only; the result cannot be READY.",
    )
    safety_preflight.add_argument("--json", action="store_true")

    safety_accept = hardware_safety_sub.add_parser(
        "accept",
        help="Run one operator-confirmed field acceptance scenario.",
    )
    safety_accept.add_argument("--profile", type=Path, required=True)
    safety_accept.add_argument("--scenario", choices=SCENARIOS, required=True)
    safety_accept.add_argument(
        "--target-signal",
        choices=SIGNAL_STALE_TARGETS,
        default=None,
        help=(
            "Required for signal_stale; for actuator_motion use actuators "
            "or independent_motion."
        ),
    )
    safety_accept.add_argument("--operator-id", required=True)
    safety_accept.add_argument("--firmware-version", required=True)
    safety_accept.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("results/hardware-safety"),
    )
    safety_accept.add_argument(
        "--confirmation-phrase",
        default=None,
        help="Exact phrase shown by the command; there is no --yes shortcut.",
    )
    safety_accept.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON and never prompt for confirmation.",
    )

    safety_verify = hardware_safety_sub.add_parser(
        "verify",
        help="Verify the content digest and required acceptance fields.",
    )
    safety_verify.add_argument("--artifact", type=Path, required=True)
    safety_verify.add_argument("--json", action="store_true")

    safety_report = hardware_safety_sub.add_parser(
        "report",
        help="Require every scenario for one exact Profile and firmware.",
    )
    safety_report.add_argument("--profile", type=Path, required=True)
    safety_report.add_argument("--firmware-version", required=True)
    safety_report.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("results/hardware-safety"),
    )
    safety_report.add_argument("--json", action="store_true")

    from fireclaw_core.infra.fault_injection import SCENARIOS as FAULT_SCENARIOS

    fault_test = subparsers.add_parser(
        "fault-test",
        help="Run repeatable simulation/process fault-injection acceptance.",
    )
    fault_test_sub = fault_test.add_subparsers(
        dest="fault_test_command",
        required=True,
    )
    fault_run = fault_test_sub.add_parser(
        "run",
        help="Run selected fault scenarios and write an immutable evidence bundle.",
    )
    fault_run.add_argument(
        "--scenario",
        action="append",
        choices=tuple(FAULT_SCENARIOS),
        default=None,
        help="Scenario to run; repeat the flag or omit it to run all seven.",
    )
    fault_run.add_argument(
        "--live-ros",
        action="store_true",
        help=(
            "Start, kill, and restart an isolated local roscore for the ROS "
            "master scenario. No robot actuation is performed."
        ),
    )
    fault_run.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="FireClaw repository containing the selected pytest nodes.",
    )
    fault_run.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("results/fault-injection"),
    )
    fault_run.add_argument("--json", action="store_true")

    fault_verify = fault_test_sub.add_parser(
        "verify",
        help="Verify the manifest and digests for one fault-test run.",
    )
    fault_verify.add_argument("--run-dir", type=Path, required=True)
    fault_verify.add_argument("--json", action="store_true")

    robot_gateway = subparsers.add_parser("robot-gateway", help="Start a robot-local FireClawGateway.")
    robot_gateway.add_argument("robot_gateway_args", nargs=argparse.REMAINDER)

    robot_profile = subparsers.add_parser("robot-profile", help="Manage robot capability profiles.")
    robot_profile_sub = robot_profile.add_subparsers(dest="robot_profile_command", required=True)
    profile_export = robot_profile_sub.add_parser("export", help="Export a profile to robots.json.")
    profile_export.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_export.add_argument("--output", required=True, help="Path to robots.json output.")

    profile_discover = robot_profile_sub.add_parser(
        "discover",
        help="Discover ROS1 sensor topics and write suggested profile rules.",
    )
    profile_discover.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_discover.add_argument("--output", default=None, help="Path to suggested TOML output. Required unless --write-profile is given.")
    profile_discover.add_argument(
        "--write-profile",
        action="store_true",
        help="Explicitly write suggested discovery rules to the profile instead of --output.",
    )

    profile_diff = robot_profile_sub.add_parser(
        "diff-discovery",
        help="Compare current ROS1 sensor discovery against profile rules and fingerprint.",
    )
    profile_diff.add_argument("--profile", required=True, help="Path to robot profile TOML.")

    profile_confirm = robot_profile_sub.add_parser(
        "confirm-discovery",
        help="Confirm current ROS1 sensor discovery and write auditable profile rules.",
    )
    profile_confirm.add_argument("--profile", required=True, help="Path to robot profile TOML.")
    profile_confirm.add_argument("--confirmed-by", required=True, help="Operator ID that reviewed the discovery mapping.")
    profile_confirm.add_argument("--confirmed-at", default=None, help="ISO-8601 confirmation time. Defaults to current UTC time.")

    deploy = subparsers.add_parser(
        "deploy",
        help="Plan, apply, or inspect Plugin Runtime deployment.",
    )
    deploy_sub = deploy.add_subparsers(dest="deploy_command", required=True)
    for deploy_name, deploy_help in (
        ("plan", "Resolve providers and show the immutable deployment plan."),
        ("apply", "Build/install selected Runtime providers and write receipts."),
        ("status", "Verify the installed deployment and ROS readiness."),
        (
            "run",
            "Supervise bringup, Robot Gateway, and Mission Gateway as one runtime.",
        ),
    ):
        deploy_parser = deploy_sub.add_parser(deploy_name, help=deploy_help)
        deploy_parser.add_argument(
            "--profile",
            type=Path,
            default=None,
            help="Robot TOML Profile; defaults to the Profile selected by setup.",
        )
        deploy_parser.add_argument(
            "--output-root",
            type=Path,
            default=None,
            help="Override deployment.output_root.",
        )
        if deploy_name == "status":
            deploy_parser.add_argument(
                "--no-runtime-check",
                action="store_true",
                help="Verify immutable install artifacts without probing the live ROS graph.",
            )
        if deploy_name == "run":
            deploy_parser.add_argument(
                "--runtime-ready-timeout",
                type=float,
                default=120.0,
            )
            deploy_parser.add_argument(
                "--gateway-ready-timeout",
                type=float,
                default=30.0,
            )
            deploy_parser.add_argument(
                "--mission-gateway-ready-timeout",
                type=float,
                default=30.0,
            )
            deploy_parser.add_argument(
                "--probe-interval",
                type=float,
                default=1.0,
            )
            deploy_parser.add_argument(
                "--readiness-monitor-interval",
                type=float,
                default=5.0,
            )
            deploy_parser.add_argument(
                "--readiness-failure-limit",
                type=int,
                default=3,
            )
            deploy_parser.add_argument(
                "--monitor-interval",
                type=float,
                default=0.25,
            )
            deploy_parser.add_argument(
                "--shutdown-timeout",
                type=float,
                default=15.0,
            )
            deploy_parser.add_argument(
                "--terminate-timeout",
                type=float,
                default=3.0,
            )
            deploy_parser.add_argument(
                "--simulation-restarts",
                type=int,
                default=2,
                help="Bounded full-stack restarts; ignored for real deployments.",
            )
            deploy_parser.add_argument(
                "--restart-backoff",
                type=float,
                default=2.0,
            )
    service = deploy_sub.add_parser(
        "service",
        help="Manage the generated systemd user service explicitly.",
    )
    service_sub = service.add_subparsers(
        dest="service_command",
        required=True,
    )
    for service_name, service_help in (
        ("render", "Show the integrity-checked generated unit."),
        ("install", "Install, enable, and start the generated unit."),
        ("status", "Inspect installed unit integrity and systemd state."),
        ("start", "Start the installed managed unit."),
        ("stop", "Stop the installed managed unit safely."),
        ("restart", "Restart the installed managed unit."),
        ("uninstall", "Stop, disable, and remove the managed unit."),
    ):
        service_parser = service_sub.add_parser(service_name, help=service_help)
        service_parser.add_argument(
            "--profile",
            type=Path,
            default=None,
            help="Robot TOML Profile; defaults to the Profile selected by setup.",
        )
        service_parser.add_argument(
            "--output-root",
            type=Path,
            default=None,
            help="Override deployment.output_root.",
        )
        if service_name == "install":
            service_parser.add_argument(
                "--no-enable",
                action="store_true",
                help="Stage the unit without enabling it at login.",
            )
            service_parser.add_argument(
                "--no-start",
                action="store_true",
                help="Install the unit without starting it now.",
            )

    args = parser.parse_args()
    if args.command_name == "setup":
        from fireclaw_core.infra.user_setup import handle_setup

        return handle_setup(args)
    if args.command_name == "submit-subtask":
        result = _build_mission_agent(args).submit_subtask(
            args.robot,
            args.command,
            session_id=args.session_id,
            dedupe_key=args.dedupe_key,
            operator=_mission_operator(),
        )
        _print_json(result)
        return 0 if result.get("status") in SUCCESS_STATUSES else 1
    if args.command_name == "trace":
        result = _build_mission_agent(args).mission_trace(args.mission_id)
        _print_json(result)
        return 0 if result.get("status") != "not_found" else 1
    if args.command_name == "cancel":
        result = _build_mission_agent(args).cancel_mission(args.mission_id, operator=_mission_operator())
        _print_json(result)
        return 0 if result.get("status") in CANCEL_SUCCESS_STATUSES else 1
    if args.command_name == "plan-mission":
        agent = _build_mission_agent(args, planner=_build_planner(args))
        result = agent.plan_and_submit(args.command, session_id=args.session_id, operator=_mission_operator(), use_scheduler=args.use_scheduler)
        _print_json(result)
        return 0 if result.get("status") == "planned" else 1
    if args.command_name == "events":
        result = _build_mission_agent(args).mission_events(
            args.mission_id,
            robot_id=args.robot_id,
            event_type=args.event_type,
            limit=args.limit,
        )
        _print_json(result)
        return 0
    if args.command_name == "replay":
        result = _build_mission_agent(args).replay_incident(args.mission_id)
        _print_json(result)
        return 0 if result.get("status") not in ("not_found", "not_configured") else 1
    if args.command_name == "corrections":
        return _handle_corrections(args)
    if args.command_name == "memory":
        return _handle_memory(args)
    if args.command_name == "approval":
        return _handle_approval(args)
    if args.command_name == "deploy":
        return _handle_deploy(args)
    if args.command_name == "lifecycle-check":
        from fireclaw_core.lifecycle import LifecycleMaintenanceRunner
        from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
        from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
        report = LifecycleMaintenanceRunner(
            task_registry=JsonlTaskRegistryStore(args.task_registry),
            subagent_registry=JsonlSubagentRegistry(args.subagent_registry),
        ).run(stale_threshold_seconds=args.stale_threshold_seconds)
        _print_json(report)
        return 0 if report["status"] == "ok" else 2
    if args.command_name == "security-audit":
        from fireclaw_core.security.audit import run_security_audit

        report = run_security_audit(
            config_path=args.config,
            runtime_root=args.runtime_root,
            plugin_dirs=args.plugin_dir,
            deep=args.deep,
        )
        _print_json(report.to_dict())
        if args.fail_on == "never":
            return 0
        if report.summary.critical:
            return 2
        if args.fail_on == "warn" and report.summary.warn:
            return 1
        return 0
    if args.command_name == "serve":
        from fireclaw_core.gateway.config import find_config, load_config, merge_config
        from fireclaw_core.gateway.serve import run_server_blocking
        cfg: dict[str, object] = {}
        config_path = find_config(args.config)
        if config_path is not None:
            cfg = load_config(config_path)
        merged = merge_config(cfg, {
            "adapter": args.adapter,
            "runtime_root": args.runtime_root,
            "ros1_config": args.ros1_config,
            "host": args.host,
            "port": args.port,
            "api_token": args.api_token,
            "robot_gateway_client_api_token": args.robot_gateway_api_token,
            "tls_enabled": args.tls if args.tls else None,
            "tls_cert_file": args.tls_cert_file,
            "tls_key_file": args.tls_key_file,
            "tls_ca_file": args.tls_ca_file,
            "tls_require_client_cert": (
                args.tls_require_client_cert
                if args.tls_require_client_cert
                else None
            ),
            "robot_gateway_client_tls_ca_file": args.robot_gateway_ca_file,
            "robot_gateway_client_tls_cert_file": (
                args.robot_gateway_client_cert_file
            ),
            "robot_gateway_client_tls_key_file": (
                args.robot_gateway_client_key_file
            ),
            "data_dir": str(args.data_dir) if args.data_dir else None,
            "planner_type": args.planner,
            "provider_base_url": args.provider_base_url,
            "provider_api_key": args.provider_api_key,
            "model": args.model,
            "model_catalog_path": args.catalog,
            "llm_trace_path": args.llm_trace_path,
            "robot_agent_enabled": args.robot_agent if args.robot_agent else None,
            "robot_agent_planner": args.robot_agent_planner,
            "robot_agent_provider_base_url": args.robot_agent_provider_base_url,
            "robot_agent_provider_api_key": args.robot_agent_provider_api_key,
            "robot_agent_model": args.robot_agent_model,
            "robot_agent_model_catalog_path": args.robot_agent_catalog,
            "mission_robot_profiles": args.robot_profile,
            "embodied_runtime_mode": args.embodied_runtime_mode,
            "deployment": None,
            "memory_rag_backend": args.memory_rag_backend,
            "memory_rag_bm25_index_dir": args.memory_rag_bm25_index_dir,
            "memory_rag_dense_index_dir": args.memory_rag_dense_index_dir,
            "memory_rag_generation_root": args.memory_rag_generation_root,
            "memory_rag_embedding_provider": args.memory_rag_embedding_provider,
            "memory_rag_embedding_model_path": args.memory_rag_embedding_model_path,
            "memory_rag_reranker_provider": args.memory_rag_reranker_provider,
            "memory_rag_reranker_model_path": args.memory_rag_reranker_model_path,
            "memory_rag_device": args.memory_rag_device,
            "memory_rag_candidate_multiplier": args.memory_rag_candidate_multiplier,
            "memory_rag_rrf_k": args.memory_rag_rrf_k,
            "knowledge_rag_backend": args.knowledge_rag_backend,
            "knowledge_rag_bm25_index_dir": args.knowledge_rag_bm25_index_dir,
            "knowledge_rag_dense_index_dir": args.knowledge_rag_dense_index_dir,
            "knowledge_rag_generation_root": args.knowledge_rag_generation_root,
            "knowledge_rag_embedding_provider": args.knowledge_rag_embedding_provider,
            "knowledge_rag_embedding_model_path": args.knowledge_rag_embedding_model_path,
            "knowledge_rag_reranker_provider": args.knowledge_rag_reranker_provider,
            "knowledge_rag_reranker_model_path": args.knowledge_rag_reranker_model_path,
            "knowledge_rag_device": args.knowledge_rag_device,
            "knowledge_rag_candidate_multiplier": args.knowledge_rag_candidate_multiplier,
            "knowledge_rag_rrf_k": args.knowledge_rag_rrf_k,
        })
        memory_rag = _build_memory_rag_config(
            argparse.Namespace(**{
                key: merged.get(key)
                for key in (
                    "memory_rag_backend",
                    "memory_rag_bm25_index_dir",
                    "memory_rag_dense_index_dir",
                    "memory_rag_generation_root",
                    "memory_rag_embedding_provider",
                    "memory_rag_embedding_model_path",
                    "memory_rag_reranker_provider",
                    "memory_rag_reranker_model_path",
                    "memory_rag_device",
                    "memory_rag_candidate_multiplier",
                    "memory_rag_rrf_k",
                )
            })
        )
        external_knowledge_rag = _build_external_knowledge_rag_config(
            argparse.Namespace(**{
                key: merged.get(key)
                for key in (
                    "knowledge_rag_backend",
                    "knowledge_rag_bm25_index_dir",
                    "knowledge_rag_dense_index_dir",
                    "knowledge_rag_generation_root",
                    "knowledge_rag_embedding_provider",
                    "knowledge_rag_embedding_model_path",
                    "knowledge_rag_reranker_provider",
                    "knowledge_rag_reranker_model_path",
                    "knowledge_rag_device",
                    "knowledge_rag_candidate_multiplier",
                    "knowledge_rag_rrf_k",
                )
            })
        )
        from fireclaw_core.gateway.transport import (
            GatewayTlsClientConfig,
            GatewayTlsServerConfig,
        )
        from fireclaw_core.gateway.network_security import (
            gateway_network_policy_from_config,
        )
        from fireclaw_core.infra.runtime_paths import (
            fireclaw_runtime_directory,
            resolve_fireclaw_runtime_root,
        )

        runtime_root = resolve_fireclaw_runtime_root(
            configured=merged.get("runtime_root"),
            config_path=config_path,
        )
        with fireclaw_runtime_directory(runtime_root):
            run_server_blocking(
                adapter=str(merged.get("adapter", "simulator")),
                ros1_config=merged.get("ros1_config"),
                host=str(merged.get("host", "127.0.0.1")),
                port=int(merged.get("port", 8766)),
                api_token=merged.get("api_token"),
                robot_gateway_api_token=merged.get(
                    "robot_gateway_client_api_token"
                ),
                tls=GatewayTlsServerConfig(
                    enabled=bool(merged.get("tls_enabled", False)),
                    cert_file=merged.get("tls_cert_file"),
                    key_file=merged.get("tls_key_file"),
                    ca_file=merged.get("tls_ca_file"),
                    require_client_cert=bool(
                        merged.get("tls_require_client_cert", False)
                    ),
                ),
                robot_gateway_tls=GatewayTlsClientConfig(
                    ca_file=merged.get("robot_gateway_client_tls_ca_file"),
                    cert_file=merged.get(
                        "robot_gateway_client_tls_cert_file"
                    ),
                    key_file=merged.get(
                        "robot_gateway_client_tls_key_file"
                    ),
                ),
                network_policy=gateway_network_policy_from_config(
                    merged.get("network")
                ),
                planner_type=str(
                    merged.get("planner_type", "deterministic")
                ),
                provider_base_url=merged.get("provider_base_url"),
                provider_api_key=merged.get("provider_api_key"),
                model=merged.get("model"),
                model_catalog_path=merged.get("model_catalog_path"),
                llm_trace_path=merged.get("llm_trace_path"),
                data_dir=Path(str(merged.get("data_dir", "data"))),
                robot_agent_enabled=bool(
                    merged.get("robot_agent_enabled", False)
                ),
                robot_agent_planner=str(
                    merged.get("robot_agent_planner", "deterministic")
                ),
                robot_agent_provider_base_url=merged.get(
                    "robot_agent_provider_base_url"
                ),
                robot_agent_provider_api_key=merged.get(
                    "robot_agent_provider_api_key"
                ),
                robot_agent_model=merged.get("robot_agent_model"),
                robot_agent_model_catalog_path=merged.get(
                    "robot_agent_model_catalog_path"
                ),
                robot_profiles=(
                    tuple(merged["mission_robot_profiles"])
                    if merged.get("mission_robot_profiles")
                    else None
                ),
                embodied_runtime_mode=(
                    str(merged["embodied_runtime_mode"])
                    if merged.get("embodied_runtime_mode") is not None
                    else None
                ),
                memory_rag=memory_rag,
                external_knowledge_rag=external_knowledge_rag,
                deployment_config=(
                    dict(merged["deployment"])
                    if isinstance(merged.get("deployment"), dict)
                    else None
                ),
            )
        return 0
    if args.command_name == "mission":
        from fireclaw_core.gateway.transport import GatewayTlsClientConfig
        from fireclaw_core.mission.interactive import run_interactive
        run_interactive(
            server_url=args.server,
            timeout=args.timeout,
            api_token=args.api_token,
            tls=GatewayTlsClientConfig(
                ca_file=args.tls_ca_file,
                cert_file=args.tls_client_cert_file,
                key_file=args.tls_client_key_file,
            ),
        )
        return 0
    if args.command_name == "status":
        from fireclaw_core.infra.operator_cli import handle_status

        return handle_status(args)
    if args.command_name == "doctor":
        from fireclaw_core.infra.operator_cli import handle_doctor

        return handle_doctor(args)
    if args.command_name == "recover":
        from fireclaw_core.infra.operator_cli import handle_recover

        if args.request_id is not None and args.request_only:
            parser.error("--request-id and --request-only cannot be used together")
        if args.request_only and args.confirmation_phrase is not None:
            parser.error(
                "--request-only and --confirmation-phrase cannot be used together"
            )
        return handle_recover(args)
    if args.command_name == "hardware-safety":
        from fireclaw_core.infra.hardware_safety_acceptance import (
            handle_hardware_safety,
        )

        return handle_hardware_safety(args)
    if args.command_name == "fault-test":
        from fireclaw_core.infra.fault_injection import handle_fault_test

        return handle_fault_test(args)
    if args.command_name == "robot-gateway":
        from fireclaw_core.gateway.gateway import main as gateway_main
        return gateway_main(args.robot_gateway_args)
    if args.command_name == "robot-profile":
        return _handle_robot_profile(args)
    parser.error(f"Unknown command: {args.command_name}")
    return 1


def _handle_corrections(args: argparse.Namespace) -> int:
    store = MissionMemoryStore(args.memory_path)
    records = store.search(mission_id=args.mission_id, record_type="correction", limit=args.limit)
    _print_json({"mission_id": args.mission_id, "corrections": [r.to_dict() for r in records]})
    return 0


def _handle_memory(args: argparse.Namespace) -> int:
    import uuid
    from datetime import datetime, timezone

    store = MissionMemoryStore(args.memory_path)

    if args.memory_command == "list":
        records = store.list_records(mission_id=args.mission_id, record_type=args.record_type)
        if args.limit is not None:
            records = records[: args.limit]
        print(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2))
        return 0

    if args.memory_command == "add":
        try:
            content = json.loads(args.content)
        except json.JSONDecodeError as exc:
            print(f"Error: --content is not valid JSON: {exc}", file=sys.stderr)
            return 1
        record = MissionMemoryRecord(
            record_id=f"mem-{uuid.uuid4().hex[:8]}",
            mission_id=args.mission_id,
            record_type=args.record_type,
            content=content,
            robot_id=args.robot_id,
            subtask_id=args.subtask_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.append(record)
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.memory_command == "summary":
        result = store.summary(mission_id=args.mission_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Error: unknown memory subcommand: {args.memory_command}", file=sys.stderr)
    return 1


def _handle_approval(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    store = JsonlApprovalStore(args.approval_path)

    if args.approval_command == "list":
        requests = store.list_requests(mission_id=args.mission_id, status=args.status)
        print(json.dumps([r.to_dict() for r in requests], ensure_ascii=False, indent=2))
        return 0

    if args.approval_command == "request":
        request = store.create(
            mission_id=args.mission_id,
            action=args.action,
            risk_level=args.risk_level,
            command=args.command,
            requested_by="mission_cli",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _print_json(request.to_dict())
        return 0

    if args.approval_command == "decide":
        now = datetime.now(timezone.utc).isoformat()
        if args.decision == "approve":
            result = store.approve(args.request_id, decided_by="mission_cli", decided_at=now)
        else:
            result = store.deny(args.request_id, decided_by="mission_cli", reason=args.reason, decided_at=now)
        if result is None:
            print(f"Error: request {args.request_id} not found or already decided", file=sys.stderr)
            return 1
        _print_json(result.to_dict())
        return 0

    print(f"Error: unknown approval subcommand: {args.approval_command}", file=sys.stderr)
    return 1


def _handle_deploy(args: argparse.Namespace) -> int:
    from fireclaw_core.deployment import (
        DeploymentError,
        SystemdServiceError,
        apply_deployment,
        build_deployment_plan,
        inspect_deployment_status,
    )
    from fireclaw_core.infra.user_setup import resolve_active_profile_path

    try:
        args.profile = resolve_active_profile_path(args.profile)
        if args.deploy_command == "service":
            return _handle_deploy_service(args)
        if args.deploy_command == "plan":
            result = build_deployment_plan(
                args.profile,
                output_root=args.output_root,
            ).to_dict()
            _print_json(result)
            return 0
        if args.deploy_command == "apply":
            plan = build_deployment_plan(
                args.profile,
                output_root=args.output_root,
            )
            _print_json(apply_deployment(plan))
            return 0
        if args.deploy_command == "status":
            result = inspect_deployment_status(
                args.profile,
                output_root=args.output_root,
                check_runtime=not args.no_runtime_check,
            )
            _print_json(result)
            if result.get("status") in {"ready", "installed"}:
                return 0
            if result.get("status") == "installed_not_ready":
                return 1
            return 2
        if args.deploy_command == "run":
            from fireclaw_core.deployment.supervisor import (
                RuntimeSupervisorSettings,
                run_runtime_supervisor,
            )

            settings = RuntimeSupervisorSettings(
                runtime_ready_timeout_seconds=args.runtime_ready_timeout,
                gateway_ready_timeout_seconds=args.gateway_ready_timeout,
                mission_gateway_ready_timeout_seconds=(
                    args.mission_gateway_ready_timeout
                ),
                readiness_monitor_interval_seconds=(
                    args.readiness_monitor_interval
                ),
                readiness_failure_limit=args.readiness_failure_limit,
                probe_interval_seconds=args.probe_interval,
                monitor_interval_seconds=args.monitor_interval,
                shutdown_timeout_seconds=args.shutdown_timeout,
                terminate_timeout_seconds=args.terminate_timeout,
                simulation_restart_limit=args.simulation_restarts,
                restart_backoff_seconds=args.restart_backoff,
            )
            result = run_runtime_supervisor(
                args.profile,
                output_root=args.output_root,
                settings=settings,
            )
            _print_json(result)
            # systemd units use 78 as a non-restartable policy failure after
            # the supervisor has exhausted its own bounded recovery budget.
            return 0 if result.get("status") == "stopped" else 78
    except (DeploymentError, SystemdServiceError, OSError, ValueError) as exc:
        _print_json(
            {
                "status": "error",
                "code": getattr(exc, "code", "deployment_invalid"),
                "message": str(exc),
                "operator_action": getattr(
                    exc,
                    "operator_action",
                    "检查部署 Profile 和依赖后重试。",
                ),
            }
        )
        return 2
    raise ValueError(f"Unknown deploy command: {args.deploy_command}")


def _handle_deploy_service(args: argparse.Namespace) -> int:
    from fireclaw_core.deployment import (
        control_systemd_user_service,
        generated_systemd_service,
        inspect_systemd_user_service,
        install_systemd_user_service,
        uninstall_systemd_user_service,
    )

    if args.service_command == "render":
        _print_json(
            generated_systemd_service(
                args.profile,
                output_root=args.output_root,
            ).to_dict()
        )
        return 0
    if args.service_command == "install":
        _print_json(
            install_systemd_user_service(
                args.profile,
                output_root=args.output_root,
                enable=not args.no_enable,
                start=not args.no_start,
            )
        )
        return 0
    if args.service_command == "status":
        result = inspect_systemd_user_service(
            args.profile,
            output_root=args.output_root,
        )
        _print_json(result)
        return 0 if result.get("status") == "active" else 1
    if args.service_command in {"start", "stop", "restart"}:
        _print_json(
            control_systemd_user_service(
                args.profile,
                args.service_command,
                output_root=args.output_root,
            )
        )
        return 0
    if args.service_command == "uninstall":
        _print_json(
            uninstall_systemd_user_service(
                args.profile,
                output_root=args.output_root,
            )
        )
        return 0
    raise ValueError(f"Unknown service command: {args.service_command}")


def _handle_robot_profile(args: argparse.Namespace) -> int:
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    if args.robot_profile_command == "export":
        profile = load_robot_capability_profile(args.profile)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = {"robots": [profile.to_robot_registry_entry()]}
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _print_json({"status": "written", "output": str(output), "robot_id": profile.robot_id})
        return 0
    if args.robot_profile_command == "discover":
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        if not args.write_profile and not args.output:
            print("Error: --output is required unless --write-profile is given.", file=sys.stderr)
            return 1

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()

        def _toml_escape(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        def _fingerprint_lines() -> list[str]:
            if report.runtime_fingerprint is None:
                return []
            fingerprint = report.runtime_fingerprint
            lines = [
                "[robot.discovery_fingerprint]",
                f'source = "{_toml_escape(fingerprint.source)}"',
                f'topics_hash = "{_toml_escape(fingerprint.topics_hash)}"',
            ]
            if fingerprint.nodes_hash is not None:
                lines.append(f'nodes_hash = "{_toml_escape(fingerprint.nodes_hash)}"')
            lines.append("")
            return lines

        def _replace_table_block(existing: str, table_header: str, replacement_lines: list[str]) -> str:
            if table_header not in existing:
                return existing.rstrip() + "\n\n" + "\n".join(replacement_lines).rstrip() + "\n"
            lines = existing.splitlines()
            result: list[str] = []
            index = 0
            while index < len(lines):
                if lines[index].strip() == table_header:
                    result.extend(replacement_lines)
                    index += 1
                    while index < len(lines):
                        stripped = lines[index].strip()
                        if stripped.startswith("[") and stripped.endswith("]"):
                            break
                        index += 1
                    continue
                result.append(lines[index])
                index += 1
            return "\n".join(result).rstrip() + "\n"

        fingerprint_lines = _fingerprint_lines()
        header_lines: list[str] = [
            "# Suggested FireClaw sensor discovery rules.",
            "# Review before copying into the robot profile.",
            "",
        ]
        header_lines.extend(fingerprint_lines)
        header_lines.extend([
            "[robot.sensor_discovery]",
            "enabled = true",
            f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
            "",
        ])
        rule_lines: list[str] = []
        for finding in report.findings:
            if finding.status not in {"verified", "degraded"}:
                continue
            rule_lines.extend([
                "[[robot.sensor_discovery.rules]]",
                f'topic_pattern = "{_toml_escape(finding.topic)}"',
                f'message_type = "{_toml_escape(finding.message_type)}"',
                f'sensor = "{_toml_escape(finding.sensor)}"',
                f"confidence = {finding.confidence:.2f}",
                "confirmed = false",
                "",
            ])
        text = "\n".join(header_lines + rule_lines)
        output = Path(args.profile if args.write_profile else args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            updated = existing
            if fingerprint_lines:
                updated = _replace_table_block(updated, "[robot.discovery_fingerprint]", fingerprint_lines)
            if "[robot.sensor_discovery]" in updated:
                addition = "\n".join(rule_lines)
            else:
                addition = "\n".join([
                    "[robot.sensor_discovery]",
                    "enabled = true",
                    f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
                    "",
                    *rule_lines,
                ])
            output.write_text(updated.rstrip() + "\n\n" + addition.rstrip() + "\n", encoding="utf-8")
        else:
            output.write_text(text, encoding="utf-8")
        _print_json({
            "status": "written",
            "output": str(output),
            "verified_sensors": report.verified_sensors(),
        })
        return 0
    if args.robot_profile_command == "diff-discovery":
        from fireclaw_core.agent.profile_discovery import build_discovery_diff
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        payload = diff.to_dict()
        payload["verified_sensors"] = report.verified_sensors()
        payload["findings"] = [finding.to_dict() for finding in report.findings]
        _print_json(payload)
        return 0
    if args.robot_profile_command == "confirm-discovery":
        from datetime import datetime, timezone

        from fireclaw_core.agent.profile_discovery import (
            build_discovery_diff,
            render_confirmed_discovery_blocks,
            replace_robot_table_block,
        )
        from fireclaw_core.ros.ros1_sensor_discovery import (
            Ros1CliGraphProvider,
            Ros1CliMessageProbe,
            Ros1SensorDiscovery,
        )

        profile = load_robot_capability_profile(args.profile)
        discovery = Ros1SensorDiscovery(
            graph_provider=Ros1CliGraphProvider(),
            message_probe=Ros1CliMessageProbe(),
            extra_rules=profile.sensor_discovery.rules,
            timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            profile_fingerprint=profile.discovery_fingerprint,
        )
        report = discovery.discover()
        if report.runtime_fingerprint is None:
            print("Error: runtime fingerprint unavailable; refusing to confirm discovery.", file=sys.stderr)
            return 1
        if not report.verified_sensors():
            print("Error: no verified sensors discovered; refusing to confirm discovery.", file=sys.stderr)
            return 1
        confirmed_at = args.confirmed_at or datetime.now(timezone.utc).isoformat()
        block_text = render_confirmed_discovery_blocks(
            report=report,
            message_timeout_seconds=profile.sensor_discovery.message_timeout_seconds,
            confirmed_by=args.confirmed_by,
            confirmed_at=confirmed_at,
        )
        replacement_lines = block_text.splitlines()
        profile_path = Path(args.profile)
        existing = profile_path.read_text(encoding="utf-8")
        updated = replace_robot_table_block(existing, "[robot.discovery_fingerprint]", [])
        updated = replace_robot_table_block(updated, "[robot.sensor_discovery]", replacement_lines)
        profile_path.write_text(updated, encoding="utf-8")
        diff = build_discovery_diff(report, profile.sensor_discovery.rules)
        _print_json({
            "status": "confirmed",
            "profile": str(profile_path),
            "confirmed_by": args.confirmed_by,
            "confirmed_at": confirmed_at,
            "verified_sensors": report.verified_sensors(),
            "diff": diff.to_dict(),
        })
        return 0
    print(f"Error: unknown robot-profile subcommand: {args.robot_profile_command}", file=sys.stderr)
    return 1


def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", default=None, help="Path to robot registry JSON. Required when --robot-profile is not provided.")
    parser.add_argument("--robot-profile", action="append", default=None, help="Path to robot profile TOML. Repeat for multiple robots.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")
    parser.add_argument("--operator-id", default="mission-agent", help="Operator ID for authorization.")
    parser.add_argument("--role", default="operator", help="Operator role (observer, operator, supervisor, admin).")
    parser.add_argument("--scopes", nargs="*", default=None, help="Explicit operator scopes (overrides role defaults).")


def _add_runtime_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--memory-path", default=None, help="Path to mission memory JSONL.")
    parser.add_argument("--memory-index", default=None, help="Path to SQLite memory index.")
    _add_memory_rag_options(parser)
    _add_external_knowledge_rag_options(parser)
    parser.add_argument(
        "--embodied-runtime-mode",
        choices=("real", "simulation", "replay"),
        default=None,
        help="Enable policy-checked embodied memory in the explicit runtime domain.",
    )
    parser.add_argument("--task-registry", default=None, help="Path to task registry JSONL.")
    parser.add_argument(
        "--subagent-registry",
        default=None,
        help="Path to the legacy-named Robot Agent run registry JSONL.",
    )
    parser.add_argument("--session-lineage", default=None, help="Path to session lineage JSONL.")
    parser.add_argument("--task-flow", default=None, help="Path to task-flow registry JSONL.")
    parser.add_argument("--approval-path", default=None, help="Path to approval store JSONL.")
    parser.add_argument("--mission-planning-audit-path", default=None, help="Path to mission planning audit JSONL.")


def _add_memory_rag_options(
    parser: argparse.ArgumentParser,
    *,
    optional_defaults: bool = False,
) -> None:
    parser.add_argument(
        "--memory-rag-backend",
        choices=("bm25", "dense", "hybrid", "hybrid_rerank"),
        default=None,
        help="Use an existing RAG backend for mission-memory candidate retrieval.",
    )
    parser.add_argument("--memory-rag-bm25-index-dir", default=None)
    parser.add_argument("--memory-rag-dense-index-dir", default=None)
    parser.add_argument(
        "--memory-rag-generation-root",
        default=None,
        help="Managed generation root for automatic validated index refresh.",
    )
    parser.add_argument(
        "--memory-rag-embedding-provider",
        choices=("fake", "bge-m3"),
        default=None,
    )
    parser.add_argument("--memory-rag-embedding-model-path", default=None)
    parser.add_argument(
        "--memory-rag-reranker-provider",
        choices=("fake", "bge-reranker"),
        default=None,
    )
    parser.add_argument("--memory-rag-reranker-model-path", default=None)
    parser.add_argument("--memory-rag-device", default=None)
    parser.add_argument(
        "--memory-rag-candidate-multiplier",
        type=int,
        default=None if optional_defaults else 3,
    )
    parser.add_argument(
        "--memory-rag-rrf-k",
        type=int,
        default=None if optional_defaults else 60,
    )


def _add_external_knowledge_rag_options(
    parser: argparse.ArgumentParser,
    *,
    optional_defaults: bool = False,
) -> None:
    parser.add_argument(
        "--knowledge-rag-backend",
        choices=("bm25", "dense", "hybrid", "hybrid_rerank"),
        default=None,
        help="Ground mission planning with an external firefighting knowledge index.",
    )
    parser.add_argument("--knowledge-rag-bm25-index-dir", default=None)
    parser.add_argument("--knowledge-rag-dense-index-dir", default=None)
    parser.add_argument(
        "--knowledge-rag-generation-root",
        default=None,
        help="Prebuilt managed generation root for hot-reloading external knowledge.",
    )
    parser.add_argument(
        "--knowledge-rag-embedding-provider",
        choices=("fake", "bge-m3"),
        default=None,
    )
    parser.add_argument("--knowledge-rag-embedding-model-path", default=None)
    parser.add_argument(
        "--knowledge-rag-reranker-provider",
        choices=("fake", "bge-reranker"),
        default=None,
    )
    parser.add_argument("--knowledge-rag-reranker-model-path", default=None)
    parser.add_argument("--knowledge-rag-device", default=None)
    parser.add_argument(
        "--knowledge-rag-candidate-multiplier",
        type=int,
        default=None if optional_defaults else 3,
    )
    parser.add_argument(
        "--knowledge-rag-rrf-k",
        type=int,
        default=None if optional_defaults else 60,
    )


def _build_mission_runtime_paths(args: argparse.Namespace) -> MissionRuntimePaths:
    robot_profiles = tuple(Path(p) for p in (getattr(args, "robot_profile", None) or ()))
    robot_registry = getattr(args, "robot_registry", None)
    if robot_registry is None:
        if not robot_profiles:
            raise SystemExit("--robot-registry is required when --robot-profile is not provided")
        robot_registry_path = Path("robots.json")
    else:
        robot_registry_path = Path(robot_registry)
    return MissionRuntimePaths(
        robot_registry=robot_registry_path,
        mission_registry=args.mission_registry,
        mission_memory=getattr(args, "memory_path", None),
        memory_index=getattr(args, "memory_index", None),
        embodied_runtime_mode=getattr(args, "embodied_runtime_mode", None),
        task_registry=getattr(args, "task_registry", None),
        subagent_registry=getattr(args, "subagent_registry", None),
        session_lineage=getattr(args, "session_lineage", None),
        task_flow=getattr(args, "task_flow", None),
        approvals=getattr(args, "approval_path", None),
        robot_profiles=robot_profiles,
        mission_planning_audit=Path(p) if (p := getattr(args, "mission_planning_audit_path", None)) else None,
        memory_rag=_build_memory_rag_config(args),
        external_knowledge_rag=_build_external_knowledge_rag_config(args),
    )


def _build_memory_rag_config(args: argparse.Namespace) -> RagRuntimeConfig | None:
    backend = getattr(args, "memory_rag_backend", None)
    if backend is None:
        return None
    return RagRuntimeConfig(
        backend=backend,
        source_kind="mission_memory",
        bm25_index_dir=(
            Path(value)
            if (value := getattr(args, "memory_rag_bm25_index_dir", None))
            else None
        ),
        dense_index_dir=(
            Path(value)
            if (value := getattr(args, "memory_rag_dense_index_dir", None))
            else None
        ),
        generation_root=(
            Path(value)
            if (value := getattr(args, "memory_rag_generation_root", None))
            else None
        ),
        embedding_provider=getattr(args, "memory_rag_embedding_provider", None),
        embedding_model_path=(
            Path(value)
            if (value := getattr(args, "memory_rag_embedding_model_path", None))
            else None
        ),
        reranker_provider=getattr(args, "memory_rag_reranker_provider", None),
        reranker_model_path=(
            Path(value)
            if (value := getattr(args, "memory_rag_reranker_model_path", None))
            else None
        ),
        device=getattr(args, "memory_rag_device", None),
        candidate_multiplier=(
            getattr(args, "memory_rag_candidate_multiplier", None) or 3
        ),
        rrf_k=getattr(args, "memory_rag_rrf_k", None) or 60,
    )


def _build_external_knowledge_rag_config(
    args: argparse.Namespace,
) -> RagRuntimeConfig | None:
    backend = getattr(args, "knowledge_rag_backend", None)
    if backend is None:
        return None
    return RagRuntimeConfig(
        backend=backend,
        source_kind="external_knowledge",
        bm25_index_dir=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_bm25_index_dir", None))
            else None
        ),
        dense_index_dir=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_dense_index_dir", None))
            else None
        ),
        generation_root=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_generation_root", None))
            else None
        ),
        embedding_provider=getattr(args, "knowledge_rag_embedding_provider", None),
        embedding_model_path=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_embedding_model_path", None))
            else None
        ),
        reranker_provider=getattr(args, "knowledge_rag_reranker_provider", None),
        reranker_model_path=(
            Path(value)
            if (value := getattr(args, "knowledge_rag_reranker_model_path", None))
            else None
        ),
        device=getattr(args, "knowledge_rag_device", None),
        candidate_multiplier=(
            getattr(args, "knowledge_rag_candidate_multiplier", None) or 3
        ),
        rrf_k=getattr(args, "knowledge_rag_rrf_k", None) or 60,
    )


def _build_mission_agent(args: argparse.Namespace, *, planner: Any = None) -> MissionAgent:
    paths = _build_mission_runtime_paths(args)
    return build_mission_agent_from_paths(
        paths,
        operator_id=args.operator_id,
        role=args.role,
        scopes=args.scopes,
        planner=planner,
        source="mission_cli",
    )


def _build_planner(args: argparse.Namespace) -> Any:
    """Build the appropriate planner based on CLI flags."""
    try:
        return _build_planner_shared(
            planner_type=args.planner,
            provider_base_url=args.provider_base_url,
            provider_api_key=args.provider_api_key,
            model=args.model,
            llm_trace_path=args.llm_trace_path,
            model_catalog_path=getattr(args, "catalog", None),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _mission_operator() -> dict[str, Any]:
    return {
        "operator_id": "mission-agent",
        "role": "operator",
        "control_scopes": [
            "task.submit",
            "task.cancel",
            "mission.cancel",
            "state.read",
        ],
        "source": "mission_cli",
    }


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
