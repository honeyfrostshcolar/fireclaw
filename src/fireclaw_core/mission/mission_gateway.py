from __future__ import annotations

import json
import hashlib
import hmac
import logging
from pathlib import Path
import re
from queue import Empty as QueueEmpty, Full as QueueFull, Queue
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4
from fireclaw_core.approval.approval_relay import ApprovalRelay
from fireclaw_core.approval.approval_runtime import ApprovalRuntime
from fireclaw_core.gateway.auth import (
    AuthenticatedGatewayPrincipal,
    authenticate_gateway_request,
    validate_gateway_bind,
)
from fireclaw_core.infra import tomllib_compat as tomllib
from fireclaw_core.gateway.method_scopes import authorize_method
from fireclaw_core.gateway.network_security import (
    GatewayNetworkPolicy,
    GatewayRequestBodyError,
    GatewayRequestGuard,
    read_json_object_body,
)
from fireclaw_core.gateway.transport import (
    GatewayTlsServerConfig,
    create_gateway_http_server,
    gateway_scheme,
)

from fireclaw_core.monitoring.stream_events import EventBus, StreamEvent, TelemetryTracker

from fireclaw_core.gateway.control import OperatorContext
from fireclaw_core.devtools.fleet_doctor import FleetDoctor
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_plan_validator import MissionPlanValidator
from fireclaw_core.mission.planning_dialogue import (
    DEFAULT_MAX_CLARIFICATION_ROUNDS,
    DEFAULT_PLANNING_DIALOGUE_TTL_SECONDS,
    PlanningDialogue,
    PlanningDialogueError,
    PlanningDialogueStore,
)
from fireclaw_core.mission.plan_artifact import (
    DEFAULT_PLAN_TTL_SECONDS,
    PlanArtifactError,
    PlanArtifactRecord,
    PlanArtifactStore,
    assess_plan_risk,
    bind_relative_target_yaws,
    build_readiness_binding,
    require_dispatchable_readiness,
    required_plan_approvals,
    validate_plan_2d,
    validate_plan_target_binding,
)
from fireclaw_core.mission.runtime_identity import (
    GatewayRuntimeIdentity,
    evidence_envelope,
    utc_now_iso,
)
from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.infra.operator_readiness import build_operator_doctor_snapshot
from fireclaw_core.infra.session_lineage import JsonlSessionLineageStore, validate_resume_ownership
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
from fireclaw_core.mission.mission_run import MissionRunManager
from fireclaw_core.mission.task_graph import task_graph_from_mission_plan
from fireclaw_core.memory.reconciliation import (
    EmbodiedMemoryReconciler,
    ReplicationBatch,
)

logger = logging.getLogger(__name__)

STATIC_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}


MISSION_TRACE_RE = re.compile(r"^/missions/([^/]+)/trace$")
MISSION_EVENTS_RE = re.compile(r"^/missions/([^/]+)/events$")
MISSION_RUN_RE = re.compile(r"^/missions/([^/]+)/run$")
MISSION_REPORT_RE = re.compile(r"^/missions/([^/]+)/report$")
MISSION_CANCEL_RE = re.compile(r"^/missions/([^/]+)/cancel$")
MISSION_PAUSE_RE = re.compile(r"^/missions/([^/]+)/pause$")
MISSION_RESUME_RE = re.compile(r"^/missions/([^/]+)/resume$")
MISSION_CORRECTIONS_RE = re.compile(r"^/missions/([^/]+)/corrections?$")
MISSION_APPROVALS_RE = re.compile(r"^/missions/([^/]+)/approvals$")
MISSION_EVENTS_STREAM_RE = re.compile(r"^/missions/([^/]+)/events/stream$")
TASK_TRACE_RE = re.compile(r"^/tasks/([^/]+)/trace$")
TASK_EVENTS_RE = re.compile(r"^/tasks/([^/]+)/events$")
TASK_RUN_RE = re.compile(r"^/tasks/([^/]+)/run$")

_MISSION_STREAM_TERMINAL_EVENTS = frozenset({
    "mission.completed",
    "mission.blocked",
    "mission.escalated",
    "mission.failed",
    "mission.timed_out",
    "mission.cancelled",
    "mission.lost",
})
_MISSION_STREAM_REPORT_EVENTS = frozenset({
    "mission.report_ready",
    "mission.final_report_ready",
    *_MISSION_STREAM_TERMINAL_EVENTS,
})
TASK_REPORT_RE = re.compile(r"^/tasks/([^/]+)/report$")
TASK_CANCEL_RE = re.compile(r"^/tasks/([^/]+)/cancel$")
TASK_PAUSE_RE = re.compile(r"^/tasks/([^/]+)/pause$")
TASK_RESUME_RE = re.compile(r"^/tasks/([^/]+)/resume$")
MISSION_MEMORY_SYNC_RE = re.compile(r"^/missions/([^/]+)/memory/sync$")
MISSION_MEMORY_TOOLS_RE = re.compile(r"^/missions/([^/]+)/memory/tools$")
MISSION_MEMORY_TOOL_CALL_RE = re.compile(r"^/missions/([^/]+)/memory/tools/call$")
MISSION_MEMORY_LIFECYCLE_RE = re.compile(r"^/missions/([^/]+)/memory/lifecycle$")
MISSION_MEMORY_AUDIT_RE = re.compile(r"^/missions/([^/]+)/memory/audit$")
MISSION_MEMORY_ARCHIVE_RE = re.compile(r"^/missions/([^/]+)/memory/archive$")
MISSION_MEMORY_DELETE_RE = re.compile(r"^/missions/([^/]+)/memory/delete$")
MEMORY_KNOWLEDGE_REVOKE_RE = re.compile(r"^/memory/knowledge/([^/]+)/revoke$")


@dataclass(frozen=True)
class MissionGatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8766
    api_token: str | None = None
    tls: GatewayTlsServerConfig = field(default_factory=GatewayTlsServerConfig)
    network: GatewayNetworkPolicy = field(default_factory=GatewayNetworkPolicy)
    planning_dialogue_max_rounds: int = DEFAULT_MAX_CLARIFICATION_ROUNDS
    planning_dialogue_ttl_seconds: float = DEFAULT_PLANNING_DIALOGUE_TTL_SECONDS


class MissionGateway:
    def __init__(
        self,
        config: MissionGatewayConfig,
        *,
        mission_agent: MissionAgent,
        registry: RobotRegistry,
        subagent_client: RobotSubagentClient | None = None,
        approval_runtime: ApprovalRuntime | None = None,
        approval_relay: ApprovalRelay | None = None,
        plugin_runtime: Any | None = None,
        task_registry: JsonlTaskRegistryStore | None = None,
        subagent_registry: JsonlSubagentRegistry | None = None,
        session_lineage_store: JsonlSessionLineageStore | None = None,
        memory_reconciler: EmbodiedMemoryReconciler | None = None,
        replication_security: Any | None = None,
        mission_run_manager: MissionRunManager | None = None,
        runtime_identity: GatewayRuntimeIdentity | None = None,
        plan_artifact_store: PlanArtifactStore | None = None,
        planning_dialogue_store: PlanningDialogueStore | None = None,
        plan_readiness_provider: Callable[[], Mapping[str, Any]] | None = None,
        plan_artifact_ttl_seconds: float = DEFAULT_PLAN_TTL_SECONDS,
        runtime_epoch: str | None = None,
    ) -> None:
        self.process_working_directory = Path.cwd().resolve(strict=False)
        self.config = config
        self._network_guard = GatewayRequestGuard(
            config.network,
            configured_host=config.host,
            tls_enabled=config.tls.enabled,
        )
        self.mission_agent = mission_agent
        self.registry = registry
        self.subagent_client = subagent_client or RobotSubagentClient()
        self.approval_runtime = approval_runtime
        self.approval_relay = approval_relay
        self.plugin_runtime = plugin_runtime
        self.task_registry = task_registry
        self.subagent_registry = subagent_registry
        self._session_lineage_store = session_lineage_store
        self.memory_reconciler = memory_reconciler
        self.replication_security = replication_security
        self.runtime_identity = runtime_identity or GatewayRuntimeIdentity.unknown()
        self.plan_artifact_store = plan_artifact_store or PlanArtifactStore()
        self.planning_dialogue_store = (
            planning_dialogue_store
            or PlanningDialogueStore(
                ttl_seconds=config.planning_dialogue_ttl_seconds,
                max_rounds=config.planning_dialogue_max_rounds,
            )
        )
        self._plan_readiness_provider = plan_readiness_provider
        self.plan_artifact_ttl_seconds = float(plan_artifact_ttl_seconds)
        self.runtime_epoch = runtime_epoch or uuid4().hex
        self._approval_relays: dict[str, dict[str, Any]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_bus = EventBus()
        self._telemetry = TelemetryTracker()
        self.mission_run_manager = mission_run_manager or MissionRunManager(
            mission_agent,
            event_sink=self._publish_run_event,
        )

    @property
    def base_url(self) -> str:
        scheme = gateway_scheme(self.config.tls)
        if self._server is not None:
            host, port = self._server.server_address
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{self.config.host}:{self.config.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        validate_gateway_bind(self.config.host, self.config.api_token)
        # Recover and start consolidation coordinator before accepting requests
        coordinator = self.mission_agent.consolidation_coordinator
        if coordinator is not None:
            try:
                coordinator.recover()
                coordinator.start()
            except Exception:
                logger.warning("Failed to start consolidation coordinator", exc_info=True)
        handler_class = self._handler_class()
        self._server = create_gateway_http_server(
            (self.config.host, self.config.port),
            handler_class,
            tls=self.config.tls,
            network_policy=self.config.network,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        validate_gateway_bind(self.config.host, self.config.api_token)
        coordinator = self.mission_agent.consolidation_coordinator
        if coordinator is not None:
            try:
                coordinator.recover()
                coordinator.start()
            except Exception:
                logger.warning("Failed to start consolidation coordinator", exc_info=True)
        handler_class = self._handler_class()
        self._server = create_gateway_http_server(
            (self.config.host, self.config.port),
            handler_class,
            tls=self.config.tls,
            network_policy=self.config.network,
        )
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is None:
            return
        self.mission_run_manager.shutdown(wait=False)
        # Stop consolidation coordinator after stopping request intake
        coordinator = self.mission_agent.consolidation_coordinator
        if coordinator is not None:
            try:
                coordinator.stop()
            except Exception:
                logger.warning("Failed to stop consolidation coordinator", exc_info=True)
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    # ------------------------------------------------------------------
    # Endpoint logic
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Report that the assembled Mission Gateway is accepting requests.

        Fleet and robot readiness intentionally remain on ``/fleet/doctor``;
        this endpoint proves only that the control-plane process completed
        startup and is serving the expected API.
        """

        return {
            "schema_version": 1,
            "status": "ok",
            "service": "mission_gateway",
        }

    def readiness(self) -> dict[str, Any]:
        """Report evidence-backed readiness plus compatibility projections."""
        observed_at = utc_now_iso()
        fleet_doctor = self.fleet_doctor()
        doctor_snapshot = build_operator_doctor_snapshot(report=fleet_doctor)
        fleet_state = self.fleet_state()
        identity_value = self.runtime_identity.value_dict()
        phase = doctor_snapshot.get("phase", "unknown")
        safe_state = doctor_snapshot.get("safe_state", "unknown")
        reason_code = doctor_snapshot.get("reason_code", "unknown")
        summary = doctor_snapshot.get(
            "summary",
            "Gateway did not provide an admission projection.",
        )
        observations = {
            "admission_phase": evidence_envelope(
                phase,
                source="fleet_doctor_operator_projection",
                observed_at=observed_at,
                freshness="fresh",
            ),
            "admission_safe_state": evidence_envelope(
                safe_state,
                source="fleet_doctor_operator_projection",
                observed_at=observed_at,
                freshness="fresh",
            ),
            "reason_code": evidence_envelope(
                reason_code,
                source="fleet_doctor_operator_projection",
                observed_at=observed_at,
                freshness="fresh",
            ),
            "summary": evidence_envelope(
                summary,
                source="fleet_doctor_operator_projection",
                observed_at=observed_at,
                freshness="fresh",
            ),
            "physical_stop_confirmed": evidence_envelope(
                None,
                source="physical_stop_evidence_unavailable",
                observed_at=observed_at,
                freshness="unknown",
            ),
            "emergency_stop_state": evidence_envelope(
                None,
                source="emergency_stop_evidence_unavailable",
                observed_at=observed_at,
                freshness="unknown",
            ),
        }
        return {
            "status": "ok",
            "schema_version": 2,
            "kind": "readiness_snapshot",
            "runtime_identity": self.runtime_identity.to_evidence(),
            "observations": observations,
            "robot_readiness": self._robot_readiness_evidence(
                fleet_doctor,
                observed_at=observed_at,
            ),
            # Compatibility projections remain derived from the same evidence.
            # New operator surfaces must consume the envelopes above.
            "active_robot_id": identity_value["robot_id"],
            "runtime_mode": identity_value["runtime_mode"],
            "active_profile_path": identity_value["active_profile_path"],
            "profile_sha256": identity_value["profile_sha256"],
            "profile_revision": identity_value["profile_revision"],
            "deployment_fingerprint": identity_value["deployment_fingerprint"],
            "phase": phase,
            "safe_state": safe_state,
            "reason_code": reason_code,
            "summary": summary,
            "fleet_state": fleet_state,
            "fleet_doctor": fleet_doctor,
            "doctor_snapshot": doctor_snapshot,
        }

    def _robot_readiness_evidence(
        self,
        fleet_doctor: dict[str, Any],
        *,
        observed_at: str,
    ) -> list[dict[str, Any]]:
        reachability: dict[str, dict[str, Any]] = {}
        findings = fleet_doctor.get("findings")
        if isinstance(findings, list):
            for finding in findings:
                if not isinstance(finding, dict) or finding.get("category") != "reachability":
                    continue
                robot_id = finding.get("robot_id")
                details = finding.get("details")
                if isinstance(robot_id, str) and isinstance(details, dict):
                    reachability[robot_id] = details

        result: list[dict[str, Any]] = []
        for entry in self.registry.list_entries():
            details = reachability.get(entry.robot_id)
            if not entry.enabled:
                value = {
                    "status": "disabled",
                    "last_seen_at": self.registry.get_last_seen_at(entry.robot_id),
                    "declared_capabilities": list(entry.capabilities),
                }
                source = "robot_registry"
                freshness = "fresh"
                evidence_observed_at = observed_at
            elif details is not None:
                value = {
                    "status": "online" if details.get("online") is True else "offline",
                    "last_seen_at": details.get("last_seen_at"),
                    "declared_capabilities": list(entry.capabilities),
                    "state": details.get("state") or {},
                }
                source = "robot_gateway_state_probe"
                freshness = "fresh"
                evidence_observed_at = str(details.get("checked_at") or observed_at)
            elif self.registry.is_stale(entry.robot_id):
                value = {
                    "status": "stale",
                    "last_seen_at": self.registry.get_last_seen_at(entry.robot_id),
                    "declared_capabilities": list(entry.capabilities),
                }
                source = "robot_registry_heartbeat"
                freshness = "stale"
                evidence_observed_at = str(
                    self.registry.get_last_seen_at(entry.robot_id) or observed_at
                )
            else:
                value = {
                    "status": "unknown",
                    "last_seen_at": self.registry.get_last_seen_at(entry.robot_id),
                    "declared_capabilities": list(entry.capabilities),
                }
                source = "robot_readiness_unavailable"
                freshness = "unknown"
                evidence_observed_at = observed_at
            result.append(
                {
                    "robot_id": entry.robot_id,
                    "readiness": evidence_envelope(
                        value,
                        source=source,
                        observed_at=evidence_observed_at,
                        freshness=freshness,
                    ),
                }
            )
        return result

    def plan_mission(
        self,
        task_description: str,
        *,
        target_robot: str | None = None,
        operator: OperatorContext | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Start bounded Mission Agent planning without dispatching a robot."""

        if not isinstance(task_description, str) or not task_description.strip():
            raise PlanArtifactError(
                "plan_command_invalid",
                "Field 'task_description' or 'command' is required.",
            )
        requested_robot = (
            target_robot.strip()
            if isinstance(target_robot, str) and target_robot.strip()
            else None
        )
        try:
            dialogue = self.planning_dialogue_store.begin(
                operator_id=_operator_id(operator),
                command=task_description,
                target_robot=requested_robot,
            )
        except PlanningDialogueError as exc:
            raise _planning_dialogue_plan_error(exc) from exc
        return self._deliberate_planning_dialogue(
            dialogue,
            operator=operator,
            event_sink=event_sink,
        )

    def continue_plan_mission(
        self,
        planning_session_id: str,
        clarification_answer: str,
        *,
        operator: OperatorContext | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Bind one operator answer and resume bounded Mission Agent planning."""

        try:
            dialogue = self.planning_dialogue_store.answer(
                planning_session_id,
                operator_id=_operator_id(operator),
                answer=clarification_answer,
            )
        except PlanningDialogueError as exc:
            raise _planning_dialogue_plan_error(exc) from exc
        return self._deliberate_planning_dialogue(
            dialogue,
            operator=operator,
            event_sink=event_sink,
        )

    def _deliberate_planning_dialogue(
        self,
        dialogue: PlanningDialogue,
        *,
        operator: OperatorContext | None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run one planning phase and retain state only while awaiting input."""

        operator_id = _operator_id(operator)
        retain_dialogue = False
        stage_timings: list[dict[str, Any]] = []

        def forward_event(event_type: str, payload: dict[str, Any]) -> None:
            if event_type == "mission_agent.stage.completed":
                stage_timings.append(dict(payload))
            if event_sink is not None:
                try:
                    event_sink(event_type, payload)
                except Exception:
                    logger.warning(
                        "Mission planning progress sink failed",
                        exc_info=True,
                    )

        def emit_stage(
            stage: str,
            started: float,
            *,
            status: str = "completed",
            details: Mapping[str, Any] | None = None,
        ) -> None:
            payload: dict[str, Any] = {
                "stage": stage,
                "status": status,
                "duration_ms": round(
                    max(0.0, (time.monotonic() - started) * 1000),
                    2,
                ),
            }
            if details:
                payload.update(dict(details))
            forward_event("mission_agent.stage.completed", payload)

        def with_timing(response: Mapping[str, Any]) -> dict[str, Any]:
            return {
                **dict(response),
                "planning_timing": _planning_timing_payload(stage_timings),
            }

        try:
            identity = self._require_authoritative_runtime_identity()
            self._require_current_profile_revision(identity.profile_revision)
            readiness_started = time.monotonic()
            snapshot = self._current_plan_readiness()
            emit_stage(
                "readiness",
                readiness_started,
                details={
                    "ready_robot_count": len(
                        _fresh_online_robot_ids(snapshot)
                    ),
                },
            )
            ready_robot_ids = _fresh_online_robot_ids(snapshot)
            requested_robot = dialogue.target_robot
            if (
                requested_robot is not None
                and requested_robot not in ready_robot_ids
            ):
                raise PlanArtifactError(
                    "robot_not_ready",
                    f"Requested robot {requested_robot} lacks fresh online readiness evidence.",
                )
            available = [
                entry
                for entry in self.registry.enabled_entries()
                if entry.robot_id in ready_robot_ids
                and (
                    requested_robot is None
                    or entry.robot_id == requested_robot
                )
            ]
            if not available:
                raise PlanArtifactError(
                    "robot_not_ready",
                    "No enabled robot has fresh online readiness evidence.",
                )
            if self.mission_agent.mission_deliberation_runtime is None:
                raise PlanArtifactError(
                    "canonical_planner_unavailable",
                    "No canonical Mission deliberation runtime is configured.",
                )

            command = dialogue.canonical_operator_command()
            try:
                deliberation = self.mission_agent.deliberate_preview(
                    command,
                    mission_id=dialogue.mission_id,
                    presence=_presence_from_readiness_snapshot(
                        snapshot,
                        registry=self.registry,
                    ),
                    available_robot_ids={
                        entry.robot_id for entry in available
                    },
                    operator_clarifications=(
                        dialogue.clarification_context()
                    ),
                    operator_id=operator_id,
                    event_sink=forward_event,
                )
            except Exception as exc:
                logger.exception("Canonical mission preview planning failed")
                raise PlanArtifactError(
                    "canonical_planner_failed",
                    "Canonical Mission Agent failed; no plan artifact was created.",
                ) from exc

            planning_result = deliberation.planning_result
            if deliberation.status == "clarification_required":
                try:
                    pending = self.planning_dialogue_store.record_question(
                        dialogue.session_id,
                        operator_id=operator_id,
                        question=deliberation.message,
                        reason_code=deliberation.reason_code,
                    )
                except PlanningDialogueError as exc:
                    if exc.code == "clarification_round_limit":
                        return with_timing({
                            "status": "escalated",
                            "message": (
                                "已达到最多 "
                                f"{dialogue.max_rounds} 轮安全澄清，"
                                "仍无法形成可验证计划；任务没有下发。"
                            ),
                            "reason_code": exc.code,
                            "preview_created": False,
                        })
                    raise _planning_dialogue_plan_error(exc) from exc
                retain_dialogue = True
                response = pending.clarification_response()
                response["deliberation_attempts"] = [
                    attempt.to_dict() for attempt in deliberation.attempts
                ]
                response["deliberation_observations"] = [
                    obs.to_dict() for obs in deliberation.observations
                ]
                return with_timing(response)

            if deliberation.status != "proposed" or planning_result is None:
                message = deliberation.message
                validation_errors = tuple(
                    deliberation.validation_errors or ()
                )
                if validation_errors:
                    # Surface deterministic validator output so the operator can
                    # fix the command (e.g. restate coordinates as "(x, y)")
                    # instead of retrying blindly into the same failure.
                    shown = "；".join(str(e) for e in validation_errors[:3])
                    suffix = (
                        f"（确定性校验反馈：{shown}"
                        + (
                            f" 等共 {len(validation_errors)} 条"
                            if len(validation_errors) > 3
                            else ""
                        )
                        + "）"
                    )
                    message = f"{message}{suffix}"
                response: dict[str, Any] = {
                    "status": deliberation.status,
                    "message": message,
                    "reason_code": deliberation.reason_code,
                    "preview_created": False,
                }
                if validation_errors:
                    response["validation_errors"] = list(validation_errors)
                if planning_result is not None:
                    response["intent"] = planning_result.intent
                if deliberation.observation_request is not None:
                    response["message"] = (
                        "Mission Agent 需要额外现场观测；预览阶段没有自动"
                        "调度机器人执行观测。请先完成独立的观测授权流程。"
                    )
                    response["observation_request"] = (
                        deliberation.observation_request.to_dict()
                    )
                return with_timing(response)

            plan = planning_result.plan
            if plan is None:
                raise PlanArtifactError(
                    "canonical_plan_invalid",
                    "Mission deliberation proposed no executable plan.",
                )
            if plan.command != command:
                raise PlanArtifactError(
                    "canonical_plan_command_mismatch",
                    "Canonical Planner changed the authenticated dialogue binding.",
                )
            target_binding_state = _target_binding_state_from_readiness_snapshot(
                snapshot,
                registry=self.registry,
            )
            relative_binding_started = time.monotonic()
            bound_plan = bind_relative_target_yaws(
                plan,
                state_snapshot=target_binding_state,
            )
            bound_target_count = sum(
                old.target != new.target
                for old, new in zip(plan.subtasks, bound_plan.subtasks)
            )
            emit_stage(
                "relative_target_binding",
                relative_binding_started,
                details={"bound_target_count": bound_target_count},
            )
            plan = bound_plan
            robot_ids = sorted({
                subtask.robot_id for subtask in plan.subtasks
            })
            if requested_robot is not None and robot_ids != [requested_robot]:
                raise PlanArtifactError(
                    "canonical_plan_robot_mismatch",
                    "Canonical Planner changed the requested robot binding.",
                )
            preview_graph = task_graph_from_mission_plan(
                plan,
                mission_id=dialogue.mission_id,
                plan_id=f"sealed-preview:{dialogue.session_id}",
            )
            target_binding_errors = validate_plan_target_binding(
                plan,
                state_snapshot=target_binding_state,
            )
            if target_binding_errors:
                raise PlanArtifactError(
                    "canonical_plan_target_unbound",
                    "导航目标没有绑定到操作员明确给出的坐标或朝向；"
                    "请提供 map 坐标 (x, y)，系统不会猜测目标点。",
                )
            errors = [
                *validate_plan_2d(plan),
                *MissionPlanValidator().validate(
                    plan,
                    self.registry,
                    task_graph=preview_graph,
                ),
            ]
            if errors:
                raise PlanArtifactError(
                    "canonical_plan_invalid",
                    "Canonical Mission Plan failed deterministic validation: "
                    + "; ".join(errors),
                )
            sealing_started = time.monotonic()
            readiness_binding = build_readiness_binding(
                snapshot,
                robot_ids=robot_ids,
            )
            # Preview has no physical side effect. Bind the full admission
            # revision, but defer the fleet-wide dispatch gate until the
            # operator confirms the sealed artifact.
            require_dispatchable_readiness(
                readiness_binding,
                require_admission=False,
            )
            risk_level = assess_plan_risk(
                plan,
                runtime_mode=str(identity.runtime_mode),
            )
            approvals = required_plan_approvals(
                risk_level,
                runtime_mode=str(identity.runtime_mode),
            )
            issued = self.plan_artifact_store.issue(
                plan,
                operator_id=operator_id,
                session_id=dialogue.session_id,
                robot_ids=robot_ids,
                runtime_epoch=self.runtime_epoch,
                runtime_mode=str(identity.runtime_mode),
                profile_revision=str(identity.profile_revision),
                readiness_revision=readiness_binding.revision,
                risk_level=risk_level,
                required_approvals=approvals,
                ttl_seconds=self.plan_artifact_ttl_seconds,
            )
            artifact = issued.to_public_dict()
            steps = [
                {
                    "index": index,
                    "node_id": subtask.node_id or f"task-{index}",
                    "robot_id": subtask.robot_id,
                    "command": subtask.command,
                    "capability_required": subtask.capability_required,
                    "execution_group": subtask.execution_group,
                    "target": (
                        dict(subtask.target)
                        if isinstance(subtask.target, dict)
                        else None
                    ),
                    "risk_level": (
                        subtask.completion_contract.get("risk_level")
                        if isinstance(subtask.completion_contract, dict)
                        else None
                    ) or risk_level,
                }
                for index, subtask in enumerate(plan.subtasks, start=1)
            ]
            emit_stage(
                "plan_artifact_seal",
                sealing_started,
                details={
                    "subtask_count": len(plan.subtasks),
                    "risk_level": risk_level,
                },
            )
            return with_timing({
                "status": "preview_ready",
                "message": planning_result.message,
                "preview_created": True,
                "task_description": dialogue.original_command,
                "planning_session_id": dialogue.session_id,
                "clarification_rounds": len(dialogue.turns),
                "intent": plan.intent,
                "target_robot": (
                    robot_ids[0] if len(robot_ids) == 1 else None
                ),
                "robot_ids": robot_ids,
                "steps": steps,
                "risk_level": risk_level,
                "plan": plan.to_dict(),
                "plan_artifact": artifact,
                "artifact_id": issued.record.artifact_id,
                "plan_token": issued.token,
                "plan_digest": issued.record.plan_digest,
                "binding_digest": issued.record.binding_digest,
                "status_version": issued.record.status_version,
                "session_id": issued.record.session_id,
                "profile_revision": issued.record.profile_revision,
                "readiness_revision": issued.record.readiness_revision,
                "required_approvals": approvals,
                "expires_at": issued.record.expires_at,
                "deliberation_attempts": [
                    attempt.to_dict() for attempt in deliberation.attempts
                ],
                "deliberation_observations": [
                    obs.to_dict() for obs in deliberation.observations
                ],
            })
        finally:
            if not retain_dialogue:
                try:
                    self.planning_dialogue_store.finish(
                        dialogue.session_id,
                        operator_id=operator_id,
                    )
                except PlanningDialogueError:
                    logger.debug(
                        "Planning dialogue was already superseded or removed",
                        exc_info=True,
                    )

    def confirm_plan(
        self,
        payload: Mapping[str, Any],
        *,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        """Consume one artifact and queue exactly its stored MissionPlan."""

        allowed_fields = {
            "artifact_id",
            "plan_token",
            "plan_digest",
            "status_version",
            "session_id",
            "robot_ids",
            "operator_confirmed",
        }
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise PlanArtifactError(
                "plan_confirmation_payload_invalid",
                "Confirmation payload contains forbidden fields: "
                + ", ".join(unknown_fields),
            )
        if payload.get("operator_confirmed") is not True:
            raise PlanArtifactError(
                "plan_confirmation_required",
                "Explicit operator confirmation is required.",
            )
        artifact_id = _required_payload_string(payload, "artifact_id")
        token = _required_payload_string(payload, "plan_token")
        plan_digest = _required_payload_string(payload, "plan_digest")
        session_id = _required_payload_string(payload, "session_id")
        status_version = _required_payload_int(payload, "status_version")
        robot_ids = _required_payload_string_list(payload, "robot_ids")
        operator_id = _operator_id(operator)
        record = self.plan_artifact_store.validate_pending(
            token,
            artifact_id=artifact_id,
            plan_digest=plan_digest,
            status_version=status_version,
            operator_id=operator_id,
            session_id=session_id,
            robot_ids=robot_ids,
            runtime_epoch=self.runtime_epoch,
        )
        if record.runtime_mode == "real":
            self._invalidate_plan(record, reason="real_dispatch_not_authorized")
            raise PlanArtifactError(
                "real_dispatch_not_authorized",
                "Real-mode confirmation is disabled until the physical stop and recovery contract is complete.",
            )
        identity = self._require_authoritative_runtime_identity()
        if (
            identity.runtime_mode != record.runtime_mode
            or identity.profile_revision != record.profile_revision
        ):
            self._invalidate_plan(record, reason="runtime_identity_drift")
            raise PlanArtifactError(
                "plan_runtime_drift",
                "Runtime identity changed after preview; create a new preview.",
            )
        try:
            self._require_current_profile_revision(record.profile_revision)
        except PlanArtifactError as exc:
            self._invalidate_plan(record, reason="profile_revision_drift")
            raise PlanArtifactError(
                "plan_profile_drift",
                "Active Profile changed after preview; create a new preview.",
            ) from exc
        snapshot = self._current_plan_readiness()
        current_readiness = build_readiness_binding(
            snapshot,
            robot_ids=record.robot_ids,
        )
        try:
            require_dispatchable_readiness(current_readiness)
        except PlanArtifactError:
            self._invalidate_plan(record, reason="robot_readiness_lost")
            raise
        if current_readiness.revision != record.readiness_revision:
            self._invalidate_plan(record, reason="readiness_revision_drift")
            raise PlanArtifactError(
                "plan_readiness_drift",
                "Robot or admission readiness changed after preview; create a new preview.",
            )
        plan = record.plan()
        # Operator grounding is a preview-time invariant. Relative references
        # such as “返回现在的位置” are resolved from authoritative live pose,
        # validated, and then included in the server-signed plan digest before
        # the operator sees the preview. Rebinding that immutable target during
        # confirmation changes the operator-approved meaning and makes ordinary
        # localization jitter look like registry drift. Dynamic safety remains
        # fail-closed here through runtime/profile/readiness revalidation and
        # the current registry/capability contract below.
        graph = task_graph_from_mission_plan(
            plan,
            mission_id="confirm-validation",
            plan_id=f"sealed:{record.artifact_id}:{record.plan_digest}",
        )
        errors = [
            *validate_plan_2d(plan),
            *MissionPlanValidator().validate(
                plan,
                self.registry,
                task_graph=graph,
            ),
        ]
        if errors:
            logger.warning(
                "Sealed plan confirmation failed registry/capability validation: "
                "artifact_id=%s errors=%s",
                record.artifact_id,
                errors,
            )
            self._invalidate_plan(record, reason="registry_or_capability_drift")
            raise PlanArtifactError(
                "plan_registry_drift",
                "封存计划已不再符合当前机器人注册表或能力契约；"
                "请重新生成任务预览。校验详情："
                + "; ".join(errors[:3]),
            )
        mission_id = f"mission-{uuid4().hex}"
        consumed = self.plan_artifact_store.consume(
            token,
            artifact_id=artifact_id,
            plan_digest=plan_digest,
            status_version=status_version,
            operator_id=operator_id,
            session_id=session_id,
            robot_ids=robot_ids,
            runtime_epoch=self.runtime_epoch,
            mission_id=mission_id,
        )
        result = self.mission_run_manager.submit_preplanned(
            plan,
            mission_id=mission_id,
            operator=operator.to_dict() if operator is not None else None,
            artifact_id=consumed.artifact_id,
            plan_digest=consumed.plan_digest,
        )
        accepted = result.get("status") == "accepted"
        execution_record = self.plan_artifact_store.record_execution(
            consumed.artifact_id,
            execution_status=str(result.get("status") or "unknown"),
            failed=not accepted,
            reason=(
                None
                if accepted
                else str(result.get("message") or "mission_queue_rejected")
            ),
        )
        if not accepted:
            raise PlanArtifactError(
                "sealed_plan_queue_failed",
                str(result.get("message") or "Sealed plan could not be queued."),
            )
        self.publish_event(
            "mission.plan_confirmed",
            "mission-gateway",
            mission_id=mission_id,
            payload={
                "plan_artifact_id": consumed.artifact_id,
                "plan_digest": consumed.plan_digest,
                "binding_digest": consumed.binding_digest,
                "robot_ids": list(consumed.robot_ids),
                "risk_level": consumed.risk_level,
                "status_version": execution_record.status_version,
                "plan_source": "sealed_plan_artifact",
            },
        )
        return {
            **result,
            "task_id": mission_id,
            "artifact_status": execution_record.status,
            "artifact_status_version": execution_record.status_version,
            "binding_digest": consumed.binding_digest,
            "robot_ids": list(consumed.robot_ids),
            "risk_level": consumed.risk_level,
        }

    def _require_authoritative_runtime_identity(self) -> GatewayRuntimeIdentity:
        identity = self.runtime_identity
        if (
            identity.freshness != "fresh"
            or identity.runtime_mode not in {"simulation", "real"}
            or not identity.profile_revision
            or not identity.active_profile_path
            or not identity.robot_id
        ):
            raise PlanArtifactError(
                "runtime_identity_unknown",
                "Gateway runtime identity is UNKNOWN; no executable preview can be sealed.",
            )
        return identity

    def _require_current_profile_revision(self, expected_revision: str | None) -> None:
        path_value = self.runtime_identity.active_profile_path
        if not path_value or not expected_revision:
            raise PlanArtifactError(
                "profile_revision_unknown",
                "Active Profile revision is not authoritative.",
            )
        path = Path(path_value)
        if path.is_symlink() or not path.is_file():
            raise PlanArtifactError(
                "profile_revision_unavailable",
                "Active Profile is unavailable or unsafe.",
            )
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise PlanArtifactError(
                "profile_revision_unavailable",
                "Active Profile could not be read.",
            ) from exc
        if not hmac.compare_digest(expected_revision, f"sha256:{digest}"):
            raise PlanArtifactError(
                "profile_revision_drift",
                "Active Profile content no longer matches the startup revision.",
            )

    def _current_plan_readiness(self) -> Mapping[str, Any]:
        value = (
            self._plan_readiness_provider()
            if self._plan_readiness_provider is not None
            else self.readiness()
        )
        if not isinstance(value, Mapping):
            raise PlanArtifactError(
                "readiness_unavailable",
                "Gateway readiness provider returned an invalid snapshot.",
            )
        return value

    def _invalidate_plan(self, record: PlanArtifactRecord, *, reason: str) -> None:
        try:
            self.plan_artifact_store.invalidate_pending(
                record.artifact_id,
                reason=reason,
            )
        except PlanArtifactError:
            logger.warning(
                "Failed to invalidate pending plan artifact %s",
                record.artifact_id,
                exc_info=True,
            )

    def recover(
        self,
        payload: dict[str, Any],
        *,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        operator_confirmed = payload.get("operator_confirmed")
        if not operator_confirmed:
            from fireclaw_core.errors.friendly_errors import resolve_friendly_error
            resp = resolve_friendly_error(
                "safety_requires_confirmation",
                {"action_description": "Mission Gateway 准入投影重置请求"},
            )
            return {
                "status": "error",
                "message": "Operator confirmation is required for admission projection reset.",
                "error": resp.to_dict(),
            }
        reason = str(
            payload.get("reason")
            or "Operator submitted confirmation without additional notes"
        )
        robot_id = _optional_string(payload, "robot_id")

        if hasattr(self.mission_agent, "dispatch_recovery_report"):
            self.mission_agent.dispatch_recovery_report.clear()

        self.publish_event(
            "mission.admission_projection_reset",
            "mission-gateway",
            payload={
                "operator_confirmed": True,
                "reason": reason,
                "robot_id": robot_id,
                "physical_stop_confirmed": False,
                "robot_physical_status": "unknown",
            },
        )
        return {
            "status": "ok",
            "admission_projection_reset": True,
            "physical_stop_confirmed": False,
            "robot_physical_status": "unknown",
            "message": (
                "Mission Gateway admission projection was reset; this does not "
                "confirm physical safety or implement formal two-phase recovery."
            ),
            "readiness": self.readiness(),
        }

    def submit_mission(
        self,
        command: str,
        *,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        use_scheduler: bool = True,
        background: bool | None = None,
    ) -> dict[str, Any]:
        # Guard: validate resume ownership when session_id is provided
        if (
            session_id is not None
            and self._session_lineage_store is not None
            and operator is not None
        ):
            decision = validate_resume_ownership(
                self._session_lineage_store,
                session_id=session_id,
                operator_id=operator.operator_id,
            )
            if not decision.ok:
                return {"status": "denied", "message": decision.reason}

        operator_dict = operator.to_dict() if operator is not None else None
        # Scheduler-backed submissions default to the non-blocking Mission Run
        # path.  Explicit ``background=false`` remains available for legacy
        # callers and deterministic CLI/test workflows.
        if background is None:
            background = bool(use_scheduler)
        if background and self.mission_agent.mission_deliberation_runtime is None:
            # Preserve an immediate, useful error for an unconfigured planner.
            background = False
        if background:
            result = self.mission_run_manager.submit(
                command,
                mission_id=session_id,
                operator=operator_dict,
                use_scheduler=use_scheduler,
            )
            mission_id = result.get("mission_id")
            if mission_id:
                self.publish_event(
                    "mission.submitted",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={
                        "command": command,
                        "status": result.get("status"),
                        "run_id": result.get("run_id"),
                    },
                )
            return result
        result = self.mission_agent.plan_and_submit(
            command,
            session_id=session_id,
            operator=operator_dict,
            use_scheduler=use_scheduler,
        )
        mission_id = result.get("mission_id")
        if mission_id:
            self.publish_event(
                "mission.submitted",
                "mission-gateway",
                mission_id=mission_id,
                payload={"command": command, "status": result.get("status")},
            )
            if result.get("status") == "planned":
                self.publish_event(
                    "mission.planned",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={"intent": result.get("intent")},
                )
            for subtask in result.get("subtask_results", []):
                if subtask.get("status") == "accepted":
                    self.publish_event(
                        "mission.subtask_dispatched",
                        "mission-gateway",
                        mission_id=mission_id,
                        task_id=subtask.get("task_id"),
                        payload={
                            "robot_id": subtask.get("robot_id"),
                            "task_id": subtask.get("task_id"),
                            "status": subtask.get("status"),
                        },
                    )
        return result

    def get_mission_run(self, mission_id: str) -> dict[str, Any]:
        return self.mission_run_manager.get(mission_id)

    def get_mission_report(self, mission_id: str) -> dict[str, Any]:
        report = self.mission_run_manager.report(mission_id)
        if report.get("status") == "not_found":
            persisted = self.mission_agent.final_report(mission_id)
            if persisted is not None:
                return persisted
        return report

    def pause_mission(self, mission_id: str) -> dict[str, Any]:
        return self.mission_run_manager.pause(mission_id)

    def resume_mission(self, mission_id: str) -> dict[str, Any]:
        return self.mission_run_manager.resume(mission_id)

    def correct_mission(
        self,
        mission_id: str,
        payload: dict[str, Any],
        *,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        correction = payload.get("correction")
        if not isinstance(correction, str) or not correction.strip():
            return {
                "status": "error",
                "mission_id": mission_id,
                "message": "Field 'correction' is required.",
            }
        return self.mission_run_manager.correct(
            mission_id,
            correction=correction.strip(),
            context=_optional_string(payload, "context"),
            robot_id=_optional_string(payload, "robot_id"),
            subtask_id=_optional_string(payload, "subtask_id"),
            operator=operator.to_dict() if operator is not None else None,
        )

    def get_mission_trace(self, mission_id: str) -> dict[str, Any]:
        trace = self.mission_agent.mission_trace(mission_id)
        run = self.mission_run_manager.get(mission_id)
        if run.get("status") != "not_found":
            trace["run"] = run
            trace["run_status"] = run.get("run_status", run.get("status"))
            if run.get("terminal") and trace.get("status") in {"created", "running"}:
                trace["status"] = _mission_trace_status_from_run(
                    str(run.get("run_status") or run.get("status") or "")
                )
            if run.get("final_report") is not None:
                trace["final_report"] = run["final_report"]
        return trace

    def get_mission_events(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self.mission_agent.mission_events(
            mission_id,
            robot_id=robot_id,
            event_type=event_type,
            limit=limit,
        )

    def get_memory_tool_definitions(self, mission_id: str) -> dict[str, Any]:
        tools = self.mission_agent.memory_tool_definitions()
        lifecycle = self.mission_agent.memory_lifecycle
        lifecycle_state = lifecycle.state(mission_id).to_dict() if lifecycle else None
        if lifecycle_state is not None and not lifecycle_state["operational"]:
            tools = []
        return {
            "mission_id": mission_id,
            "runtime_mode": self.mission_agent.embodied_runtime_mode,
            "tools": tools,
            "count": len(tools),
            "read_only": True,
            "advisory_only": True,
            "memory_lifecycle": lifecycle_state,
        }

    def call_memory_tool(
        self,
        mission_id: str,
        *,
        name: str,
        arguments: dict[str, Any],
        requester_id: str,
        scopes: frozenset[str],
    ) -> dict[str, Any]:
        return self.mission_agent.call_memory_tool(
            mission_id=mission_id,
            name=name,
            arguments=arguments,
            requester_id=requester_id,
            scopes=scopes,
        )

    def get_memory_lifecycle(self, mission_id: str) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured", "mission_id": mission_id}
        return {
            "status": "ok",
            "lifecycle": lifecycle.state(mission_id).to_dict(),
            "history": lifecycle.lifecycle_history(mission_id),
        }

    def get_memory_audit(
        self,
        mission_id: str,
        *,
        include_restricted: bool,
        limit: int,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured", "mission_id": mission_id}
        return lifecycle.read_audit(
            mission_id,
            include_restricted=include_restricted,
            limit=limit,
        )

    def archive_memory(
        self,
        mission_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured", "mission_id": mission_id}
        trace = self.get_mission_trace(mission_id)
        if trace.get("status") == "not_found":
            return {"status": "not_found", "mission_id": mission_id}
        return lifecycle.archive_mission(
            mission_id,
            actor_id=actor_id,
            reason=reason,
            mission_status=str(trace.get("status") or "unknown"),
        )

    def delete_memory_audit(
        self,
        mission_id: str,
        *,
        actor_id: str,
        reason: str,
        confirmation: str,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured", "mission_id": mission_id}
        return lifecycle.delete_audit(
            mission_id,
            actor_id=actor_id,
            reason=reason,
            confirmation=confirmation,
        )

    def list_reusable_knowledge(
        self,
        *,
        knowledge_type: str | None,
        tags: list[str],
        limit: int,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured", "knowledge": [], "count": 0}
        records = lifecycle.list_knowledge(
            knowledge_type=knowledge_type,
            tags=tags,
            limit=limit,
        )
        return {
            "status": "ok",
            "knowledge": [record.to_dict() for record in records],
            "count": len(records),
            "advisory_only": True,
        }

    def approve_reusable_knowledge(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured"}
        source_event_ids = payload.get("source_event_ids")
        content = payload.get("content")
        tags = payload.get("tags", [])
        applicable_runtime_modes = payload.get("applicable_runtime_modes")
        if not isinstance(source_event_ids, list):
            raise ValueError("Field 'source_event_ids' must be an array")
        if not isinstance(content, dict):
            raise ValueError("Field 'content' must be an object")
        if not isinstance(tags, list):
            raise ValueError("Field 'tags' must be an array")
        if applicable_runtime_modes is not None and not isinstance(
            applicable_runtime_modes, list
        ):
            raise ValueError("Field 'applicable_runtime_modes' must be an array")
        record = lifecycle.approve_knowledge(
            source_mission_id=str(payload.get("source_mission_id") or ""),
            source_event_ids=source_event_ids,
            knowledge_type=str(payload.get("knowledge_type") or ""),
            title=str(payload.get("title") or ""),
            content=content,
            tags=tags,
            applicable_runtime_modes=applicable_runtime_modes,
            actor_id=actor_id,
            reason=str(payload.get("reason") or ""),
        )
        return {"status": "approved", "knowledge": record.to_dict()}

    def revoke_reusable_knowledge(
        self,
        knowledge_id: str,
        *,
        actor_id: str,
        reason: str,
    ) -> dict[str, Any]:
        lifecycle = self.mission_agent.memory_lifecycle
        if lifecycle is None:
            return {"status": "not_configured"}
        record = lifecycle.revoke_knowledge(
            knowledge_id,
            actor_id=actor_id,
            reason=reason,
        )
        return {"status": "revoked", "knowledge": record.to_dict()}

    def sync_robot_memory(
        self,
        mission_id: str,
        *,
        robot_id: str | None = None,
        batch_limit: int = 200,
        max_batches: int = 10,
    ) -> dict[str, Any]:
        if self.memory_reconciler is None:
            return {"status": "not_configured", "mission_id": mission_id, "robots": []}
        if not 1 <= batch_limit <= 1000:
            raise ValueError("batch_limit must be between 1 and 1000")
        if not 1 <= max_batches <= 100:
            raise ValueError("max_batches must be between 1 and 100")
        if robot_id is not None:
            entry = self.registry.get(robot_id)
            entries = [entry] if entry is not None and entry.enabled else []
        else:
            entries = self.registry.enabled_entries(include_stale=True)
        if not entries:
            return {
                "status": "not_found",
                "mission_id": mission_id,
                "robot_id": robot_id,
                "robots": [],
            }

        robot_results: list[dict[str, Any]] = []
        for entry in entries:
            expected_store_id = f"robot:{entry.robot_id}"
            if self.memory_reconciler.runtime_mode == "real":
                if not entry.base_url.startswith("https://"):
                    robot_results.append({
                        "robot_id": entry.robot_id,
                        "source_store_id": expected_store_id,
                        "cursor": 0,
                        "has_more": False,
                        "reports": [],
                        "status": "error",
                        "error": "real runtime requires HTTPS robot endpoint",
                    })
                    continue
                if self.replication_security is None:
                    robot_results.append({
                        "robot_id": entry.robot_id,
                        "source_store_id": expected_store_id,
                        "cursor": 0,
                        "has_more": False,
                        "reports": [],
                        "status": "error",
                        "error": "replication security not configured",
                    })
                    continue
            cursor = self.memory_reconciler.checkpoint(
                expected_store_id,
                mission_id=mission_id,
            )
            reports: list[dict[str, Any]] = []
            has_more = False
            error: str | None = None
            for _ in range(max_batches):
                try:
                    runtime_mode = self.memory_reconciler.runtime_mode
                    payload = self.subagent_client.get_memory_replication(
                        entry,
                        cursor=cursor,
                        limit=batch_limit,
                        mission_id=mission_id,
                        runtime_mode=runtime_mode,
                    )
                    if self.replication_security is not None:
                        from fireclaw_core.memory.replication_security import (
                            ReplicationNonceCache,
                            ReplicationRequestScope,
                        )
                        policy = self.replication_security.robot_policies.get(entry.robot_id)
                        if policy is None:
                            raise ValueError("replication robot policy not found")
                        scope = ReplicationRequestScope(
                            mission_id=mission_id,
                            runtime_mode=runtime_mode,
                            cursor=cursor,
                            limit=batch_limit,
                        )
                        report = self.memory_reconciler.ingest_signed_payload(
                            payload,
                            expected_robot_id=entry.robot_id,
                            expected_mission_id=mission_id,
                            expected_scope=scope,
                            key_provider=self.replication_security.key_provider,
                            peer_policy=policy,
                            nonce_cache=self.replication_security.nonce_cache,
                        )
                    else:
                        batch = ReplicationBatch.from_dict(payload)
                        if batch.source_store_id != expected_store_id:
                            raise ValueError("robot replication store identity mismatch")
                        if batch.cursor != cursor:
                            raise ValueError("robot replication cursor mismatch")
                        report = self.memory_reconciler.ingest_batch(
                            batch,
                            expected_robot_id=entry.robot_id,
                            expected_mission_id=mission_id,
                        )
                except Exception as exc:
                    error_class = type(exc).__name__
                    error = f"{error_class}: replication sync failed"
                    break
                reports.append(report.to_dict())
                # Re-parse cursor from payload for loop control
                next_cursor = int(payload.get("next_cursor", cursor))
                batch_has_more = bool(payload.get("has_more", False))
                if next_cursor == cursor and batch_has_more:
                    error = "robot replication cursor made no progress"
                    break
                cursor = next_cursor
                has_more = batch_has_more
                if not has_more:
                    break
            robot_results.append({
                "robot_id": entry.robot_id,
                "source_store_id": expected_store_id,
                "cursor": cursor,
                "has_more": has_more,
                "reports": reports,
                "status": "error" if error is not None else ("partial" if has_more else "synced"),
                "error": error,
            })
        return {
            "status": (
                "error"
                if all(item["status"] == "error" for item in robot_results)
                else "partial"
                if any(item["status"] != "synced" for item in robot_results)
                else "synced"
            ),
            "mission_id": mission_id,
            "robots": robot_results,
        }

    def cancel_mission(
        self,
        mission_id: str,
        *,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self.mission_run_manager.get(mission_id)
        if run.get("status") != "not_found":
            result = self.mission_run_manager.cancel(
                mission_id,
                operator=operator,
            )
        else:
            result = self.mission_agent.cancel_mission(mission_id, operator=operator)
        status = result.get("status")
        if status == "cancel_requested":
            self.publish_event(
                "mission.cancel_requested",
                "mission-gateway",
                mission_id=mission_id,
                payload={"status": status},
            )
        elif status == "cancelled":
            self.publish_event(
                "mission.cancelled",
                "mission-gateway",
                mission_id=mission_id,
                payload={"status": status},
            )
        return result

    def handle_approval(
        self,
        mission_id: str,
        payload: dict[str, Any],
        *,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        action = payload.get("action")
        if action == "request":
            semantic_action = payload.get("semantic_action", "")
            if not semantic_action:
                semantic_action = _derive_action_from_command(payload.get("command", ""))
            result = self.mission_agent.request_approval(
                mission_id,
                action=semantic_action,
                risk_level=payload.get("risk_level", "low"),
                command=payload.get("command", ""),
                operator=operator,
            )
            if result.get("status") == "pending":
                self.publish_event(
                    "mission.approval_requested",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={
                        "request_id": result.get("request", {}).get("request_id"),
                        "action": semantic_action,
                        "risk_level": payload.get("risk_level", "low"),
                    },
                )
            # Apply tool approval hooks from plugin runtime
            approval_reasons: list[dict[str, str]] = []
            if result.get("status") == "pending" and self.plugin_runtime is not None:
                for effect in self.plugin_runtime.run_tool_approval_hooks(
                    "add_reason",
                    {"mission_id": mission_id, "action": semantic_action, "payload": dict(payload)},
                ):
                    reason = effect.get("effect", {}).get("reason")
                    if isinstance(reason, str) and reason:
                        approval_reasons.append({"plugin_id": str(effect.get("plugin_id", "")), "reason": reason})
            if approval_reasons:
                result = {**result, "approval_reasons": approval_reasons}
            if result.get("status") == "pending" and self.approval_runtime is not None:
                request = result.get("request")
                request_id = request.get("request_id") if isinstance(request, dict) else None
                if isinstance(request_id, str) and request_id:
                    relay = payload.get("relay")
                    if isinstance(relay, dict):
                        self._approval_relays[request_id] = {
                            "channel": str(relay.get("channel") or "console"),
                            "operator_id": str(relay.get("operator_id") or "unknown"),
                        }
                    raw_token, token = self.approval_runtime.create_token(request_id)
                    result = {
                        **result,
                        "approval_token": raw_token,
                        "token": _public_token_dict(token.to_dict()),
                    }
                    # Deliver to external relay if configured and relay metadata present
                    if self.approval_relay is not None and isinstance(relay, dict):
                        relay_meta = self._approval_relays.get(request_id, {})
                        try:
                            self.approval_relay.deliver_pending_approval(
                                request_id=request_id,
                                mission_id=mission_id,
                                action=semantic_action,
                                risk_level=payload.get("risk_level", "low"),
                                channel=relay_meta.get("channel", "console"),
                                operator_id=relay_meta.get("operator_id", "unknown"),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Approval relay delivery failed for %s: %s",
                                request_id, exc,
                            )
                            result = {**result, "relay_error": str(exc)}
            return result
        if action == "pending":
            if self.approval_runtime is None:
                return {"status": "not_configured", "pending_approvals": []}
            self.approval_runtime.expire_stale()
            pending = [
                item
                for item in self.approval_runtime.pending_projection()
                if item.get("mission_id") == mission_id
            ]
            for item in pending:
                relay = self._approval_relays.get(str(item.get("request_id")))
                if relay is not None:
                    item["relay"] = dict(relay)
            return {"status": "pending", "pending_approvals": pending}
        if action == "resolve_token":
            if self.approval_runtime is None:
                return {"status": "not_configured"}
            token = payload.get("approval_token")
            if not isinstance(token, str) or not token:
                return {"status": "error", "message": "Field 'approval_token' is required for resolve_token action."}
            record = self.approval_runtime.resolve_token(token)
            if record is None or record.mission_id != mission_id:
                return {"status": "not_found"}
            return {"status": "resolved", "token": _public_token_dict(record.to_dict())}
        if action == "decide":
            request_id = payload.get("request_id", "")
            if not request_id:
                return {"status": "error", "message": "Field 'request_id' is required for decide action."}
            result = self.mission_agent.decide_approval(
                request_id,
                decision=payload.get("decision", ""),
                reason=payload.get("reason"),
                operator=operator,
            )
            if result.get("status") == "decided":
                self.publish_event(
                    "mission.approval_decided",
                    "mission-gateway",
                    mission_id=mission_id,
                    payload={
                        "request_id": request_id,
                        "decision": payload.get("decision", ""),
                    },
                )
            return result
        return {"status": "error", "message": f"Unknown approval action: {action}"}

    def fleet_state(self) -> dict[str, Any]:
        entries = self.registry.enabled_entries(include_stale=True)
        return {
            "entries": [
                {
                    **{
                        "robot_id": e.robot_id,
                        "base_url": e.base_url,
                        "capabilities": list(e.capabilities),
                        "zone": e.zone,
                        "enabled": e.enabled,
                    },
                    "is_online": self.registry.is_online(e.robot_id),
                    "is_stale": self.registry.is_stale(e.robot_id),
                    "last_seen_at": self.registry.get_last_seen_at(e.robot_id),
                }
                for e in entries
            ],
        }

    def fleet_doctor(self) -> dict[str, Any]:
        doctor = FleetDoctor(
            registry=self.registry,
            subagent_client=self.subagent_client,
            task_registry=self.task_registry,
            subagent_registry=self.subagent_registry,
        )
        findings = doctor.diagnose()
        summary = doctor.summary(findings)
        recovery = list(self.mission_agent.dispatch_recovery_report)
        summary["dispatch_recovery"] = {
            "attempted_count": len(recovery),
            "blocked_count": sum(
                item.get("status") == "blocked" for item in recovery
            ),
            "results": recovery,
        }
        summary["network_admission"] = self._network_guard.snapshot()
        return summary

    def publish_event(
        self,
        event_type: str,
        source: str,
        *,
        mission_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish an event to the mission-level EventBus (for SSE streaming)."""
        event = StreamEvent(
            event_type=event_type,
            source=source,
            mission_id=mission_id,
            task_id=task_id,
            payload=payload or {},
        )
        self._event_bus.publish(event)
        self._telemetry.record_event(event)

    def _publish_run_event(
        self,
        event_type: str,
        mission_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Bridge MissionRunManager lifecycle events onto the Gateway EventBus."""
        stream_payload = _mission_run_stream_payload(event_type, payload)
        self.publish_event(
            event_type,
            "mission-run",
            mission_id=mission_id,
            payload=stream_payload,
        )
        if event_type == "mission.report_ready":
            self.publish_event(
                "mission.final_report_ready",
                "mission-run",
                mission_id=mission_id,
                payload=stream_payload,
            )

    # ------------------------------------------------------------------
    # HTTP plumbing
    # ------------------------------------------------------------------

    def _handler_class(self):
        gateway = self

        class MissionRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if not gateway._admit_request(self, method="GET"):
                    return
                principal = gateway._authenticate_request(
                    self,
                    allow_public_health=parsed.path == "/health",
                )
                if principal is None:
                    return
                result = authorize_method(
                    f"GET {parsed.path}",
                    set(principal.gateway_scopes),
                )
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                stream_match = MISSION_EVENTS_STREAM_RE.match(parsed.path)
                if stream_match:
                    gateway._stream_mission_events(
                        self,
                        parsed,
                        mission_id=stream_match.group(1),
                    )
                    return
                if parsed.path in ("/events", "/events/stream"):
                    gateway._stream_mission_events(
                        self,
                        parsed,
                        mission_id=None,
                    )
                    return
                gateway._handle_get(self, principal=principal)

            def do_POST(self) -> None:
                if not gateway._admit_request(self, method="POST"):
                    return
                principal = gateway._authenticate_request(self)
                if principal is None:
                    return
                parsed = urlparse(self.path)
                result = authorize_method(
                    f"POST {parsed.path}",
                    set(principal.gateway_scopes),
                )
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                gateway._handle_post(self, principal=principal)

            def log_message(self, format: str, *args: object) -> None:
                return

        return MissionRequestHandler

    def _admit_request(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        method: str,
    ) -> bool:
        server_host, server_port = handler.server.server_address
        decision = self._network_guard.admit(
            method=method,
            headers=handler.headers,
            server_host=str(server_host),
            server_port=int(server_port),
        )
        if decision.allowed:
            return True
        handler.close_connection = True
        self._write_error(handler, decision.status, decision.message)
        return False

    def _authenticate_request(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        allow_public_health: bool = False,
    ) -> AuthenticatedGatewayPrincipal | None:
        client_host = str(handler.client_address[0])
        if not allow_public_health:
            rate_limit = self._network_guard.check_auth(client_host)
            if not rate_limit.allowed:
                body = json.dumps(
                    {"error": "Too many authentication failures."},
                    ensure_ascii=False,
                ).encode("utf-8")
                handler.send_response(HTTPStatus.TOO_MANY_REQUESTS)
                handler.send_header(
                    "Retry-After",
                    str(rate_limit.retry_after_seconds),
                )
                handler.send_header(
                    "Content-Type",
                    "application/json; charset=utf-8",
                )
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
                return None
        result = authenticate_gateway_request(
            authorization_header=handler.headers.get("Authorization"),
            client_host=client_host,
            api_token=self.config.api_token,
            allow_public_health=allow_public_health,
        )
        if result.allowed and result.principal is not None:
            if not allow_public_health:
                self._network_guard.reset_auth_failures(client_host)
            return result.principal
        if not allow_public_health:
            self._network_guard.record_auth_failure(client_host)
        body = json.dumps({"error": "Unauthorized"}, ensure_ascii=False).encode("utf-8")
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header("WWW-Authenticate", "Bearer")
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return None

    def _stream_mission_events(
        self,
        handler: BaseHTTPRequestHandler,
        parsed: Any,
        *,
        mission_id: str | None = None,
    ) -> None:
        client_host = str(handler.client_address[0])
        if not self._network_guard.acquire_sse(client_host):
            handler.close_connection = True
            self._write_error(
                handler,
                HTTPStatus.TOO_MANY_REQUESTS,
                "SSE connection limit exceeded.",
            )
            return

        connection = getattr(handler, "connection", None)
        previous_timeout: float | None = None
        if connection is not None and hasattr(connection, "settimeout"):
            try:
                previous_timeout = connection.gettimeout()
            except (AttributeError, OSError):
                previous_timeout = None
            connection.settimeout(
                self.config.network.sse_write_timeout_seconds
            )

        event_queue: Queue[StreamEvent] = Queue(
            maxsize=self.config.network.sse_queue_size
        )
        overflowed = threading.Event()

        def _on_event(event: StreamEvent) -> None:
            if mission_id is not None and event.mission_id not in {mission_id, None}:
                return
            try:
                event_queue.put_nowait(event)
            except QueueFull:
                overflowed.set()

        token = self._event_bus.subscribe(_on_event)
        try:
            handler.send_response(HTTPStatus.OK)
            handler.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8",
            )
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "keep-alive")
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()

            after_seq = _sse_after_sequence(handler, parsed)
            sent_sequences: set[int] = set()
            if after_seq is not None:
                for event in self._event_bus.get_recent_events(
                    after_sequence=after_seq
                ):
                    if mission_id is not None and event.mission_id not in {mission_id, None}:
                        continue
                    handler.wfile.write(
                        event.to_sse_format().encode("utf-8")
                    )
                    handler.wfile.flush()
                    sent_sequences.add(event.sequence)
                    if event.event_type in _MISSION_STREAM_TERMINAL_EVENTS:
                        return
            elif mission_id is not None:
                history = self.get_mission_events(mission_id, limit=200)
                for value in history.get("events", []):
                    history_payload = value.get("payload", value)
                    history_timestamp = value.get("timestamp")
                    event = StreamEvent(
                        event_type=value.get("type", "unknown"),
                        source="mission-history",
                        event_id=value.get("event_id", f"history-{uuid4().hex}"),
                        timestamp=(
                            history_timestamp
                            if isinstance(history_timestamp, str)
                            else datetime.now(timezone.utc).isoformat()
                        ),
                        mission_id=mission_id,
                        robot_id=value.get("robot_id")
                        or (
                            history_payload.get("robot_id")
                            if isinstance(history_payload, dict)
                            else None
                        ),
                        task_id=value.get("task_id")
                        or (
                            history_payload.get("task_id")
                            if isinstance(history_payload, dict)
                            else None
                        ),
                        payload=history_payload,
                    )
                    handler.wfile.write(
                        event.to_sse_format().encode("utf-8")
                    )
                    if event.event_type in _MISSION_STREAM_TERMINAL_EVENTS:
                        handler.wfile.flush()
                        return
                handler.wfile.flush()

            last_write = time.monotonic()
            while not overflowed.is_set():
                timeout = max(0.1, 15.0 - (time.monotonic() - last_write))
                try:
                    event = event_queue.get(timeout=timeout)
                except QueueEmpty:
                    handler.wfile.write(b": heartbeat\n\n")
                    handler.wfile.flush()
                    last_write = time.monotonic()
                    continue
                if event.sequence in sent_sequences:
                    continue
                handler.wfile.write(event.to_sse_format().encode("utf-8"))
                handler.wfile.flush()
                sent_sequences.add(event.sequence)
                last_write = time.monotonic()
                if event.event_type in _MISSION_STREAM_TERMINAL_EVENTS:
                    return
        except Exception:
            return
        finally:
            self._event_bus.unsubscribe(token)
            self._network_guard.release_sse(client_host)
            if connection is not None and hasattr(connection, "settimeout"):
                try:
                    connection.settimeout(previous_timeout)
                except OSError:
                    pass

    def _stream_planning_events(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        run_planning: Callable[
            [Callable[[str, dict[str, Any]], None]],
            dict[str, Any],
        ],
    ) -> None:
        """Run preview planning off the HTTP writer and stream bounded events.

        This mirrors OpenClaw's run/event/final lifecycle while preserving the
        existing synchronous preview endpoints.  The Mission Agent only calls
        a bounded ``put_nowait`` sink; socket writes and heartbeats happen on
        the request thread, so a slow terminal cannot delay model decisions.
        """

        client_host = str(handler.client_address[0])
        if not self._network_guard.acquire_sse(client_host):
            handler.close_connection = True
            self._write_error(
                handler,
                HTTPStatus.TOO_MANY_REQUESTS,
                "SSE connection limit exceeded.",
            )
            return

        connection = getattr(handler, "connection", None)
        previous_timeout: float | None = None
        if connection is not None and hasattr(connection, "settimeout"):
            try:
                previous_timeout = connection.gettimeout()
            except (AttributeError, OSError):
                previous_timeout = None
            connection.settimeout(
                self.config.network.sse_write_timeout_seconds
            )

        started = time.monotonic()
        event_queue: Queue[dict[str, Any]] = Queue(
            maxsize=self.config.network.sse_queue_size
        )
        done = threading.Event()
        state_lock = threading.Lock()
        result_box: dict[str, Any] = {}
        latest_activity: dict[str, Any] = {
            "iteration": None,
            "max_iterations": None,
            "operation": None,
            "message": "Mission Agent planning started.",
        }
        sequence = 0
        dropped_events = 0

        def make_event_locked(
            event_type: str,
            payload: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            nonlocal sequence
            sequence += 1
            return {
                "schema_version": 1,
                "event_type": event_type,
                "sequence": sequence,
                "elapsed_seconds": round(
                    max(0.0, time.monotonic() - started),
                    3,
                ),
                "payload": dict(payload or {}),
            }

        def make_event(
            event_type: str,
            payload: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            with state_lock:
                return make_event_locked(event_type, payload)

        def progress_sink(
            event_type: str,
            payload: dict[str, Any],
        ) -> None:
            nonlocal dropped_events
            with state_lock:
                if event_type == "mission_agent.turn.started":
                    latest_activity["operation"] = None
                for key in (
                    "run_id",
                    "mission_id",
                    "iteration",
                    "max_iterations",
                    "operation",
                    "message",
                ):
                    if payload.get(key) is not None:
                        latest_activity[key] = payload[key]
                event = make_event_locked(event_type, payload)
                try:
                    event_queue.put_nowait(event)
                except QueueFull:
                    dropped_events += 1

        def planning_worker() -> None:
            try:
                result_box["result"] = run_planning(progress_sink)
            except PlanArtifactError as exc:
                result_box["error"] = {
                    "status": "error",
                    "error_code": exc.code,
                    "message": str(exc),
                    "http_status": int(_plan_artifact_http_status(exc.code)),
                }
            except Exception:
                logger.exception("Unhandled error in streamed Mission planning")
                result_box["error"] = {
                    "status": "error",
                    "error_code": "internal_server_error",
                    "message": "Internal server error.",
                    "http_status": int(HTTPStatus.INTERNAL_SERVER_ERROR),
                }
            finally:
                result_box["worker_finished_at"] = time.monotonic()
                done.set()

        def write_event(event: dict[str, Any]) -> None:
            event_type = str(event.get("event_type") or "planning.progress")
            event_sequence = int(event.get("sequence") or 0)
            data = json.dumps(
                event,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            block = (
                f"event: {event_type}\n"
                f"id: {event_sequence}\n"
                f"data: {data}\n\n"
            )
            handler.wfile.write(block.encode("utf-8"))
            handler.wfile.flush()

        try:
            handler.send_response(HTTPStatus.OK)
            handler.send_header(
                "Content-Type",
                "text/event-stream; charset=utf-8",
            )
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("X-Accel-Buffering", "no")
            handler.send_header("Connection", "close")
            handler.end_headers()
            write_event(make_event(
                "planning.started",
                {"message": "Mission Agent planning started."},
            ))

            worker = threading.Thread(
                target=planning_worker,
                name="fireclaw-mission-planning-stream",
                daemon=True,
            )
            worker.start()

            heartbeat_interval = 1.0
            while True:
                try:
                    event = event_queue.get(timeout=heartbeat_interval)
                except QueueEmpty:
                    if done.is_set() and event_queue.empty():
                        break
                    with state_lock:
                        heartbeat_payload = dict(latest_activity)
                        heartbeat_payload["dropped_events"] = dropped_events
                    write_event(make_event(
                        "planning.heartbeat",
                        heartbeat_payload,
                    ))
                    continue
                write_event(event)
                if done.is_set() and event_queue.empty():
                    break

            with state_lock:
                dropped = dropped_events
            if "error" in result_box:
                final_type = "planning.error"
                final_payload = dict(result_box["error"])
            else:
                final_type = "planning.result"
                final_payload = dict(result_box.get("result") or {})
            final_payload["dropped_progress_events"] = dropped
            worker_finished_at = result_box.get("worker_finished_at")
            sse_finalize_ms = (
                max(0.0, (time.monotonic() - float(worker_finished_at)) * 1000)
                if isinstance(worker_finished_at, (int, float))
                else 0.0
            )
            sse_stage = {
                "stage": "sse_finalize",
                "status": "completed",
                "duration_ms": round(sse_finalize_ms, 2),
            }
            timing_payload = final_payload.get("planning_timing")
            if not isinstance(timing_payload, dict):
                timing_payload = _planning_timing_payload(())
            timing_stages = timing_payload.get("stages")
            if not isinstance(timing_stages, list):
                timing_stages = []
            timing_stages.append(sse_stage)
            by_stage_ms = timing_payload.get("by_stage_ms")
            if not isinstance(by_stage_ms, dict):
                by_stage_ms = {}
            by_stage_ms["sse_finalize"] = round(sse_finalize_ms, 2)
            final_payload["planning_timing"] = {
                "stages": timing_stages,
                "by_stage_ms": by_stage_ms,
            }
            write_event(make_event(
                "mission_agent.stage.completed",
                sse_stage,
            ))
            write_event(make_event(final_type, final_payload))
        except (BrokenPipeError, ConnectionError, OSError):
            # Preview planning is read-only.  If the client disconnects, the
            # bounded worker may finish, but no robot action can be dispatched.
            return
        finally:
            handler.close_connection = True
            self._network_guard.release_sse(client_host)
            if connection is not None and hasattr(connection, "settimeout"):
                try:
                    connection.settimeout(previous_timeout)
                except OSError:
                    pass

    def _handle_get(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        principal: AuthenticatedGatewayPrincipal,
    ) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/console"):
            from fireclaw_core.web_console import read_web_console_asset

            try:
                body, content_type = read_web_console_asset("index.html")
            except FileNotFoundError:
                self._write_error(handler, HTTPStatus.NOT_FOUND, "Web console index.html not found.")
                return
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        if path.startswith("/static/"):
            from fireclaw_core.web_console import read_web_console_asset

            rel_path = unquote(path[len("/static/"):])
            try:
                body, content_type = read_web_console_asset(rel_path)
            except (FileNotFoundError, ValueError):
                self._write_error(handler, HTTPStatus.NOT_FOUND, f"Static asset not found: {path}")
                return
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return

        if path == "/health":
            self._write_json(handler, HTTPStatus.OK, self.health())
            return
        if path == "/readiness":
            self._write_json(handler, HTTPStatus.OK, self.readiness())
            return
        if path == "/fleet/state":
            self._write_json(handler, HTTPStatus.OK, self.fleet_state())
            return
        if path == "/fleet/doctor":
            self._write_json(handler, HTTPStatus.OK, self.fleet_doctor())
            return
        if path == "/memory/knowledge":
            try:
                result = self.list_reusable_knowledge(
                    knowledge_type=_first(query, "knowledge_type"),
                    tags=[value for value in query.get("tag", []) if value],
                    limit=_int_query(query, "limit", 100),
                )
            except ValueError as exc:
                self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._write_json(handler, HTTPStatus.OK, result)
            return

        if path == "/config/templates":
            from fireclaw_core.config.templates import TemplateManager
            manager = TemplateManager()
            templates = [t.to_dict() for t in manager.list_templates()]
            self._write_json(handler, HTTPStatus.OK, {"status": "ok", "templates": templates})
            return

        if path == "/config/schema":
            from fireclaw_core.config.schema import get_core_config_schemas
            schemas = get_core_config_schemas()
            schema_dict = {}
            for s in schemas:
                schema_dict[s.plugin_name] = s.to_dict()
                parts = s.plugin_name.split(".")
                for part in parts:
                    if part not in schema_dict:
                        schema_dict[part] = s.to_dict()
                if "navigation" in s.plugin_name and "navigation" not in schema_dict:
                    schema_dict["navigation"] = s.to_dict()
                if s.plugin_name == "fireclaw.sensors.thermal":
                    schema_dict["ros1_sensor"] = s.to_dict()
            self._write_json(
                handler,
                HTTPStatus.OK,
                {"status": "ok", "schemas": schema_dict},
            )
            return

        if path == "/config/history":
            from fireclaw_core.config.snapshots import ProfileSnapshotManager
            from fireclaw_core.infra.user_setup import resolve_active_profile_path
            profile_param = _first(query, "profile")
            try:
                profile_path = Path(profile_param) if profile_param else resolve_active_profile_path(None)
            except Exception as exc:
                self._write_friendly_error(
                    handler,
                    HTTPStatus.BAD_REQUEST,
                    "config_validation_failed",
                    context={"validation_errors": f"Could not resolve profile: {exc}"},
                    technical_details=str(exc),
                )
                return
            if not profile_path.is_file():
                self._write_friendly_error(
                    handler,
                    HTTPStatus.NOT_FOUND,
                    "config_validation_failed",
                    context={"validation_errors": f"Profile file not found: {profile_path}"},
                    technical_details=str(profile_path),
                )
                return
            snap_mgr = ProfileSnapshotManager(history_root=profile_path.parent / ".history")
            snapshots = [s.to_dict() for s in snap_mgr.list_snapshots(profile_path.stem)]
            self._write_json(
                handler,
                HTTPStatus.OK,
                {"status": "ok", "profile_path": str(profile_path), "snapshots": snapshots},
            )
            return

        trace_match = MISSION_TRACE_RE.match(path) or TASK_TRACE_RE.match(path)
        if trace_match:
            mission_id = trace_match.group(1)
            self._write_json(handler, HTTPStatus.OK, self.get_mission_trace(mission_id))
            return

        run_match = MISSION_RUN_RE.match(path) or TASK_RUN_RE.match(path)
        if run_match:
            mission_id = run_match.group(1)
            result = self.get_mission_run(mission_id)
            if _first(query, "view") == "status":
                result = _mission_run_status_projection(result)
            status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
            self._write_json(handler, status, result)
            return

        report_match = MISSION_REPORT_RE.match(path) or TASK_REPORT_RE.match(path)
        if report_match:
            mission_id = report_match.group(1)
            result = self.get_mission_report(mission_id)
            status = (
                HTTPStatus.NOT_FOUND
                if result.get("status") == "not_found"
                else HTTPStatus.ACCEPTED
                if result.get("status") == "pending"
                else HTTPStatus.OK
            )
            self._write_json(handler, status, result)
            return

        events_match = MISSION_EVENTS_RE.match(path) or TASK_EVENTS_RE.match(path)
        if events_match:
            mission_id = events_match.group(1)
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.get_mission_events(
                    mission_id,
                    robot_id=_first(query, "robot_id"),
                    event_type=_first(query, "event_type"),
                    limit=_int_query(query, "limit", 200),
                ),
            )
            return

        tools_match = MISSION_MEMORY_TOOLS_RE.match(path)
        if tools_match:
            mission_id = tools_match.group(1)
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.get_memory_tool_definitions(mission_id),
            )
            return

        lifecycle_match = MISSION_MEMORY_LIFECYCLE_RE.match(path)
        if lifecycle_match:
            mission_id = lifecycle_match.group(1)
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.get_memory_lifecycle(mission_id),
            )
            return

        audit_match = MISSION_MEMORY_AUDIT_RE.match(path)
        if audit_match:
            mission_id = audit_match.group(1)
            scopes = principal.gateway_scopes
            try:
                result = self.get_memory_audit(
                    mission_id,
                    include_restricted=(
                        "admin" in scopes or "memory.restricted.read" in scopes
                    ),
                    limit=_int_query(query, "limit", 200),
                )
            except (ValueError, RuntimeError) as exc:
                self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._write_json(handler, HTTPStatus.OK, result)
            return

        self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")

    def _handle_post(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        principal: AuthenticatedGatewayPrincipal,
    ) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        operator = principal.operator
        try:
            payload = self._read_json(handler)

            if path == "/plan-mission/stream":
                allowed = {
                    "task_description",
                    "command",
                    "task",
                    "target_robot",
                }
                unknown = sorted(set(payload) - allowed)
                if unknown:
                    raise PlanArtifactError(
                        "plan_preview_payload_invalid",
                        "Preview payload contains unsupported fields: "
                        + ", ".join(unknown),
                    )
                task_desc = (
                    payload.get("task_description")
                    or payload.get("command")
                    or payload.get("task")
                )
                if not isinstance(task_desc, str) or not task_desc.strip():
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "planner_failed",
                        context={
                            "goal": "",
                            "reason": (
                                "Field 'task_description' or 'command' "
                                "is required."
                            ),
                        },
                        technical_details=(
                            "Field 'task_description' or 'command' is "
                            "missing or empty."
                        ),
                    )
                    return
                target_robot = _optional_string(
                    payload,
                    "target_robot",
                )
                self._stream_planning_events(
                    handler,
                    run_planning=lambda event_sink: self.plan_mission(
                        task_desc,
                        target_robot=target_robot,
                        operator=operator,
                        event_sink=event_sink,
                    ),
                )
                return

            if path == "/plan-mission":
                allowed = {"task_description", "command", "task", "target_robot"}
                unknown = sorted(set(payload) - allowed)
                if unknown:
                    raise PlanArtifactError(
                        "plan_preview_payload_invalid",
                        "Preview payload contains unsupported fields: "
                        + ", ".join(unknown),
                    )
                task_desc = payload.get("task_description") or payload.get("command") or payload.get("task")
                if not isinstance(task_desc, str) or not task_desc.strip():
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "planner_failed",
                        context={"goal": "", "reason": "Field 'task_description' or 'command' is required."},
                        technical_details="Field 'task_description' or 'command' is missing or empty.",
                    )
                    return
                result = self.plan_mission(
                    task_desc,
                    target_robot=_optional_string(payload, "target_robot"),
                    operator=operator,
                )
                self._write_json(
                    handler,
                    (
                        HTTPStatus.OK
                        if result.get("status") in {
                            "preview_ready",
                            "clarification_required",
                        }
                        else HTTPStatus.UNPROCESSABLE_ENTITY
                    ),
                    result,
                )
                return

            if path == "/plan-mission/clarification/stream":
                allowed = {"planning_session_id", "answer"}
                unknown = sorted(set(payload) - allowed)
                if unknown:
                    raise PlanArtifactError(
                        "plan_clarification_payload_invalid",
                        "Clarification payload contains unsupported fields: "
                        + ", ".join(unknown),
                    )
                planning_session_id = _optional_string(
                    payload,
                    "planning_session_id",
                )
                answer = _optional_string(payload, "answer")
                if planning_session_id is None or answer is None:
                    raise PlanArtifactError(
                        "plan_clarification_payload_invalid",
                        "Fields 'planning_session_id' and 'answer' are required.",
                    )
                self._stream_planning_events(
                    handler,
                    run_planning=lambda event_sink: (
                        self.continue_plan_mission(
                            planning_session_id,
                            answer,
                            operator=operator,
                            event_sink=event_sink,
                        )
                    ),
                )
                return

            if path == "/plan-mission/clarification":
                allowed = {"planning_session_id", "answer"}
                unknown = sorted(set(payload) - allowed)
                if unknown:
                    raise PlanArtifactError(
                        "plan_clarification_payload_invalid",
                        "Clarification payload contains unsupported fields: "
                        + ", ".join(unknown),
                    )
                planning_session_id = _optional_string(
                    payload,
                    "planning_session_id",
                )
                answer = _optional_string(payload, "answer")
                if planning_session_id is None or answer is None:
                    raise PlanArtifactError(
                        "plan_clarification_payload_invalid",
                        "Fields 'planning_session_id' and 'answer' are required.",
                    )
                result = self.continue_plan_mission(
                    planning_session_id,
                    answer,
                    operator=operator,
                )
                self._write_json(
                    handler,
                    (
                        HTTPStatus.OK
                        if result.get("status") in {
                            "preview_ready",
                            "clarification_required",
                        }
                        else HTTPStatus.UNPROCESSABLE_ENTITY
                    ),
                    result,
                )
                return

            if path == "/plan-mission/confirm":
                result = self.confirm_plan(payload, operator=operator)
                self._write_json(handler, HTTPStatus.ACCEPTED, result)
                return

            if path in ("/missions", "/tasks"):
                self._write_json(
                    handler,
                    HTTPStatus.PRECONDITION_REQUIRED,
                    {
                        "status": "error",
                        "error_code": "plan_confirmation_required",
                        "message": (
                            "Direct mission submission is disabled. Create a "
                            "sealed preview at POST /plan-mission, then consume "
                            "it once at POST /plan-mission/confirm."
                        ),
                        "preview_endpoint": "/plan-mission",
                        "confirm_endpoint": "/plan-mission/confirm",
                    },
                )
                return

            if path == "/recover":
                result = self.recover(payload, operator=operator)
                status = HTTPStatus.BAD_REQUEST if result.get("status") == "error" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            if path == "/memory/knowledge/approve":
                try:
                    result = self.approve_reusable_knowledge(
                        payload,
                        actor_id=principal.principal_id,
                    )
                except ValueError as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._write_json(handler, HTTPStatus.OK, result)
                return

            knowledge_revoke_match = MEMORY_KNOWLEDGE_REVOKE_RE.match(path)
            if knowledge_revoke_match:
                try:
                    result = self.revoke_reusable_knowledge(
                        knowledge_revoke_match.group(1),
                        actor_id=principal.principal_id,
                        reason=str(payload.get("reason") or ""),
                    )
                except KeyError as exc:
                    self._write_error(handler, HTTPStatus.NOT_FOUND, str(exc))
                    return
                except ValueError as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._write_json(handler, HTTPStatus.OK, result)
                return

            archive_match = MISSION_MEMORY_ARCHIVE_RE.match(path)
            if archive_match:
                mission_id = archive_match.group(1)
                try:
                    result = self.archive_memory(
                        mission_id,
                        actor_id=principal.principal_id,
                        reason=str(payload.get("reason") or ""),
                    )
                except (ValueError, RuntimeError) as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            delete_match = MISSION_MEMORY_DELETE_RE.match(path)
            if delete_match:
                mission_id = delete_match.group(1)
                try:
                    result = self.delete_memory_audit(
                        mission_id,
                        actor_id=principal.principal_id,
                        reason=str(payload.get("reason") or ""),
                        confirmation=str(payload.get("confirmation") or ""),
                    )
                except (ValueError, RuntimeError) as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._write_json(handler, HTTPStatus.OK, result)
                return

            sync_match = MISSION_MEMORY_SYNC_RE.match(path)
            if sync_match:
                mission_id = sync_match.group(1)
                try:
                    result = self.sync_robot_memory(
                        mission_id,
                        robot_id=_optional_string(payload, "robot_id"),
                        batch_limit=_optional_positive_int(payload, "batch_limit", 200),
                        max_batches=_optional_positive_int(payload, "max_batches", 10),
                    )
                except ValueError as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if result.get("status") == "not_configured"
                    else HTTPStatus.NOT_FOUND
                    if result.get("status") == "not_found"
                    else HTTPStatus.OK
                )
                self._write_json(handler, status, result)
                return

            tool_call_match = MISSION_MEMORY_TOOL_CALL_RE.match(path)
            if tool_call_match:
                mission_id = tool_call_match.group(1)
                name = payload.get("name")
                arguments = payload.get("arguments", {})
                if not isinstance(name, str) or not name.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'name' is required.")
                    return
                if not isinstance(arguments, dict):
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'arguments' must be an object.")
                    return
                try:
                    result = self.call_memory_tool(
                        mission_id,
                        name=name.strip(),
                        arguments=arguments,
                        requester_id=principal.principal_id,
                        scopes=principal.gateway_scopes,
                    )
                except (ValueError, RuntimeError) as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if result.get("status") == "not_configured"
                    else HTTPStatus.OK
                )
                self._write_json(handler, status, result)
                return

            cancel_match = MISSION_CANCEL_RE.match(path) or TASK_CANCEL_RE.match(path)
            if cancel_match:
                mission_id = cancel_match.group(1)
                result = self.cancel_mission(
                    mission_id,
                    operator=operator.to_dict() if operator is not None else None,
                )
                if "mission_id" in result and "task_id" not in result:
                    result["task_id"] = result["mission_id"]
                status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            pause_match = MISSION_PAUSE_RE.match(path) or TASK_PAUSE_RE.match(path)
            if pause_match:
                mission_id = pause_match.group(1)
                result = self.pause_mission(mission_id)
                status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            resume_match = MISSION_RESUME_RE.match(path) or TASK_RESUME_RE.match(path)
            if resume_match:
                mission_id = resume_match.group(1)
                result = self.resume_mission(mission_id)
                status = HTTPStatus.NOT_FOUND if result.get("status") == "not_found" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            correction_match = MISSION_CORRECTIONS_RE.match(path)
            if correction_match:
                mission_id = correction_match.group(1)
                result = self.correct_mission(
                    mission_id,
                    payload,
                    operator=principal.operator,
                )
                status = (
                    HTTPStatus.NOT_FOUND
                    if result.get("status") == "not_found"
                    else HTTPStatus.FORBIDDEN
                    if result.get("status") == "denied"
                    else HTTPStatus.BAD_REQUEST
                    if result.get("status") == "error"
                    else HTTPStatus.OK
                )
                self._write_json(handler, status, result)
                return

            approvals_match = MISSION_APPROVALS_RE.match(path)
            if approvals_match:
                mission_id = approvals_match.group(1)
                result = self.handle_approval(
                    mission_id,
                    payload,
                    operator=principal.operator,
                )
                is_error = result.get("status") == "error"
                status = HTTPStatus.BAD_REQUEST if is_error else HTTPStatus.OK
                self._write_json(handler, status, result)
                return

            if path == "/config/discover":
                from fireclaw_core.config.discovery import RosGraphDiscoverer
                master_uri = payload.get("master_uri")
                timeout = float(payload.get("timeout", 2.0))
                discoverer = RosGraphDiscoverer()
                report = discoverer.probe_ros_master(master_uri=master_uri, timeout=timeout)
                self._write_json(handler, HTTPStatus.OK, report.to_dict())
                return

            if path == "/config/diff":
                from fireclaw_core.config.diff_engine import ConfigDiffEngine
                old_config = payload.get("old_config")
                new_config = payload.get("new_config")
                if old_config is None or new_config is None:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "config_validation_failed",
                        context={"validation_errors": "Fields 'old_config' and 'new_config' are required."},
                    )
                    return
                engine = ConfigDiffEngine()
                if isinstance(old_config, str) and isinstance(new_config, str):
                    result = engine.compare_toml_strings(old_config, new_config)
                elif isinstance(old_config, dict) and isinstance(new_config, dict):
                    result = engine.compare_configs(old_config, new_config)
                elif isinstance(old_config, str) and isinstance(new_config, dict):
                    result = engine.compare_configs(tomllib.loads(old_config), new_config)
                elif isinstance(old_config, dict) and isinstance(new_config, str):
                    result = engine.compare_configs(old_config, tomllib.loads(new_config))
                else:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "config_validation_failed",
                        context={"validation_errors": "Invalid old_config or new_config type."},
                    )
                    return
                self._write_json(handler, HTTPStatus.OK, result.to_dict())
                return

            if path == "/config/save":
                from fireclaw_core.config.snapshots import ProfileSnapshotManager
                from fireclaw_core.config.secrets import SecretManager
                profile_path_str = payload.get("profile_path")
                content = payload.get("content")
                if not profile_path_str or content is None:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "config_save_failed",
                        context={
                            "profile_path": profile_path_str or "",
                            "reason": "Fields 'profile_path' and 'content' are required.",
                        },
                    )
                    return
                profile_path = Path(profile_path_str)
                profile_path.parent.mkdir(parents=True, exist_ok=True)
                content_str = str(content)
                profile_path.write_text(content_str, encoding="utf-8")

                create_snap = payload.get("create_snapshot", True)
                snapshot_id = None
                if create_snap:
                    snap_mgr = ProfileSnapshotManager(history_root=profile_path.parent / ".history")
                    snap = snap_mgr.create_snapshot(
                        profile_path,
                        summary=str(payload.get("reason") or "Saved via Gateway"),
                    )
                    snapshot_id = snap.snapshot_id
                self._write_json(
                    handler,
                    HTTPStatus.OK,
                    {
                        "status": "saved",
                        "profile_path": str(profile_path),
                        "snapshot_id": snapshot_id,
                    },
                )
                return

            if path == "/config/rollback":
                from fireclaw_core.config.snapshots import ProfileSnapshotManager
                from fireclaw_core.infra.user_setup import resolve_active_profile_path
                snapshot_id = payload.get("snapshot_id")
                profile_path_str = payload.get("profile_path")
                if not snapshot_id:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "config_rollback_failed",
                        context={
                            "snapshot_id": "",
                            "reason": "Field 'snapshot_id' is required.",
                        },
                    )
                    return
                try:
                    profile_path = Path(profile_path_str) if profile_path_str else resolve_active_profile_path(None)
                except Exception as exc:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "config_rollback_failed",
                        context={
                            "snapshot_id": snapshot_id,
                            "reason": f"Could not resolve profile path: {exc}",
                        },
                        technical_details=str(exc),
                    )
                    return
                if not profile_path.is_file():
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.NOT_FOUND,
                        "config_validation_failed",
                        context={
                            "validation_errors": f"Profile file not found: {profile_path}",
                        },
                        technical_details=str(profile_path),
                    )
                    return
                snap_mgr = ProfileSnapshotManager(history_root=profile_path.parent / ".history")
                success, msg = snap_mgr.rollback_to_snapshot(snapshot_id, profile_path)
                if not success:
                    self._write_friendly_error(
                        handler,
                        HTTPStatus.NOT_FOUND,
                        "snapshot_not_found",
                        context={"snapshot_id": snapshot_id, "reason": msg},
                        technical_details=msg,
                    )
                    return
                self._write_json(
                    handler,
                    HTTPStatus.OK,
                    {
                        "status": "rolled_back",
                        "snapshot_id": snapshot_id,
                        "profile_path": str(profile_path),
                        "message": msg,
                    },
                )
                return

            if path == "/config/test-field":
                import time
                from urllib import request as urllib_request
                from fireclaw_core.config.discovery import RosGraphDiscoverer

                field_name = str(payload.get("field_name") or "")
                field_value = str(payload.get("field_value") or "")
                probe_action = str(payload.get("probe_action") or "")

                start_t = time.perf_counter()

                if probe_action == "master" or "master_uri" in field_name:
                    discoverer = RosGraphDiscoverer()
                    report = discoverer.probe_ros_master(master_uri=field_value, timeout=1.0)
                    elapsed = (time.perf_counter() - start_t) * 1000.0
                    if report.discovered:
                        self._write_json(handler, HTTPStatus.OK, {
                            "status": "ok",
                            "field_name": field_name,
                            "field_value": field_value,
                            "latency_ms": round(elapsed, 2),
                            "message": f"ROS Master reachable ({report.master_uri})",
                        })
                    else:
                        self._write_json(handler, HTTPStatus.OK, {
                            "status": "error",
                            "field_name": field_name,
                            "field_value": field_value,
                            "latency_ms": round(elapsed, 2),
                            "message": f"ROS Master unreachable at {field_value}",
                        })
                    return
                elif probe_action == "gateway" or "base_url" in field_name:
                    try:
                        req = urllib_request.Request(f"{field_value.rstrip('/')}/health", method="GET")
                        with urllib_request.urlopen(req, timeout=1.0) as resp:
                            elapsed = (time.perf_counter() - start_t) * 1000.0
                            self._write_json(handler, HTTPStatus.OK, {
                                "status": "ok",
                                "field_name": field_name,
                                "field_value": field_value,
                                "latency_ms": round(elapsed, 2),
                                "message": f"Gateway reachable (HTTP {resp.status})",
                            })
                    except Exception as exc:
                        elapsed = (time.perf_counter() - start_t) * 1000.0
                        self._write_json(handler, HTTPStatus.OK, {
                            "status": "error",
                            "field_name": field_name,
                            "field_value": field_value,
                            "latency_ms": round(elapsed, 2),
                            "message": f"Endpoint unreachable: {exc}",
                        })
                    return
                else:
                    elapsed = (time.perf_counter() - start_t) * 1000.0
                    if field_value.startswith("/"):
                        self._write_json(handler, HTTPStatus.OK, {
                            "status": "ok",
                            "field_name": field_name,
                            "field_value": field_value,
                            "latency_ms": round(elapsed, 2),
                            "message": f"Valid ROS topic name syntax: {field_value}",
                        })
                    else:
                        self._write_json(handler, HTTPStatus.OK, {
                            "status": "ok",
                            "field_name": field_name,
                            "field_value": field_value,
                            "latency_ms": round(elapsed, 2),
                            "message": f"Configured value: {field_value}",
                        })
                    return

            self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {path}")
        except GatewayRequestBodyError as exc:
            self._write_error(handler, exc.status, str(exc))
        except PlanArtifactError as exc:
            self._write_json(
                handler,
                _plan_artifact_http_status(exc.code),
                {
                    "status": "error",
                    "error_code": exc.code,
                    "message": str(exc),
                },
            )
        except Exception:
            logger.exception("Unhandled error in POST %s", path)
            self._write_error(handler, HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error.")

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        return read_json_object_body(handler, self.config.network)

    def _write_json(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _write_error(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
        self._write_json(handler, status, {"status": "error", "message": message})

    def _write_friendly_error(
        self,
        handler: BaseHTTPRequestHandler,
        status: HTTPStatus,
        error_code: str,
        context: dict[str, Any] | None = None,
        technical_details: str | None = None,
    ) -> None:
        from fireclaw_core.errors.friendly_errors import resolve_friendly_error

        resp = resolve_friendly_error(
            error_code,
            context=context,
            technical_details=technical_details,
        )
        payload = {
            "status": "error",
            "message": resp.what_happened,
            "error": resp.to_dict(),
        }
        self._write_json(handler, status, payload)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    value = _first(query, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _mission_run_stream_payload(
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep replayable SSE lifecycle events bounded; reports remain queryable."""

    if event_type not in _MISSION_STREAM_REPORT_EVENTS:
        return dict(payload)

    projected: dict[str, Any] = {}
    for key in (
        "status",
        "report_status",
        "report_pending",
        "report_available",
        "report_duration_ms",
        "plan_artifact_id",
        "plan_digest",
        "plan_source",
    ):
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or isinstance(value, (str, int, float, bool)):
            projected[key] = value

    report: Mapping[str, Any] | None = None
    final_report = payload.get("final_report")
    if isinstance(final_report, Mapping):
        report = final_report
    elif event_type == "mission.report_ready":
        report = payload
    if report is not None:
        projected["report_available"] = True
        report_status = payload.get("report_status")
        if report_status != "ready":
            report_status = report.get("status")
        if isinstance(report_status, str):
            projected["report_status"] = report_status
            projected.setdefault("status", report_status)
        summary = report.get("summary")
        if isinstance(summary, str):
            projected["report_summary"] = _bounded_stream_text(summary)
    return projected


def _planning_timing_payload(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return bounded, JSON-safe stage timings for preview responses."""

    stages: list[dict[str, Any]] = []
    by_stage_ms: dict[str, float] = {}
    allowed_keys = {
        "stage",
        "status",
        "duration_ms",
        "iteration",
        "record_kind",
        "critical_chars",
        "advisory_chars",
        "memory_count",
        "correction_count",
        "external_knowledge_count",
        "input_tokens",
        "output_reserve_tokens",
        "model",
        "outcome",
        "subtask_count",
        "bound_target_count",
        "ready_robot_count",
        "error_code",
        "error_type",
    }
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        stage = raw.get("stage")
        duration = raw.get("duration_ms")
        if not isinstance(stage, str) or not stage.strip():
            continue
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            continue
        item = {
            str(key): value
            for key, value in raw.items()
            if key in allowed_keys
        }
        item["duration_ms"] = round(max(0.0, float(duration)), 2)
        stages.append(item)
        key = str(stage)
        iteration = raw.get("iteration")
        if isinstance(iteration, int) and iteration > 0:
            key += f"[{iteration}]"
        record_kind = raw.get("record_kind")
        if isinstance(record_kind, str) and record_kind:
            key += f":{record_kind}"
        by_stage_ms[key] = round(
            by_stage_ms.get(key, 0.0) + max(0.0, float(duration)),
            2,
        )
    return {"stages": stages, "by_stage_ms": by_stage_ms}


def _mission_run_status_projection(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only bounded control-plane fields needed by operator followers."""

    keys = (
        "run_id",
        "mission_id",
        "status",
        "run_status",
        "terminal",
        "created_at",
        "updated_at",
        "plan_artifact_id",
        "plan_digest",
        "plan_source",
        "correction_count",
        "report_status",
        "report_pending",
        "report_started_at",
        "report_finished_at",
        "report_duration_ms",
        "report_error",
    )
    projected = {key: result[key] for key in keys if key in result}
    nested_result = result.get("result")
    if isinstance(nested_result, Mapping):
        nested_status = nested_result.get("status")
        if isinstance(nested_status, str):
            projected["result_status"] = nested_status
        # Operator-facing failure causes stay bounded but must survive the
        # projection, otherwise a failed mission reports no reason at all.
        reasons = nested_result.get("failure_reasons")
        if isinstance(reasons, list) and reasons:
            projected["failure_reasons"] = [
                str(reason) for reason in reasons[:5]
            ]
        message = nested_result.get("message")
        if isinstance(message, str) and message.strip():
            projected["message"] = message.strip()[:500]
    final_report = result.get("final_report")
    if isinstance(final_report, Mapping):
        projected["report_available"] = True
        report_status = final_report.get("status")
        if isinstance(report_status, str):
            projected["report_status"] = report_status
    report_error = result.get("report_error")
    if isinstance(report_error, str):
        projected["report_error"] = _bounded_stream_text(report_error)
    error = result.get("error")
    if isinstance(error, str):
        projected["error"] = _bounded_stream_text(error)
    return projected


def _bounded_stream_text(value: str, *, limit: int = 2048) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_positive_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Field '{key}' must be a positive integer.")
    return value


def _required_payload_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlanArtifactError(
            "plan_confirmation_payload_invalid",
            f"Field '{key}' must be a non-empty string.",
        )
    return value.strip()


def _required_payload_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise PlanArtifactError(
            "plan_confirmation_payload_invalid",
            f"Field '{key}' must be a positive integer.",
        )
    return value


def _required_payload_string_list(
    payload: Mapping[str, Any],
    key: str,
) -> list[str]:
    value = payload.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise PlanArtifactError(
            "plan_confirmation_payload_invalid",
            f"Field '{key}' must be a non-empty string list.",
        )
    return [item.strip() for item in value]


def _operator_id(operator: OperatorContext | None) -> str:
    if operator is not None and operator.operator_id.strip():
        return operator.operator_id.strip()
    return "unknown-operator"


def _planning_dialogue_plan_error(
    error: PlanningDialogueError,
) -> PlanArtifactError:
    return PlanArtifactError(error.code, str(error))


def _presence_from_readiness_snapshot(
    snapshot: Mapping[str, Any],
    *,
    registry: RobotRegistry,
) -> dict[str, dict[str, Any]]:
    """Project the same readiness evidence used by the preview admission gate."""

    by_robot: dict[str, Mapping[str, Any]] = {}
    values = snapshot.get("robot_readiness")
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, Mapping):
                continue
            robot_id = item.get("robot_id")
            readiness = item.get("readiness")
            if isinstance(robot_id, str) and isinstance(readiness, Mapping):
                by_robot[robot_id] = readiness

    presence: dict[str, dict[str, Any]] = {}
    for entry in registry.enabled_entries(include_stale=True):
        evidence = by_robot.get(entry.robot_id, {})
        value = evidence.get("value")
        value = value if isinstance(value, Mapping) else {}
        status = value.get("status")
        freshness = evidence.get("freshness")
        presence[entry.robot_id] = {
            "robot_id": entry.robot_id,
            "online": status == "online" and freshness == "fresh",
            "stale": status == "stale" or freshness == "stale",
            "last_seen_at": value.get("last_seen_at"),
            "state": (
                dict(value["state"])
                if isinstance(value.get("state"), Mapping)
                else {}
            ),
        }
    return presence


def _target_binding_state_from_readiness_snapshot(
    snapshot: Mapping[str, Any],
    *,
    registry: RobotRegistry,
) -> dict[str, list[dict[str, Any]]]:
    """Expose only live map poses in the shape used by target binding.

    The readiness snapshot is the gateway's authoritative live projection,
    while ``validate_plan_target_binding`` consumes the planner's
    ``MissionStateSnapshot``-compatible ``robots`` view.  Keep this adapter
    read-only and deliberately omit robots without a valid map pose so the
    validator fails closed when TF evidence is unavailable.
    """

    presence = _presence_from_readiness_snapshot(snapshot, registry=registry)
    robots: list[dict[str, Any]] = []
    for robot_id, value in presence.items():
        gateway_state = value.get("state")
        if not isinstance(gateway_state, Mapping):
            continue
        robot_state = gateway_state.get("robot_state")
        if not isinstance(robot_state, Mapping):
            continue
        pose = robot_state.get("pose")
        if isinstance(pose, Mapping):
            robots.append({"robot_id": robot_id, "pose": dict(pose)})
    return {"robots": robots}


def _fresh_online_robot_ids(snapshot: Mapping[str, Any]) -> set[str]:
    values = snapshot.get("robot_readiness")
    if not isinstance(values, list):
        raise PlanArtifactError(
            "readiness_unavailable",
            "Gateway readiness contains no robot evidence.",
        )
    result: set[str] = set()
    for item in values:
        if not isinstance(item, Mapping):
            continue
        robot_id = item.get("robot_id")
        evidence = item.get("readiness")
        if not isinstance(robot_id, str) or not isinstance(evidence, Mapping):
            continue
        readiness_value = evidence.get("value")
        if (
            evidence.get("freshness") == "fresh"
            and isinstance(readiness_value, Mapping)
            and readiness_value.get("status") == "online"
        ):
            result.add(robot_id)
    return result


def _plan_artifact_http_status(code: str) -> HTTPStatus:
    if code == "plan_token_invalid":
        return HTTPStatus.NOT_FOUND
    if code == "planning_session_not_found":
        return HTTPStatus.NOT_FOUND
    if code == "planning_session_expired":
        return HTTPStatus.GONE
    if code == "planning_session_operator_mismatch":
        return HTTPStatus.FORBIDDEN
    if code == "real_dispatch_not_authorized":
        return HTTPStatus.LOCKED
    if code in {
        "canonical_planner_unavailable",
        "canonical_planner_failed",
        "plan_artifact_capacity_exhausted",
        "plan_artifact_store_unavailable",
        "readiness_unavailable",
    }:
        return HTTPStatus.SERVICE_UNAVAILABLE
    if code in {
        "canonical_plan_invalid",
        "canonical_plan_command_mismatch",
        "canonical_plan_robot_mismatch",
        "canonical_plan_target_unbound",
    }:
        return HTTPStatus.UNPROCESSABLE_ENTITY
    if code in {
        "plan_command_invalid",
        "plan_preview_payload_invalid",
        "plan_clarification_payload_invalid",
        "planning_dialogue_payload_invalid",
        "plan_confirmation_payload_invalid",
        "plan_confirmation_required",
        "plan_binding_invalid",
        "plan_robot_mismatch",
        "plan_ttl_invalid",
    }:
        return HTTPStatus.BAD_REQUEST
    if code == "sealed_plan_queue_failed":
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.CONFLICT


def _derive_action_from_command(command: str) -> str:
    """Derive a short semantic action label from a natural-language command."""
    if not command:
        return "unknown"
    # Take the first meaningful verb phrase, capped at 40 chars
    action = command.strip().split("，")[0].split(",")[0].strip()
    return action[:40] if action else "unknown"


def _public_token_dict(token: dict[str, Any]) -> dict[str, Any]:
    public = dict(token)
    public.pop("token_hash", None)
    return public


def _mission_submit_status(result: dict[str, Any]) -> HTTPStatus:
    """Pick an HTTP status code for a mission submission result."""
    status = result.get("status")
    if status == "planned" and result.get("mission_id"):
        return HTTPStatus.ACCEPTED
    if status in ("no_planner", "no_robots", "unauthorized"):
        return HTTPStatus.BAD_REQUEST
    return HTTPStatus.ACCEPTED if result.get("mission_id") else HTTPStatus.BAD_REQUEST


def _mission_trace_status_from_run(run_status: str) -> str:
    return {
        "completed": "succeeded",
        "cancelled": "cancelled",
        "escalated": "escalated",
        "blocked": "failed",
        "failed": "failed",
        "timed_out": "failed",
        "lost": "failed",
    }.get(run_status, run_status or "running")


def _sse_after_sequence(
    handler: BaseHTTPRequestHandler,
    parsed: Any,
) -> int | None:
    raw = handler.headers.get("Last-Event-ID")
    if raw is None:
        params = parse_qs(parsed.query)
        raw = params.get("cursor", [None])[0] or params.get("after_sequence", [None])[0]
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
