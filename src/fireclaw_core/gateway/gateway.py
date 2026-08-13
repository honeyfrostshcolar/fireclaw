from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import errno
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from math import isfinite
from pathlib import Path
from queue import Empty as QueueEmpty, Full as QueueFull, Queue
import sqlite3
import threading
import time
import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from fireclaw_core.gateway.method_scopes import authorize_method

from fireclaw_core.approval.execution_authorization import (
    ExecutionAuthorization,
    HmacExecutionAuthorizationAuthority,
    VerifiedExecutionAuthorization,
    authorized_action,
    execution_scope_hash,
)
from fireclaw_core.infra.runtime_state import (
    SqliteAgentLoopCheckpointStore,
    SqliteAuthoritativeRuntimeStore,
    SqliteEventLedger,
    SqliteResourceLeaseManager,
    SqliteTaskQueue,
    ResourceAdmissionRecoveryError,
    StaleRuntimeStateWrite,
)
from fireclaw_core.monitoring.stream_events import EventBus, StreamEvent, TelemetryTracker

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.computer_tools import ComputerSandbox
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.gateway.auth import (
    AuthenticatedGatewayPrincipal,
    authenticate_gateway_request,
    resolve_gateway_api_token,
    validate_gateway_bind,
)
from fireclaw_core.gateway.network_security import (
    GatewayNetworkPolicy,
    GatewayRequestBodyError,
    GatewayRequestGuard,
    gateway_network_policy_from_config,
    read_json_object_body,
)
from fireclaw_core.gateway.transport import (
    GatewayTlsServerConfig,
    create_gateway_http_server,
    gateway_scheme,
)
from fireclaw_core.gateway.control import AuthorizationRequest, ControlPolicy, OperatorContext, operator_from_payload
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
from fireclaw_core.memory.robot_memory import RobotMemoryRecorder
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
from fireclaw_core.memory.entity_memory import EntityMemoryService
from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline
from fireclaw_core.memory.entity_tools import EntityMemoryTools
from fireclaw_core.memory.reconciliation import EmbodiedMemoryReplicationExporter
from fireclaw_core.planner.planner import Plan, PlanningResult, PlanStep
from fireclaw_core.planner.planner_builder import build_provider_runtime
from fireclaw_core.agent.robot_agent import (
    DeterministicRobotAgentPlanner,
    RobotAgentRuntime,
)
from fireclaw_core.agent.robot_deliberation import (
    LLMRobotAgentDecisionPolicy,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.agent.skill_inventory import build_robot_skill_inventory
from fireclaw_core.agent.robot_tools import build_robot_skill_tools
from fireclaw_core.execution.runtime_config import ADAPTER_CHOICES, create_robot_adapter
from fireclaw_core.task.task_contract import StructuredRobotTask, validate_structured_robot_task
from fireclaw_core.task.task_state import project_task_state
from fireclaw_core.task.terminal_outcome import (
    ROBOT_TASK_TERMINAL_EVENT_TYPES,
    build_robot_task_terminal_outcome,
    robot_task_terminal_status_from_event,
    robot_task_terminal_status_from_trace,
)
from fireclaw_core.policy.deployment import (
    DeploymentProfile,
    SandboxProfile,
    deployment_profile_from_config,
    validate_robot_deployment_binding,
)
from fireclaw_core.ros.ros1_config import Ros1DiagnosticsConfig
from fireclaw_core.ros.ros1_diagnostics import Ros1DiagnosticsBackend
from fireclaw_plugin_sdk import (
    HARDWARE_STOP_EVIDENCE_CLASS,
    STOP_EVIDENCE_SERVICE_PREFIX,
)


_RESOURCE_RECOVERY_REQUEST_TTL_SECONDS = 300
_STOP_EVIDENCE_MAX_VALIDITY_SECONDS = 30
_HARDWARE_STOP_EVIDENCE_SCHEMA_VERSION = 1
_HARDWARE_STOP_MINIMUM_SAMPLES = 3
_HARDWARE_STOP_MINIMUM_HOLD_SECONDS = 0.75
_HARDWARE_STOP_MAX_ACTUATOR_VELOCITY = 0.02
_HARDWARE_STOP_MAX_LINEAR_VELOCITY = 0.02
_HARDWARE_STOP_MAX_ANGULAR_VELOCITY = 0.05


def _bounded_stop_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    normalized = float(value)
    if not isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return normalized


def _stop_sample_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _unique_stop_names(value: Any, field_name: str) -> set[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} contains an invalid name")
        names.append(item.strip())
    if len(names) != len(set(names)):
        raise ValueError(f"{field_name} contains duplicate names")
    return set(names)


def _default_robot_deployment_profile() -> DeploymentProfile:
    workspace_root = Path("data/fireclaw-sandbox/robot-agent")
    return DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=SandboxProfile(
            workspace_root=workspace_root,
            allowed_workspace_roots=(workspace_root,),
        ),
    )


@dataclass(frozen=True)
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    adapter: str = "dry-run"
    robot_id: str = "fireclaw-gateway"
    ros1_config_path: str | None = None
    memory_path: str = "memory/fireclaw-gateway.jsonl"
    event_path: str = "memory/fireclaw-gateway-events.jsonl"
    task_queue_path: str = "memory/fireclaw-gateway-tasks.jsonl"
    robot_agent_checkpoint_path: str | None = None
    runtime_state_path: str | None = None
    dry_run: bool = True
    available_sensors: tuple[str, ...] = ()
    default_session_id: str = "default"
    max_active_execution_tasks: int = 1
    authorization_expiry_seconds: int = 300
    api_token: str | None = None
    tls: GatewayTlsServerConfig = field(default_factory=GatewayTlsServerConfig)
    network: GatewayNetworkPolicy = field(default_factory=GatewayNetworkPolicy)
    robot_agent_enabled: bool = False
    robot_agent_planner: str = "deterministic"
    robot_agent_provider_base_url: str | None = None
    robot_agent_provider_api_key: str | None = None
    robot_agent_model: str | None = None
    robot_agent_model_catalog_path: str | None = None
    extension_paths: tuple[str, ...] = ("extensions",)
    plugin_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    robot_profile_path: str | None = None
    embodied_memory_path: str | None = None
    embodied_memory_index_path: str | None = None
    embodied_runtime_mode: str | None = None
    deployment_profile: DeploymentProfile = field(
        default_factory=_default_robot_deployment_profile
    )


def resolve_gateway_storage_namespace(config: GatewayConfig) -> GatewayConfig:
    """Keep implicit gateway state files in the configured memory namespace."""

    if config.memory_path == "memory/fireclaw-gateway.jsonl":
        return config
    memory_path = Path(config.memory_path)
    stem = memory_path.stem
    updates: dict[str, str] = {}
    if config.event_path == "memory/fireclaw-gateway-events.jsonl":
        updates["event_path"] = str(
            memory_path.with_name(f"{stem}-events.jsonl")
        )
    if config.task_queue_path == "memory/fireclaw-gateway-tasks.jsonl":
        updates["task_queue_path"] = str(
            memory_path.with_name(f"{stem}-tasks.jsonl")
        )
    if config.runtime_state_path is None:
        updates["runtime_state_path"] = str(
            memory_path.with_name(f"{stem}-runtime.sqlite3")
        )
    return replace(config, **updates) if updates else config


@dataclass
class TaskControl:
    task_id: str
    session_id: str
    command: str
    started_at: str
    structured_task: dict[str, Any] | None = None
    execution_authorization: ExecutionAuthorization | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class EmergencyStopState:
    active: bool = False
    reason: str | None = None
    operator_id: str | None = None
    activated_at: str | None = None
    task_id: str | None = None


def load_gateway_robot_profile(config: GatewayConfig):
    """Load robot profile from config path, returning None when no path is set."""
    if not config.robot_profile_path:
        return None
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    return load_robot_capability_profile(config.robot_profile_path)


def resolve_gateway_config_with_profile(config: GatewayConfig, profile=None) -> GatewayConfig:
    """Apply robot profile fields to gateway config before adapter/store construction."""
    if not config.robot_profile_path:
        return config
    if profile is None:
        profile = load_gateway_robot_profile(config)
    return replace(
        config,
        adapter=profile.adapter,
        robot_id=profile.robot_id,
        ros1_config_path=profile.ros1_config,
        memory_path=str(profile.memory_path),
        event_path=str(profile.event_path),
        task_queue_path=str(profile.task_queue_path),
    )


def apply_gateway_dry_run_to_robot(robot: Any, dry_run: bool) -> None:
    """Synchronize gateway dry-run mode onto adapters that expose a dry_run flag."""
    if hasattr(robot, "dry_run"):
        try:
            setattr(robot, "dry_run", dry_run)
        except Exception:
            logging.warning("Failed to apply gateway dry_run=%s to robot adapter", dry_run, exc_info=True)


def attach_profile_sensor_discovery(robot: Any, profile: Any) -> None:
    """Attach sensor discovery backend to the robot adapter when the profile enables it."""
    if profile is None:
        return
    if not profile.sensor_discovery.enabled:
        return
    from fireclaw_core.sensors.backends import create_profile_sensor_discovery_backend

    backend = create_profile_sensor_discovery_backend(profile)
    if backend is None:
        return
    setattr(robot, "sensor_discovery", backend)


class FireClawGateway:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        replication_security: Any | None = None,
        ros_diagnostics_backend: Ros1DiagnosticsBackend | None = None,
        computer_sandbox: ComputerSandbox | None = None,
        plugin_services: Mapping[str, Any] | None = None,
        runtime_state: SqliteAuthoritativeRuntimeStore | None = None,
    ) -> None:
        self._process_working_directory = Path.cwd().resolve(strict=False)
        self.replication_security = replication_security
        self._plugin_services = dict(plugin_services or {})
        self._gateway_plugin_host: Any | None = None
        self.robot_profile = load_gateway_robot_profile(config)
        resolved_config = resolve_gateway_config_with_profile(config, self.robot_profile)
        resolved_config = resolve_gateway_storage_namespace(resolved_config)
        self.config = resolved_config
        self._network_guard = GatewayRequestGuard(
            resolved_config.network,
            configured_host=resolved_config.host,
            tls_enabled=resolved_config.tls.enabled,
        )
        validate_robot_deployment_binding(
            resolved_config.deployment_profile,
            dry_run=resolved_config.dry_run,
            embodied_runtime_mode=resolved_config.embodied_runtime_mode,
        )
        self.computer_sandbox = computer_sandbox
        if (
            self.computer_sandbox is None
            and resolved_config.deployment_profile.sandbox.enabled
        ):
            self.computer_sandbox = ComputerSandbox(
                resolved_config.deployment_profile.sandbox
            )
        self.robot = create_robot_adapter(resolved_config.adapter, resolved_config.robot_id, config_path=resolved_config.ros1_config_path)
        apply_gateway_dry_run_to_robot(self.robot, resolved_config.dry_run)
        attach_profile_sensor_discovery(self.robot, self.robot_profile)
        self.ros_diagnostics_backend = ros_diagnostics_backend
        if (
            self.ros_diagnostics_backend is None
            and resolved_config.adapter == "ros1"
        ):
            diagnostics_config = getattr(
                getattr(self.robot, "config", None),
                "diagnostics",
                Ros1DiagnosticsConfig(),
            )
            self.ros_diagnostics_backend = (
                Ros1DiagnosticsBackend.from_config(
                    diagnostics_config,
                    robot_id=resolved_config.robot_id,
                )
            )
        self._validate_robot_profile()
        self.memory = JsonlMemoryStore(resolved_config.memory_path)
        self.embodied_memory: EmbodiedMemoryStore | None = None
        self.embodied_working_memory: EmbodiedWorkingMemory | None = None
        self._safety_memory_producer: EmbodiedMemoryProducer | None = None
        self._skill_memory_producer: EmbodiedMemoryProducer | None = None
        self.robot_memory_recorder: RobotMemoryRecorder | None = None
        self.entity_memory: EntityMemoryService | None = None
        self.entity_extraction_pipeline: EntityExtractionPipeline | None = None
        self.entity_memory_tools: EntityMemoryTools | None = None
        self.memory_replication_exporter: EmbodiedMemoryReplicationExporter | None = None
        if resolved_config.embodied_runtime_mode is not None:
            if resolved_config.embodied_memory_path is None:
                raise ValueError("embodied_runtime_mode requires embodied_memory_path")
            self.embodied_memory = EmbodiedMemoryStore(
                resolved_config.embodied_memory_path,
                index_path=resolved_config.embodied_memory_index_path,
            )
            self.embodied_working_memory = EmbodiedWorkingMemory()
            self._safety_memory_producer = EmbodiedMemoryProducer(
                self.embodied_memory,
                producer_type="safety_gate",
                producer_id=f"{resolved_config.robot_id}:safety-gate",
                working_memory=self.embodied_working_memory,
            )
            self._skill_memory_producer = EmbodiedMemoryProducer(
                self.embodied_memory,
                producer_type="skill_runtime",
                producer_id=f"{resolved_config.robot_id}:skill-runtime",
                working_memory=self.embodied_working_memory,
            )
            self.entity_memory = EntityMemoryService(
                store=self.embodied_memory,
                resolver_producer=EmbodiedMemoryProducer(
                    self.embodied_memory,
                    producer_type="entity_resolver",
                    producer_id=f"{resolved_config.robot_id}:entity-resolver",
                    working_memory=self.embodied_working_memory,
                ),
                operator_producer=EmbodiedMemoryProducer(
                    self.embodied_memory,
                    producer_type="approval_runtime",
                    producer_id=f"{resolved_config.robot_id}:entity-approval",
                    working_memory=self.embodied_working_memory,
                ),
                runtime_mode=resolved_config.embodied_runtime_mode,
            )
            self.entity_extraction_pipeline = EntityExtractionPipeline(
                store=self.embodied_memory,
                entity_memory=self.entity_memory,
                runtime_mode=resolved_config.embodied_runtime_mode,
            )
            self.entity_memory_tools = EntityMemoryTools(self.entity_memory)
            self.memory_replication_exporter = EmbodiedMemoryReplicationExporter(
                store=self.embodied_memory,
                source_store_id=f"robot:{resolved_config.robot_id}",
                source_robot_id=resolved_config.robot_id,
            )
            self.robot_memory_recorder = RobotMemoryRecorder(
                robot_producer=EmbodiedMemoryProducer(
                    self.embodied_memory,
                    producer_type="robot_adapter",
                    producer_id=f"{resolved_config.robot_id}:robot-adapter",
                    working_memory=self.embodied_working_memory,
                ),
                sensor_producer=EmbodiedMemoryProducer(
                    self.embodied_memory,
                    producer_type="sensor_adapter",
                    producer_id=f"{resolved_config.robot_id}:sensor-adapter",
                    working_memory=self.embodied_working_memory,
                ),
                runtime_mode=resolved_config.embodied_runtime_mode,
                entity_extraction_pipeline=self.entity_extraction_pipeline,
            )
        runtime_state_path = (
            resolved_config.runtime_state_path
            or str(
                Path(resolved_config.memory_path).with_name(
                    f"{Path(resolved_config.memory_path).stem}"
                    "-runtime.sqlite3"
                )
            )
        )
        if runtime_state is not None:
            expected_state_path = Path(runtime_state_path).resolve(
                strict=False
            )
            supplied_state_path = runtime_state.path.resolve(strict=False)
            if supplied_state_path != expected_state_path:
                raise ValueError(
                    "Injected runtime_state path must match "
                    "GatewayConfig.runtime_state_path"
                )
        self.runtime_state = runtime_state or SqliteAuthoritativeRuntimeStore(
            runtime_state_path
        )
        self.events = SqliteEventLedger(
            self.runtime_state,
            audit_path=resolved_config.event_path,
        )
        self.task_queue = SqliteTaskQueue(
            self.runtime_state,
            audit_path=resolved_config.task_queue_path,
        )
        checkpoint_path = (
            resolved_config.robot_agent_checkpoint_path
            or f"{resolved_config.task_queue_path}.agent-loops.jsonl"
        )
        self.agent_loop_checkpoints = SqliteAgentLoopCheckpointStore(
            self.runtime_state,
            audit_path=checkpoint_path,
        )
        self.resource_leases = SqliteResourceLeaseManager(
            self.runtime_state
        )
        self._stop_evidence_providers = (
            self._discover_stop_evidence_providers()
        )
        self.execution_authorization_authority = (
            HmacExecutionAuthorizationAuthority(
                issuer_id=f"robot-gateway:{resolved_config.robot_id}",
                key_id="robot-local-v1",
                secret=self.runtime_state.get_or_create_secret(
                    "execution_authorization_hmac"
                ),
            )
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._task_threads: dict[str, threading.Thread] = {}
        self._task_controls: dict[str, TaskControl] = {}
        self._task_lock = threading.RLock()
        self._runtime_storage_fault: dict[str, Any] | None = None
        persisted_emergency = self.resource_leases.admission_state()
        self._emergency_stop = EmergencyStopState(
            active=bool(persisted_emergency.get("closed")),
            reason=(
                str(persisted_emergency["reason"])
                if persisted_emergency.get("reason") is not None
                else None
            ),
            activated_at=(
                str(persisted_emergency["closed_at"])
                if persisted_emergency.get("closed_at") is not None
                else None
            ),
            task_id=(
                str(persisted_emergency["task_id"])
                if persisted_emergency.get("task_id") is not None
                else None
            ),
        )
        self._event_bus = EventBus()
        self._telemetry = TelemetryTracker()
        self.robot_agent_runtime = self._build_robot_agent_runtime()
        self._reconcile_stale_task_queue_records()

    def _validate_robot_profile(self) -> None:
        if self.robot_profile is None:
            return
        from fireclaw_core.agent.robot_profile import validate_robot_capability_profile
        from fireclaw_core.execution.skills import create_default_skill_registry
        from fireclaw_core.plugin.extension_loader import load_fireclaw_extensions
        from fireclaw_core.plugin.plugin_host import FireClawPluginHost

        host = FireClawPluginHost()
        load_fireclaw_extensions(
            host,
            self.config.extension_paths,
            mode=self.config.deployment_profile.mode,
            role="robot_agent",
            plugin_configs=self.config.plugin_configs,
            services=self._extension_services(),
        )
        registry = create_default_skill_registry(self.robot, plugin_host=host)
        errors = validate_robot_capability_profile(self.robot_profile, registry)
        if errors:
            raise ValueError("Invalid robot profile: " + "; ".join(errors))
        self._gateway_plugin_host = host

    def _discover_stop_evidence_providers(
        self,
    ) -> tuple[dict[str, Any], ...]:
        providers: dict[str, dict[str, Any]] = {}

        # Services supplied by Gateway assembly are already inside the trusted
        # host boundary. They are useful for hardware-specific witnesses and
        # deterministic acceptance tests.
        for service_id, service in self._plugin_services.items():
            if not str(service_id).startswith(STOP_EVIDENCE_SERVICE_PREFIX):
                continue
            if not callable(getattr(service, "collect_stop_evidence", None)):
                continue
            providers[str(service_id)] = self._stop_evidence_descriptor(
                service_id=str(service_id),
                owner_plugin_id="gateway-injected",
                service=service,
            )

        host = self._gateway_plugin_host
        if host is None:
            return tuple(providers[key] for key in sorted(providers))
        records = {record.plugin_id: record for record in host.records()}
        for contribution in host.contributions("service"):
            service_id = str(contribution.contribution_id)
            if not service_id.startswith(STOP_EVIDENCE_SERVICE_PREFIX):
                continue
            record = records.get(contribution.owner_plugin_id)
            if record is None or record.trust_level not in {"builtin", "trusted"}:
                continue
            service = contribution.value
            if not callable(getattr(service, "collect_stop_evidence", None)):
                continue
            providers.setdefault(
                service_id,
                self._stop_evidence_descriptor(
                    service_id=service_id,
                    owner_plugin_id=contribution.owner_plugin_id,
                    service=service,
                ),
            )
        return tuple(providers[key] for key in sorted(providers))

    @staticmethod
    def _stop_evidence_descriptor(
        *,
        service_id: str,
        owner_plugin_id: str,
        service: Any,
    ) -> dict[str, Any]:
        provider_id_value = getattr(service, "provider_id", None)
        evidence_class_value = getattr(service, "evidence_class", None)
        provider_id = (
            provider_id_value.strip()
            if isinstance(provider_id_value, str) and provider_id_value.strip()
            else service_id.removeprefix(STOP_EVIDENCE_SERVICE_PREFIX)
        )
        evidence_class = (
            evidence_class_value.strip()
            if isinstance(evidence_class_value, str)
            and evidence_class_value.strip()
            else None
        )
        hardware_owned = getattr(service, "hardware_owned", None) is True
        return {
            "service_id": service_id,
            "owner_plugin_id": owner_plugin_id,
            "provider_id": provider_id,
            "evidence_class": evidence_class,
            "hardware_owned": hardware_owned,
            "qualified_for_real": (
                hardware_owned
                and evidence_class == HARDWARE_STOP_EVIDENCE_CLASS
            ),
            "service": service,
        }

    def _extension_services(self) -> dict[str, Any]:
        """Build the generic host service bag without naming domain Plugins."""

        services = dict(self._plugin_services)
        services["adapter"] = self.config.adapter
        services.setdefault(
            "fireclaw.agent-tools.computer.sandbox",
            self.computer_sandbox,
        )
        services.setdefault(
            "fireclaw.agent-tools.ros1-diagnostics.backend",
            self.ros_diagnostics_backend,
        )
        return services

    @property
    def base_url(self) -> str:
        scheme = gateway_scheme(self.config.tls)
        if self._server is None:
            return f"{scheme}://{self.config.host}:{self.config.port}"
        host, port = self._server.server_address
        return f"{scheme}://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        validate_gateway_bind(self.config.host, self.config.api_token)
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
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for thread in list(self._task_threads.values()):
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def run_agent(self, command: str, session_id: str | None = None) -> dict[str, Any]:
        task_id = f"task-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        created_at = datetime.now(timezone.utc).isoformat()
        self.task_queue.create(
            task_id=task_id,
            session_id=resolved_session_id,
            command=command,
            created_at=created_at,
        )
        self.task_queue.update(task_id, status="running", started_at=created_at)
        return self._execute_agent_task(
            command=command,
            session_id=resolved_session_id,
            task_id=task_id,
            record_received=True,
        )

    def submit_agent(
        self,
        command: str,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        dedupe_key: str | None = None,
        structured_task: dict[str, Any] | None = None,
        execution_authorization: ExecutionAuthorization | None = None,
    ) -> dict[str, Any]:
        task_id = f"task-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        resolved_operator = operator or operator_from_payload(None)
        control_decision = ControlPolicy().evaluate(resolved_operator, "task.submit")
        try:
            admission_state = self.resource_leases.admission_state()
        except (sqlite3.Error, OSError) as exc:
            return self._runtime_storage_unavailable_result(
                exc,
                session_id=resolved_session_id,
            )
        if admission_state.get("closed"):
            return {
                "status": "blocked",
                "session_id": resolved_session_id,
                "message": "机器人处于急停资源冻结状态，拒绝新任务。",
                "resource_admission": admission_state,
            }
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.runtime_state.transaction():
                duplicate = self.task_queue.find_non_terminal_by_dedupe_key(
                    dedupe_key
                )
                if duplicate is not None:
                    self._runtime_storage_fault = None
                    return {
                        "status": "duplicate",
                        "task_id": duplicate.task_id,
                        "session_id": duplicate.session_id,
                        "dedupe_key": dedupe_key,
                        "message": "任务已存在，返回现有未完成任务。",
                    }
                self.task_queue.create(
                    task_id=task_id,
                    session_id=resolved_session_id,
                    command=command,
                    created_at=started_at,
                    dedupe_key=dedupe_key,
                )
        except (sqlite3.Error, OSError) as exc:
            return self._runtime_storage_unavailable_result(
                exc,
                session_id=resolved_session_id,
                task_id=task_id,
            )
        if (
            execution_authorization is None
            and isinstance(structured_task, dict)
            and isinstance(
                structured_task.get("execution_authorization"),
                dict,
            )
        ):
            execution_authorization = ExecutionAuthorization.from_dict(
                structured_task["execution_authorization"]
            )
        control = TaskControl(
            task_id=task_id,
            session_id=resolved_session_id,
            command=command,
            started_at=started_at,
            structured_task=structured_task,
            execution_authorization=execution_authorization,
        )
        try:
            with self._task_lock:
                if len(self._task_controls) >= self.config.max_active_execution_tasks:
                    active = self._active_task_summaries_locked()
                    first_active = active[0] if active else {}
                    self.task_queue.update(
                        task_id,
                        status="failed",
                        ended_at=datetime.now(timezone.utc).isoformat(),
                        error="Gateway execution capacity is full.",
                    )
                    self._runtime_storage_fault = None
                    return {
                        "status": "busy",
                        "message": "机器人当前已有任务在执行，请等待当前任务结束或取消后再提交。",
                        "active_task_id": first_active.get("task_id"),
                        "active_tasks": active,
                        "capacity": self._task_capacity_locked(),
                    }
                self._task_controls[task_id] = control
            self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type="task.received",
                payload={"command": command},
            )
            self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type="operator.identified",
                payload=resolved_operator.to_dict(),
            )
            self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type="control.decision",
                payload=control_decision.to_dict(),
            )
        except (sqlite3.Error, OSError) as exc:
            return self._runtime_storage_unavailable_result(
                exc,
                session_id=resolved_session_id,
                task_id=task_id,
            )
        if control_decision.status != "allow":
            with self._task_lock:
                self._task_controls.pop(task_id, None)
            try:
                self.task_queue.update(
                    task_id,
                    status="denied",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    error="Operator is not authorized to submit tasks.",
                )
            except (sqlite3.Error, OSError) as exc:
                return self._runtime_storage_unavailable_result(
                    exc,
                    session_id=resolved_session_id,
                    task_id=task_id,
                )
            self._runtime_storage_fault = None
            return {
                "status": "denied",
                "task_id": task_id,
                "session_id": resolved_session_id,
                "message": "操作员没有权限提交任务。",
                "control": control_decision.to_dict(),
            }

        self._runtime_storage_fault = None
        self._start_task_worker(
            control,
            resolved_operator,
        )
        return {
            "status": "accepted",
            "task_id": task_id,
            "session_id": resolved_session_id,
            "message": "任务已接收，正在后台执行。",
        }

    def _runtime_storage_unavailable_result(
        self,
        exc: sqlite3.Error | OSError,
        *,
        session_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Fail task admission closed when authoritative storage cannot write."""

        occurred_at = datetime.now(timezone.utc).isoformat()
        code = _runtime_storage_error_code(exc)
        self._runtime_storage_fault = {
            "status": "degraded",
            "code": code,
            "occurred_at": occurred_at,
            "error_type": type(exc).__name__,
        }
        record_state = "not_created"
        if task_id is not None:
            with self._task_lock:
                self._task_controls.pop(task_id, None)
            try:
                record = self.task_queue.get(task_id)
            except (sqlite3.Error, OSError):
                record = None
                record_state = "unknown"
            if record is not None:
                record_state = record.status
                if not record.is_terminal:
                    try:
                        self.task_queue.update(
                            task_id,
                            status="failed",
                            ended_at=occurred_at,
                            error=(
                                "Task execution did not start because "
                                f"runtime storage was unavailable ({code})."
                            ),
                        )
                    except (sqlite3.Error, OSError, StaleRuntimeStateWrite):
                        pass
                    try:
                        persisted = self.task_queue.get(task_id)
                    except (sqlite3.Error, OSError):
                        persisted = None
                    record_state = (
                        persisted.status if persisted is not None else "unknown"
                    )
        result: dict[str, Any] = {
            "status": "unavailable",
            "code": code,
            "session_id": session_id,
            "retryable": True,
            "task_execution_started": False,
            "task_record_state": record_state,
            "message": (
                "权威运行状态暂时不可写，任务没有启动。"
                "修复存储后请使用相同 dedupe_key 重试。"
            ),
        }
        if task_id is not None:
            result["task_id"] = task_id
        return result

    def _start_task_worker(
        self,
        control: TaskControl,
        operator: OperatorContext,
        *,
        resumed: bool = False,
    ) -> None:
        task_id = control.task_id

        def worker() -> None:
            try:
                if resumed:
                    self.task_queue.update(task_id, status="running")
                else:
                    self.task_queue.update(
                        task_id,
                        status="running",
                        started_at=datetime.now(timezone.utc).isoformat(),
                    )
                if resumed:
                    self._append_event(
                        task_id=task_id,
                        session_id=control.session_id,
                        type="task.resume_started",
                        payload={
                            "status": "running",
                            "task_id": task_id,
                        },
                    )
                self._publish_stream_event(
                    "task.running",
                    task_id=task_id,
                )
                self._execute_agent_task(
                    command=control.command,
                    session_id=control.session_id,
                    task_id=task_id,
                    record_received=False,
                    cancellation_requested=control.cancel_event.is_set,
                    operator=operator,
                    structured_task=control.structured_task,
                    execution_authorization=(
                        control.execution_authorization
                    ),
                )
            except Exception as exc:
                failed_result = {
                    "status": "failed",
                    "task_id": task_id,
                    "session_id": control.session_id,
                    "message": str(exc),
                }
                terminal_outcome = build_robot_task_terminal_outcome("failed")
                failed_result["terminal_outcome"] = (
                    terminal_outcome.to_dict()
                )
                with self.runtime_state.transaction():
                    self.task_queue.update(
                        task_id,
                        status="failed",
                        ended_at=datetime.now(
                            timezone.utc
                        ).isoformat(),
                        error=str(exc),
                        result=failed_result,
                    )
                    self._append_event(
                        task_id=task_id,
                        session_id=control.session_id,
                        type="task.failed",
                        payload={
                            "status": "failed",
                            "message": str(exc),
                            "terminal_outcome": (
                                terminal_outcome.to_dict()
                            ),
                            "result": failed_result,
                        },
                    )
            finally:
                with self._task_lock:
                    if self._task_threads.get(task_id) is threading.current_thread():
                        self._task_threads.pop(task_id, None)
                    if self._task_controls.get(task_id) is control:
                        self._task_controls.pop(task_id, None)

        thread = threading.Thread(
            target=worker,
            daemon=True,
            name=f"fireclaw-task-{task_id}",
        )
        with self._task_lock:
            self._task_threads[task_id] = thread
        thread.start()

    def cancel_task(self, task_id: str, operator: OperatorContext | None = None) -> dict[str, Any]:
        resolved_operator = operator or operator_from_payload(None)
        control_decision = ControlPolicy().evaluate(resolved_operator, "task.cancel")
        if control_decision.status == "allow":
            with self._task_lock:
                control = self._task_controls.get(task_id)
                queue_record = self.task_queue.get(task_id)
                if (
                    queue_record is not None
                    and queue_record.status == "awaiting_confirmation"
                ):
                    if control is not None:
                        control.cancel_event.set()
                    decided_at = datetime.now(timezone.utc).isoformat()
                    pending = self._pending_authorization_for_task(
                        session_id=queue_record.session_id,
                        task_id=task_id,
                    )
                    with self.runtime_state.transaction():
                        if pending is not None:
                            try:
                                resolved = (
                                    self.runtime_state
                                    .resolve_authorization_request(
                                        str(pending["request_id"]),
                                        status="cancelled",
                                        decided_by=(
                                            resolved_operator.to_dict()
                                        ),
                                        decided_at=decided_at,
                                    )
                                )
                            except StaleRuntimeStateWrite:
                                resolved = pending
                            self._append_event(
                                task_id=task_id,
                                session_id=queue_record.session_id,
                                type="authorization.cancelled",
                                payload={
                                    "status": "cancelled",
                                    "authorization": resolved,
                                    "cancelled_by": (
                                        resolved_operator.to_dict()
                                    ),
                                    "cancelled_at": decided_at,
                                },
                            )
                        terminal_result = self._terminalize_waiting_task(
                            task_id=task_id,
                            session_id=queue_record.session_id,
                            status="cancelled",
                            message=(
                                "任务在等待确认期间被取消；"
                                "未执行待授权动作。"
                            ),
                            reason_code="cancelled_while_awaiting_confirmation",
                        )
                    return {
                        "status": "cancelled",
                        "task_id": task_id,
                        "session_id": queue_record.session_id,
                        "message": (
                            terminal_result.get("message")
                            if isinstance(terminal_result, dict)
                            else "任务已取消。"
                        ),
                    }
                if control is not None and queue_record is not None and queue_record.is_terminal:
                    control = None
                if control is not None:
                    already_requested = control.cancel_event.is_set()
                    control.cancel_event.set()
                    if not already_requested:
                        self.task_queue.update(
                            task_id,
                            status="cancel_requested",
                        )
                        self._append_event(
                            task_id=task_id,
                            session_id=control.session_id,
                            type="task.cancel_requested",
                            payload={"status": "cancel_requested", "task_id": task_id},
                        )
                    return {
                        "status": "cancel_requested",
                        "task_id": task_id,
                        "session_id": control.session_id,
                        "message": "已请求取消任务，当前 skill 返回后将停止后续步骤。",
                    }
            trace = self.task_trace(task_id)
            if trace.get("result") is not None:
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "message": "任务已经结束，无法取消。",
                }
            return {
                "status": "not_found",
                "task_id": task_id,
                "message": "没有找到正在运行的任务。",
            }
        with self._task_lock:
            control = self._task_controls.get(task_id)
        if control is None:
            return {
                "status": "not_found",
                "task_id": task_id,
                "message": "没有找到正在运行的任务。",
            }
        if control_decision.status != "allow":
            self._append_event(
                task_id=task_id,
                session_id=control.session_id,
                type="task.cancel_denied",
                payload={
                    "status": "denied",
                    "task_id": task_id,
                    "operator": resolved_operator.to_dict(),
                    "control": control_decision.to_dict(),
                },
            )
            return {
                "status": "denied",
                "task_id": task_id,
                "session_id": control.session_id,
                "message": "操作员没有权限取消任务。",
                "control": control_decision.to_dict(),
            }

    def emergency_stop(
        self,
        *,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        task_id = f"emergency-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        resolved_operator = operator or operator_from_payload(None)
        control_decision = ControlPolicy().evaluate(resolved_operator, "emergency.stop")
        requested_payload = {
            "status": "requested",
            "reason": reason,
            "operator": resolved_operator.to_dict(),
            "control": control_decision.to_dict(),
        }
        self._append_event(
            task_id=task_id,
            session_id=resolved_session_id,
            type="emergency_stop.requested",
            payload=requested_payload,
        )
        if control_decision.status != "allow":
            self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type="emergency_stop.denied",
                payload={
                    "status": "denied",
                    "reason": reason,
                    "operator": resolved_operator.to_dict(),
                    "control": control_decision.to_dict(),
                },
            )
            return {
                "status": "denied",
                "task_id": task_id,
                "session_id": resolved_session_id,
                "reason": reason,
                "message": "操作员没有权限触发急停。",
                "control": control_decision.to_dict(),
            }

        activated_at = datetime.now(timezone.utc).isoformat()
        self.resource_leases.close_admission(
            reason=reason,
            task_id=task_id,
            closed_at=activated_at,
        )
        cancelled_task_ids = self._cancel_all_active_tasks(reason=reason)
        robot_result = self.robot.emergency_stop(reason=reason)
        self._emergency_stop = EmergencyStopState(
            active=True,
            reason=reason,
            operator_id=resolved_operator.operator_id,
            activated_at=activated_at,
            task_id=task_id,
        )
        result = {
            "status": "emergency_stopped",
            "task_id": task_id,
            "session_id": resolved_session_id,
            "reason": reason,
            "operator": resolved_operator.to_dict(),
            "control": control_decision.to_dict(),
            "cancelled_task_ids": cancelled_task_ids,
            "robot_result": {
                "ok": robot_result.ok,
                "status": robot_result.status,
                "robot_id": robot_result.robot_id,
                "mode": robot_result.mode,
                "action": robot_result.action,
                "dry_run": robot_result.dry_run,
                "data": dict(robot_result.data),
                "timestamp": robot_result.timestamp,
                "error": robot_result.error,
            },
            "activated_at": activated_at,
        }
        self._append_event(
            task_id=task_id,
            session_id=resolved_session_id,
            type="emergency_stop.activated",
            payload=result,
        )
        return result

    def resource_admission_state(self) -> dict[str, Any]:
        pending = self.resource_leases.pending_recovery_request(
            robot_id=self.config.robot_id
        )
        if pending is not None:
            now = datetime.now(timezone.utc)
            try:
                expires_at = datetime.fromisoformat(str(pending["expires_at"]))
            except (KeyError, TypeError, ValueError):
                expires_at = None
            if expires_at is not None and now >= expires_at:
                expired = self.resource_leases.expire_recovery_request(
                    request_id=str(pending["request_id"]),
                    expired_at=now.isoformat(),
                )
                if expired.get("transitioned") is True:
                    self._append_event(
                        task_id=str(pending["request_id"]),
                        session_id=str(
                            pending.get("session_id")
                            or self.config.default_session_id
                        ),
                        type="resource_admission.recovery_expired",
                        payload={
                            "status": "expired",
                            "request": self._public_recovery_request(expired),
                            "reason_code": "confirmation_deadline_elapsed",
                        },
                    )
                pending = None
        return {
            "robot_id": self.config.robot_id,
            "admission": self.resource_leases.admission_snapshot(),
            "active_leases": [
                lease.to_dict()
                for lease in self.resource_leases.active(
                    robot_id=self.config.robot_id
                )
            ],
            "pending_recovery": (
                self._public_recovery_request(pending)
                if pending is not None
                else None
            ),
            "stop_evidence_providers": [
                {
                    "service_id": item["service_id"],
                    "owner_plugin_id": item["owner_plugin_id"],
                    "provider_id": item["provider_id"],
                    "evidence_class": item["evidence_class"],
                    "hardware_owned": item["hardware_owned"],
                    "qualified_for_real": item["qualified_for_real"],
                }
                for item in self._stop_evidence_providers
            ],
        }

    def request_resource_admission_recovery(
        self,
        *,
        session_id: str | None = None,
        operator: OperatorContext | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        request_id = f"recovery-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        resolved_operator = operator or operator_from_payload(None)
        control = ControlPolicy().evaluate(
            resolved_operator,
            "emergency.recover",
        )
        if control.status != "allow":
            result = {
                "status": "denied",
                "request_id": request_id,
                "session_id": resolved_session_id,
                "message": "操作员没有权限恢复资源准入。",
                "control": control.to_dict(),
            }
            self._append_event(
                task_id=request_id,
                session_id=resolved_session_id,
                type="resource_admission.recovery_denied",
                payload=result,
            )
            return result
        if not isinstance(reason, str) or not reason.strip():
            return {
                "status": "invalid",
                "request_id": request_id,
                "session_id": resolved_session_id,
                "message": "恢复请求必须说明原因。",
                "error_code": "recovery_reason_required",
            }

        frozen = self.resource_leases.admission_snapshot()
        if not frozen.get("closed"):
            result = {
                "status": "not_frozen",
                "request_id": request_id,
                "session_id": resolved_session_id,
                "message": "资源准入当前未冻结。",
                "admission": frozen,
            }
            self._append_event(
                task_id=request_id,
                session_id=resolved_session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result

        evidence = self._collect_stop_evidence(reason=reason.strip())
        if evidence["status"] != "stopped":
            result = {
                "status": "blocked",
                "request_id": request_id,
                "session_id": resolved_session_id,
                "message": "未取得可信且新鲜的现场停止证据，资源准入保持冻结。",
                "error_code": "trusted_stop_evidence_unavailable",
                "admission": frozen,
                "stop_evidence": evidence,
            }
            self._append_event(
                task_id=request_id,
                session_id=resolved_session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result

        requested_at = datetime.now(timezone.utc)
        expires_at = requested_at + timedelta(
            seconds=_RESOURCE_RECOVERY_REQUEST_TTL_SECONDS
        )
        confirmation_phrase = (
            f"RECOVER {self.config.robot_id} {request_id}"
        )
        try:
            with self.runtime_state.transaction():
                request = self.resource_leases.create_recovery_request(
                    {
                        "request_id": request_id,
                        "robot_id": self.config.robot_id,
                        "session_id": resolved_session_id,
                        "reason": reason.strip(),
                        "requested_at": requested_at.isoformat(),
                        "expires_at": expires_at.isoformat(),
                        "confirmation_phrase": confirmation_phrase,
                        "requested_by": resolved_operator.to_dict(),
                        "control": control.to_dict(),
                        "request_stop_evidence": evidence,
                    }
                )
                audit_payload = {
                    "status": "pending_confirmation",
                    "request": self._public_recovery_request(request),
                    "stop_evidence": evidence,
                }
                self._append_event(
                    task_id=request_id,
                    session_id=resolved_session_id,
                    type="resource_admission.recovery_requested",
                    payload=audit_payload,
                )
        except ResourceAdmissionRecoveryError as exc:
            result = {
                "status": "blocked",
                "request_id": request_id,
                "session_id": resolved_session_id,
                "message": str(exc),
                "error_code": exc.code,
                "stop_evidence": evidence,
            }
            self._append_event(
                task_id=request_id,
                session_id=resolved_session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result
        return {
            "status": "pending_confirmation",
            "request_id": request_id,
            "session_id": resolved_session_id,
            "robot_id": self.config.robot_id,
            "reason": reason.strip(),
            "requested_at": request["requested_at"],
            "expires_at": request["expires_at"],
            "confirmation_phrase": confirmation_phrase,
            "frozen_admission": request["frozen_admission"],
            "stop_evidence": evidence,
        }

    def confirm_resource_admission_recovery(
        self,
        *,
        request_id: str,
        confirmation_phrase: str,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        resolved_operator = operator or operator_from_payload(None)
        control = ControlPolicy().evaluate(
            resolved_operator,
            "emergency.recover",
        )
        request = self.resource_leases.recovery_request(request_id)
        session_id = (
            str(request.get("session_id") or self.config.default_session_id)
            if request is not None
            else self.config.default_session_id
        )
        if control.status != "allow":
            result = {
                "status": "denied",
                "request_id": request_id,
                "message": "操作员没有权限恢复资源准入。",
                "control": control.to_dict(),
            }
            self._append_event(
                task_id=request_id or "recovery-unknown",
                session_id=session_id,
                type="resource_admission.recovery_denied",
                payload=result,
            )
            return result
        if request is None:
            return {
                "status": "not_found",
                "request_id": request_id,
                "message": "恢复请求不存在。",
                "error_code": "recovery_request_not_found",
            }
        if request.get("status") != "pending":
            return {
                "status": "blocked",
                "request_id": request_id,
                "message": "恢复请求已不再等待确认。",
                "error_code": "recovery_request_not_pending",
                "request": self._public_recovery_request(request),
            }
        expected_phrase = str(request.get("confirmation_phrase") or "")
        if not expected_phrase or not secrets.compare_digest(
            confirmation_phrase,
            expected_phrase,
        ):
            result = {
                "status": "denied",
                "request_id": request_id,
                "message": "恢复确认短语不匹配。",
                "error_code": "recovery_confirmation_mismatch",
            }
            self._append_event(
                task_id=request_id,
                session_id=session_id,
                type="resource_admission.recovery_confirmation_denied",
                payload=result,
            )
            return result

        now = datetime.now(timezone.utc)
        try:
            request_expiry = datetime.fromisoformat(str(request["expires_at"]))
        except (KeyError, TypeError, ValueError):
            request_expiry = now
        if now >= request_expiry:
            self.resource_leases.expire_recovery_request(
                request_id=request_id,
                expired_at=now.isoformat(),
            )
            result = {
                "status": "expired",
                "request_id": request_id,
                "message": "恢复请求已过期，请重新采集停止证据。",
                "error_code": "recovery_request_expired",
            }
            self._append_event(
                task_id=request_id,
                session_id=session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result

        evidence = self._collect_stop_evidence(
            reason=str(request.get("reason") or "admission recovery")
        )
        if evidence["status"] != "stopped":
            result = {
                "status": "blocked",
                "request_id": request_id,
                "message": "确认时未取得新的现场停止证据，资源准入保持冻结。",
                "error_code": "trusted_stop_evidence_unavailable",
                "stop_evidence": evidence,
            }
            self._append_event(
                task_id=request_id,
                session_id=session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result

        confirmed_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.runtime_state.transaction():
                recovered = self.resource_leases.recover_admission(
                    request_id=request_id,
                    confirmation_phrase=confirmation_phrase,
                    confirmed_by=resolved_operator.to_dict(),
                    confirmed_at=confirmed_at,
                    stop_evidence=evidence,
                )
                result = {
                    "status": "recovered",
                    "request_id": request_id,
                    "robot_id": self.config.robot_id,
                    "confirmed_at": confirmed_at,
                    "confirmed_by": resolved_operator.to_dict(),
                    "admission": recovered["admission"],
                    "stop_evidence": evidence,
                }
                self._append_event(
                    task_id=request_id,
                    session_id=session_id,
                    type="resource_admission.recovered",
                    payload=result,
                )
        except ResourceAdmissionRecoveryError as exc:
            result = {
                "status": "blocked",
                "request_id": request_id,
                "message": str(exc),
                "error_code": exc.code,
                "stop_evidence": evidence,
            }
            self._append_event(
                task_id=request_id,
                session_id=session_id,
                type="resource_admission.recovery_blocked",
                payload=result,
            )
            return result

        self._emergency_stop = EmergencyStopState(active=False)
        return result

    def _collect_stop_evidence(
        self,
        *,
        reason: str,
    ) -> dict[str, Any]:
        collected_at = datetime.now(timezone.utc)
        reports: list[dict[str, Any]] = []
        valid_times: list[tuple[datetime, datetime]] = []
        for descriptor in self._stop_evidence_providers:
            service_id = str(descriptor["service_id"])
            try:
                raw = descriptor["service"].collect_stop_evidence(
                    robot_id=self.config.robot_id,
                    reason=reason,
                )
                checked_at = datetime.now(timezone.utc)
                report, observed_at, expires_at = (
                    self._normalize_stop_evidence_report(
                        raw,
                        service_id=service_id,
                        owner_plugin_id=str(descriptor["owner_plugin_id"]),
                        provider_descriptor=descriptor,
                        collected_at=checked_at,
                    )
                )
                valid_times.append((observed_at, expires_at))
            except Exception as exc:
                report = {
                    "service_id": service_id,
                    "owner_plugin_id": descriptor["owner_plugin_id"],
                    "status": "unknown",
                    "error_code": "stop_evidence_provider_failed",
                    "exception_class": type(exc).__name__,
                }
            reports.append(report)

        with self._task_lock:
            active_task_ids = sorted(self._task_controls)
        active_leases = [
            lease.to_dict()
            for lease in self.resource_leases.active(
                robot_id=self.config.robot_id
            )
        ]
        blockers: list[str] = []
        if not reports:
            blockers.append("trusted_stop_evidence_provider_missing")
        if (
            self.config.deployment_profile.mode == "real"
            and not any(
                item.get("qualified_for_real") is True
                for item in self._stop_evidence_providers
            )
        ):
            blockers.append("hardware_stop_evidence_provider_missing")
        if any(report.get("status") != "stopped" for report in reports):
            blockers.append("runtime_stop_not_confirmed")
        if active_task_ids:
            blockers.append("gateway_tasks_still_active")
        if active_leases:
            blockers.append("resource_leases_still_active")

        if valid_times:
            observed_at = max(item[0] for item in valid_times)
            expires_at = min(item[1] for item in valid_times)
        else:
            observed_at = collected_at
            expires_at = collected_at
        aggregate_status = "stopped" if not blockers else (
            "moving"
            if any(report.get("status") == "moving" for report in reports)
            else "unknown"
        )
        return {
            "status": aggregate_status,
            "robot_id": self.config.robot_id,
            "observed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "providers": reports,
            "gateway_active_task_ids": active_task_ids,
            "active_leases": active_leases,
            "blockers": blockers,
        }

    def _normalize_stop_evidence_report(
        self,
        raw: Any,
        *,
        service_id: str,
        owner_plugin_id: str,
        provider_descriptor: Mapping[str, Any],
        collected_at: datetime,
    ) -> tuple[dict[str, Any], datetime, datetime]:
        if not isinstance(raw, Mapping):
            raise ValueError("stop evidence provider result must be an object")
        status = str(raw.get("status") or "")
        if status not in {"stopped", "moving", "unknown"}:
            raise ValueError("stop evidence status is invalid")
        if str(raw.get("robot_id") or "") != self.config.robot_id:
            raise ValueError("stop evidence robot_id does not match")
        if str(raw.get("provider_id") or "") != str(
            provider_descriptor.get("provider_id") or ""
        ):
            raise ValueError("stop evidence provider_id does not match")
        if str(raw.get("deployment_mode") or "") != (
            self.config.deployment_profile.mode
        ):
            raise ValueError("stop evidence deployment mode does not match")
        if (
            self.config.deployment_profile.mode == "real"
            and raw.get("dry_run") is not False
        ):
            raise ValueError("real stop evidence must not be dry-run")
        observed_at = datetime.fromisoformat(str(raw.get("observed_at") or ""))
        expires_at = datetime.fromisoformat(str(raw.get("expires_at") or ""))
        if observed_at.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError("stop evidence timestamps must include a timezone")
        if observed_at > collected_at or expires_at <= collected_at:
            raise ValueError("stop evidence is stale or not yet valid")
        validity_seconds = (expires_at - observed_at).total_seconds()
        if (
            validity_seconds <= 0
            or validity_seconds > _STOP_EVIDENCE_MAX_VALIDITY_SECONDS
        ):
            raise ValueError("stop evidence validity window is invalid")
        details = raw.get("details")
        if not isinstance(details, Mapping):
            raise ValueError("stop evidence details must be an object")
        descriptor_evidence_class = provider_descriptor.get("evidence_class")
        raw_evidence_class = raw.get("evidence_class")
        if (
            raw_evidence_class is not None
            and raw_evidence_class != descriptor_evidence_class
        ):
            raise ValueError("stop evidence class does not match provider")
        if self.config.deployment_profile.mode == "real":
            if provider_descriptor.get("qualified_for_real") is not True:
                raise ValueError(
                    "real stop evidence must come from a hardware-owned provider"
                )
            if raw_evidence_class != HARDWARE_STOP_EVIDENCE_CLASS:
                raise ValueError("real stop evidence class is invalid")
            self._validate_real_hardware_stop_details(
                details,
                status=status,
            )
        encoded_details = json.dumps(
            dict(details),
            ensure_ascii=False,
            sort_keys=True,
        )
        if len(encoded_details.encode("utf-8")) > 64 * 1024:
            raise ValueError("stop evidence details exceed the size limit")
        report = {
            "service_id": service_id,
            "owner_plugin_id": owner_plugin_id,
            "provider_id": str(raw.get("provider_id") or service_id),
            "evidence_class": descriptor_evidence_class,
            "hardware_owned": provider_descriptor.get("hardware_owned") is True,
            "qualified_for_real": (
                provider_descriptor.get("qualified_for_real") is True
            ),
            "status": status,
            "deployment_mode": raw["deployment_mode"],
            "dry_run": raw.get("dry_run"),
            "observed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "details": json.loads(encoded_details),
        }
        return report, observed_at, expires_at

    @staticmethod
    def _validate_real_hardware_stop_details(
        details: Mapping[str, Any],
        *,
        status: str,
    ) -> None:
        """Reject a real ``stopped`` claim unless every hardware check passes."""

        if details.get("schema_version") != _HARDWARE_STOP_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("hardware stop evidence schema is invalid")
        blockers = details.get("blockers")
        if not isinstance(blockers, list) or any(
            not isinstance(item, str) or not item
            for item in blockers
        ):
            raise ValueError("hardware stop evidence blockers are invalid")
        for key in (
            "watchdog",
            "emergency_stop",
            "driver",
            "brake",
            "actuators",
            "independent_motion",
        ):
            if not isinstance(details.get(key), Mapping):
                raise ValueError(f"hardware stop evidence {key} is invalid")

        # Partial or negative reports remain useful diagnostic evidence.  Only
        # a positive standstill claim is allowed to reopen admission.
        if status != "stopped":
            return
        if blockers:
            raise ValueError("stopped hardware evidence contains blockers")
        if details.get("stop_reasserted") is not True:
            raise ValueError("hardware stop was not reasserted")
        if details.get("stop_acknowledged") is not True:
            raise ValueError("hardware stop was not acknowledged")

        watchdog = details["watchdog"]
        if not (
            watchdog.get("fresh") is True
            and watchdog.get("healthy") is True
            and watchdog.get("stop_asserted") is True
            and _stop_sample_count(
                watchdog.get("sample_count"),
                "hardware watchdog sample_count",
            )
            >= _HARDWARE_STOP_MINIMUM_SAMPLES
        ):
            raise ValueError("hardware watchdog does not prove a stop")
        emergency_stop = details["emergency_stop"]
        if not (
            emergency_stop.get("fresh") is True
            and emergency_stop.get("active") is True
            and _stop_sample_count(
                emergency_stop.get("sample_count"),
                "hardware emergency stop sample_count",
            )
            >= _HARDWARE_STOP_MINIMUM_SAMPLES
        ):
            raise ValueError("hardware emergency stop is not active")
        driver = details["driver"]
        if not (
            driver.get("fresh") is True
            and driver.get("enabled") is False
            and _stop_sample_count(
                driver.get("sample_count"),
                "hardware driver sample_count",
            )
            >= _HARDWARE_STOP_MINIMUM_SAMPLES
        ):
            raise ValueError("hardware driver is not disabled")
        brake = details["brake"]
        if not isinstance(brake.get("required"), bool):
            raise ValueError("hardware brake requirement is not explicit")
        if brake["required"] and not (
            brake.get("fresh") is True
            and brake.get("engaged") is True
            and _stop_sample_count(
                brake.get("sample_count"),
                "hardware brake sample_count",
            )
            >= _HARDWARE_STOP_MINIMUM_SAMPLES
        ):
            raise ValueError("hardware brake is not engaged")

        hold_seconds = _bounded_stop_number(
            details.get("hold_seconds"),
            "hardware hold_seconds",
        )
        if hold_seconds < _HARDWARE_STOP_MINIMUM_HOLD_SECONDS:
            raise ValueError("hardware stop hold window is too short")

        actuators = details["actuators"]
        expected_names = _unique_stop_names(
            actuators.get("expected_names"),
            "expected actuator names",
        )
        observed_names = _unique_stop_names(
            actuators.get("observed_names"),
            "observed actuator names",
        )
        if not expected_names or not expected_names.issubset(observed_names):
            raise ValueError("hardware actuator inventory is incomplete")
        if actuators.get("unclassified_names") != []:
            raise ValueError("hardware actuator inventory has unclassified names")
        if not (
            actuators.get("fresh") is True
            and actuators.get("inventory_complete") is True
        ):
            raise ValueError("hardware actuator state is not complete and fresh")
        actuator_samples = _stop_sample_count(
            actuators.get("stationary_samples"),
            "hardware actuator stationary_samples",
        )
        if actuator_samples < _HARDWARE_STOP_MINIMUM_SAMPLES:
            raise ValueError("hardware actuator samples are insufficient")
        actuator_limit = _bounded_stop_number(
            actuators.get("velocity_threshold"),
            "hardware actuator velocity_threshold",
        )
        actuator_velocity = _bounded_stop_number(
            actuators.get("max_abs_velocity"),
            "hardware actuator max_abs_velocity",
        )
        if (
            actuator_limit <= 0
            or actuator_limit > _HARDWARE_STOP_MAX_ACTUATOR_VELOCITY
            or actuator_velocity > actuator_limit
        ):
            raise ValueError("hardware actuator velocity is not stationary")

        motion = details["independent_motion"]
        if motion.get("fresh") is not True:
            raise ValueError("independent motion evidence is stale")
        motion_samples = _stop_sample_count(
            motion.get("stationary_samples"),
            "independent motion stationary_samples",
        )
        if motion_samples < _HARDWARE_STOP_MINIMUM_SAMPLES:
            raise ValueError("independent motion samples are insufficient")
        linear_limit = _bounded_stop_number(
            motion.get("linear_velocity_threshold"),
            "independent linear velocity threshold",
        )
        angular_limit = _bounded_stop_number(
            motion.get("angular_velocity_threshold"),
            "independent angular velocity threshold",
        )
        linear_velocity = _bounded_stop_number(
            motion.get("max_linear_speed"),
            "independent max linear speed",
        )
        angular_velocity = _bounded_stop_number(
            motion.get("max_angular_speed"),
            "independent max angular speed",
        )
        if (
            linear_limit <= 0
            or linear_limit > _HARDWARE_STOP_MAX_LINEAR_VELOCITY
            or angular_limit <= 0
            or angular_limit > _HARDWARE_STOP_MAX_ANGULAR_VELOCITY
            or linear_velocity > linear_limit
            or angular_velocity > angular_limit
        ):
            raise ValueError("independent motion evidence is not stationary")

    @staticmethod
    def _public_recovery_request(request: Mapping[str, Any]) -> dict[str, Any]:
        public = dict(request)
        public.pop("confirmation_phrase", None)
        public.pop("transitioned", None)
        return public

    def _cancel_all_active_tasks(self, *, reason: str | None) -> list[str]:
        with self._task_lock:
            controls = list(self._task_controls.values())
        cancelled_task_ids = []
        for control in controls:
            already_requested = control.cancel_event.is_set()
            control.cancel_event.set()
            cancelled_task_ids.append(control.task_id)
            if not already_requested:
                self._append_event(
                    task_id=control.task_id,
                    session_id=control.session_id,
                    type="task.cancel_requested",
                    payload={
                        "status": "cancel_requested",
                        "task_id": control.task_id,
                        "reason": reason,
                        "source": "emergency_stop",
                    },
                )
        return cancelled_task_ids

    def _build_robot_agent_runtime(
        self,
    ) -> RobotAgentRuntime | RobotAgentDeliberationRuntime | None:
        if not self.config.robot_agent_enabled:
            return None
        if self.config.robot_agent_planner == "deterministic":
            return RobotAgentRuntime(planner=DeterministicRobotAgentPlanner())
        if self.config.robot_agent_planner == "llm":
            runtime = build_provider_runtime(
                provider_base_url=self.config.robot_agent_provider_base_url,
                provider_api_key=self.config.robot_agent_provider_api_key,
                model=self.config.robot_agent_model,
                model_catalog_path=self.config.robot_agent_model_catalog_path,
            )
            return RobotAgentDeliberationRuntime(
                policy=LLMRobotAgentDecisionPolicy(runtime),
                checkpoint_store=self.agent_loop_checkpoints,
            )
        raise ValueError(f"unsupported robot_agent_planner: {self.config.robot_agent_planner}")

    def _run_robot_agent_structured_task(
        self,
        *,
        agent: FireClawAgent,
        task_object: StructuredRobotTask,
        session_id: str,
        task_id: str,
        cancellation_requested=None,
        execution_authorization: ExecutionAuthorization | None = None,
    ) -> dict[str, Any]:
        assert self.robot_agent_runtime is not None

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type=event_type,
                payload=payload,
            )

        agent_tool_runtime: AgentToolRuntime | None = None
        has_agent_tools = False
        deployment_profile = self.config.deployment_profile
        verified_agent_tool_authorization = (
            self._verify_embedded_execution_authorization(
                execution_authorization
            )
        )

        extension_report = agent.extension_report
        if extension_report is None:
            raise RuntimeError("Robot Agent extensions were not loaded at construction time")
        bind_skill_catalog = getattr(
            self.robot_agent_runtime,
            "bind_skill_catalog",
            None,
        )
        if callable(bind_skill_catalog):
            bind_skill_catalog(agent.registry)
        has_agent_tools = bool(extension_report.tool_ids)
        emit("plugin.extensions_loaded", extension_report.to_dict())
        if has_agent_tools:
            agent_tool_runtime = AgentToolRuntime(
                plugin_host=agent.plugin_host,
                profile=deployment_profile,
                event_sink=lambda event: emit(
                    str(event["type"]),
                    dict(event["payload"]),
                ),
                authorization_use_recorder=(
                    self.runtime_state.record_authorization_use
                ),
            )

        def build_context() -> dict[str, Any]:
            robot_state_object = agent._get_robot_state()
            robot_state = agent._state_snapshot(robot_state_object)
            runtime_sensors = (
                robot_state.get("available_sensors")
                if isinstance(robot_state, dict)
                else None
            )
            verified_sensors = set(
                runtime_sensors
                if runtime_sensors is not None
                else agent.available_sensors
            )
            capability_projection = agent.project_skill_capabilities(
                task_object,
                skill_names=agent.registry.names(),
                robot_state=robot_state_object,
            )
            exposed_skill_names = capability_projection.after
            skill_tools = build_robot_skill_tools(
                agent.registry,
                exposed_skill_names=exposed_skill_names,
            )
            skill_metadata = [
                metadata
                for metadata in agent.registry.list_metadata()
                if metadata["name"] in exposed_skill_names
            ]
            primitive_skills = (
                tuple(
                    name
                    for name in self.robot_profile.primitive_skills
                    if name in exposed_skill_names
                )
                if self.robot_profile
                else exposed_skill_names
            )
            composite_chains = (
                {
                    capability: chain
                    for capability, chain in (
                        self.robot_profile.capability_skill_chains.items()
                    )
                    if all(step in exposed_skill_names for step in chain)
                }
                if self.robot_profile
                else {}
            )
            context = {
                "robot_state": robot_state,
                "environment_state": agent._state_snapshot(
                    agent._get_environment_state()
                ),
                "available_sensors": sorted(verified_sensors),
                "skill_tools": skill_tools,
                "skill_metadata": skill_metadata,
                "capability_policy": capability_projection.to_dict(),
                "skill_inventory": build_robot_skill_inventory(
                    registry=agent.registry,
                    primitive_skills=primitive_skills,
                    composite_chains=composite_chains,
                    verified_sensors=verified_sensors,
                ),
                "session_history": agent._recent_session_records(limit=50),
            }
            if agent_tool_runtime is not None:
                context["agent_tools"] = agent_tool_runtime.tool_schemas()
                context["agent_tool_policy"] = (
                    agent_tool_runtime.exposure_manifest()
                )
            if self.entity_memory_tools is not None and task_object.mission_id:
                context["memory_tools"] = (
                    self.entity_memory_tools.tool_schemas()
                )
            return context

        if isinstance(
            self.robot_agent_runtime,
            RobotAgentDeliberationRuntime,
        ):
            step_executions = []

            def execute_skill(step):
                if not step.operation_id:
                    raise ValueError(
                        "Robot Agent physical skill requires operation_id"
                    )
                target_floor = task_object.target.get("floor")
                planning_result = PlanningResult(
                    status="planned",
                    message="Robot Agent proposed one policy-checked skill.",
                    intent=task_object.task_type,
                    target_floor=(
                        target_floor
                        if isinstance(target_floor, int)
                        else None
                    ),
                    target_pose=(
                        {
                            **dict(task_object.target["pose"]),
                            "frame_id": str(
                                task_object.target.get("frame_id") or "map"
                            ),
                        }
                        if isinstance(task_object.target.get("pose"), dict)
                        else None
                    ),
                    plan=Plan(
                        intent=task_object.task_type,
                        steps=[
                            PlanStep(
                                skill_name=step.skill_name,
                                inputs=dict(step.inputs),
                            )
                        ],
                    ),
                )
                emit(
                    "robot_agent.skill_dispatch_started",
                    {
                        "operation_id": step.operation_id,
                        "skill_name": step.skill_name,
                        "inputs": dict(step.inputs),
                    },
                )
                run = agent.execute_deliberated_step(
                    command=task_object.command or task_object.task_type,
                    planning_result=planning_result,
                    structured_task=task_object,
                    operation_id=step.operation_id,
                )
                step_executions.append(run)
                emit(
                    "robot_agent.skill_dispatch_finished",
                    {
                        "operation_id": step.operation_id,
                        "skill_name": step.skill_name,
                        "output": run.payload,
                    },
                )
                return run.payload

            def reconcile_skill(
                operation_id: str,
                step,
            ) -> dict[str, Any] | None:
                started = False
                for event in self.events.events_for_task(task_id):
                    payload = event.get("payload")
                    if (
                        not isinstance(payload, dict)
                        or payload.get("operation_id") != operation_id
                    ):
                        continue
                    if event.get("type") == (
                        "robot_agent.skill_dispatch_started"
                    ):
                        started = True
                    if event.get("type") == (
                        "robot_agent.skill_dispatch_finished"
                    ):
                        self.resource_leases.release(
                            robot_id=self.config.robot_id,
                            owner_id=task_id,
                            operation_id=operation_id,
                        )
                        output = payload.get("output")
                        if isinstance(output, dict):
                            return dict(output)
                        return {"reconciliation_status": "unknown"}
                if started:
                    return {"reconciliation_status": "unknown"}
                return {"reconciliation_status": "not_started"}

            def query_context(
                tool_name: str,
                arguments: dict[str, Any],
            ) -> dict[str, Any]:
                if (
                    self.entity_memory_tools is None
                    or task_object.mission_id is None
                ):
                    return {
                        "status": "error",
                        "message": "Robot Agent context tools are unavailable.",
                        "advisory_only": True,
                    }
                try:
                    result = self.entity_memory_tools.execute(
                        tool_name,
                        arguments,
                        mission_id=task_object.mission_id,
                    )
                except (TypeError, ValueError) as exc:
                    return {
                        "status": "error",
                        "message": str(exc),
                        "advisory_only": True,
                    }
                if isinstance(result, dict):
                    return dict(result)
                return {
                    "status": "error",
                    "message": "Context tool returned a non-object result.",
                    "advisory_only": True,
                }

            def execute_agent_tool(
                tool_name: str,
                arguments: dict[str, Any],
            ) -> dict[str, Any]:
                if agent_tool_runtime is None:
                    return {
                        "status": "blocked",
                        "error_code": "agent_tool_runtime_unavailable",
                        "message": "Robot Agent Tool runtime is unavailable.",
                        "result_authority": "advisory",
                    }
                return agent_tool_runtime.execute(
                    tool_name,
                    arguments,
                    authorization=verified_agent_tool_authorization,
                    context={
                        "mission_id": task_object.mission_id,
                        "task_id": task_object.task_id,
                        "robot_id": self.config.robot_id,
                    },
                ).to_dict()

            emit("task.structured_received", task_object.to_dict())
            loop_result = self.robot_agent_runtime.run(
                task_object,
                fallback_robot_id=self.config.robot_id,
                context_provider=build_context,
                execute_skill=execute_skill,
                execute_agent_tool=execute_agent_tool,
                query_context=query_context,
                reconcile_skill=reconcile_skill,
                event_sink=emit,
                cancellation_requested=cancellation_requested,
            )
            return agent.finalize_deliberated_task(
                command=task_object.command or task_object.task_type,
                structured_task=task_object,
                loop_result=loop_result,
                step_executions=step_executions,
            )

        planning_result = self.robot_agent_runtime.plan_structured_task(
            task_object,
            fallback_robot_id=self.config.robot_id,
            context=build_context(),
            event_sink=emit,
            cancellation_requested=cancellation_requested,
        )
        return agent.run_planning_result(
            command=task_object.command or task_object.task_type,
            structured_task=task_object,
            planning_result=planning_result,
        )

    def _execute_agent_task(
        self,
        *,
        command: str,
        session_id: str,
        task_id: str,
        record_received: bool,
        cancellation_requested=None,
        operator: OperatorContext | None = None,
        structured_task: dict[str, Any] | None = None,
        execution_authorization: ExecutionAuthorization | None = None,
    ) -> dict[str, Any]:
        if record_received:
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="task.received",
                payload={"command": command},
            )
        agent = self._create_agent(
            task_id=task_id,
            session_id=session_id,
            cancellation_requested=cancellation_requested,
            execution_authorization=execution_authorization,
            operator=operator,
        )
        if structured_task is not None:
            task_object = StructuredRobotTask.from_dict(structured_task)
            if self.robot_agent_runtime is not None:
                result = self._run_robot_agent_structured_task(
                    agent=agent,
                    task_object=task_object,
                    session_id=session_id,
                    task_id=task_id,
                    cancellation_requested=cancellation_requested,
                    execution_authorization=execution_authorization,
                )
            else:
                result = agent.run_structured_task(task_object)
        else:
            result = agent.run(command)
        result["task_id"] = task_id
        self._record_authorization_request_if_needed(
            task_id=task_id,
            session_id=session_id,
            command=command,
            result=result,
            operator=operator or operator_from_payload(None),
        )
        self._record_result_events(task_id, session_id, result)
        return result

    def _verify_embedded_execution_authorization(
        self,
        authorization: ExecutionAuthorization | None,
    ) -> VerifiedExecutionAuthorization | None:
        """Verify a signed grant before the Agent Tool runtime binds it.

        The authority proves issuer, signature, expiry, robot, and the signed
        hashes. AgentToolRuntime then independently binds those hashes to the
        current plugin contract, final hook-adjusted arguments, and task
        context before consuming the grant.
        """

        if authorization is None:
            return None
        action_hashes = {
            str(action.get("action_hash") or "")
            for action in authorization.authorized_actions
            if isinstance(action, dict) and action.get("action_hash")
        }
        if not action_hashes:
            return None
        verification = self.execution_authorization_authority.verify(
            authorization,
            robot_id=self.config.robot_id,
            scope_hash=authorization.scope_hash,
            action_hashes=action_hashes,
        )
        return verification.grant if verification.verified else None

    def _record_authorization_request_if_needed(
        self,
        *,
        task_id: str,
        session_id: str,
        command: str,
        result: dict[str, Any],
        operator: OperatorContext,
    ) -> None:
        if result.get("status") != "awaiting_confirmation":
            return
        agent_tool_approval = _agent_tool_approval_from_result(result)
        risk_level = (
            "medium"
            if agent_tool_approval is not None
            else _risk_level_from_confirmation(result.get("confirmation"))
        )
        decision = ControlPolicy().evaluate_risk(operator, action="task.confirm", risk_level=risk_level)
        if decision.status not in {"allow", "approval_required"}:
            return
        if agent_tool_approval is not None:
            actions = [dict(agent_tool_approval["authorized_action"])]
            scope_hash = str(agent_tool_approval["scope_hash"])
            authorization_kind = "agent_tool"
        else:
            planning = result.get("planning")
            plan = (
                planning.get("plan")
                if isinstance(planning, dict)
                else None
            )
            steps = plan.get("steps") if isinstance(plan, dict) else None
            actions = [
                authorized_action(
                    str(step.get("skill_name") or ""),
                    dict(step.get("inputs") or {}),
                )
                for step in (steps if isinstance(steps, list) else [])
                if isinstance(step, dict)
                and isinstance(step.get("skill_name"), str)
                and isinstance(step.get("inputs"), dict)
            ]
            authorization_kind = "physical"
        if not actions:
            return
        structured_task = (
            dict(result["structured_task"])
            if isinstance(result.get("structured_task"), dict)
            else None
        )
        if agent_tool_approval is None:
            scope_hash = execution_scope_hash(
                command=command,
                structured_task=structured_task,
                actions=actions,
            )
        requested_at_dt = datetime.now(timezone.utc)
        expires_at_dt = requested_at_dt + timedelta(seconds=max(0, self.config.authorization_expiry_seconds))
        request = AuthorizationRequest(
            request_id=f"auth-{uuid4().hex}",
            task_id=task_id,
            session_id=session_id,
            command=command,
            requested_by=operator,
            required_scope=(
                "task.confirm"
                if authorization_kind == "agent_tool"
                else "safety.override"
            ),
            risk_level=risk_level,
            requested_at=requested_at_dt.isoformat(),
            expires_at=expires_at_dt.isoformat(),
            structured_task=structured_task,
            scope_hash=scope_hash,
            authorized_actions=tuple(actions),
            robot_id=self.config.robot_id,
            authorization_kind=authorization_kind,
        )
        self.runtime_state.create_authorization_request(
            request.to_dict()
        )
        self._append_event(
            task_id=task_id,
            session_id=session_id,
            type="authorization.requested",
            payload={
                "authorization": request.to_dict(),
                "control": decision.to_dict(),
            },
        )

    def _pending_authorization_for_task(
        self,
        *,
        session_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        payload = (
            self.runtime_state.pending_authorization_request_for_task(
                task_id
            )
        )
        if (
            isinstance(payload, dict)
            and payload.get("task_id") == task_id
            and payload.get("session_id") == session_id
        ):
            return payload
        return None

    def _terminalize_waiting_task(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        message: str,
        reason_code: str,
    ) -> dict[str, Any] | None:
        record = self.task_queue.get(task_id)
        if record is None:
            return None
        if record.is_terminal:
            return dict(record.result) if record.result is not None else None

        result = dict(record.result or {})
        raw_status = result.get("raw_status") or result.get("status")
        terminal_outcome = build_robot_task_terminal_outcome(status)
        if isinstance(raw_status, str) and raw_status != status:
            terminal_outcome = replace(
                terminal_outcome,
                raw_status=raw_status,
            )
            result["raw_status"] = raw_status
        result.update(
            {
                "status": status,
                "task_id": task_id,
                "session_id": session_id,
                "message": message,
                "reason_code": reason_code,
                "terminal_outcome": terminal_outcome.to_dict(),
            }
        )
        self._append_event(
            task_id=task_id,
            session_id=session_id,
            type=terminal_outcome.event_type,
            payload={
                "status": status,
                "raw_status": terminal_outcome.raw_status,
                "terminal_outcome": terminal_outcome.to_dict(),
                "message": message,
                "reason_code": reason_code,
                "result": result,
            },
        )
        self.task_queue.update(
            task_id,
            status=status,
            ended_at=datetime.now(timezone.utc).isoformat(),
            error=(message if status not in {"completed", "cancelled"} else None),
            result=result,
        )
        return result

    def _authorize_confirmation(
        self,
        *,
        session_id: str,
        operator: OperatorContext,
    ) -> tuple[bool, dict[str, Any]]:
        request_payload = (
            self.runtime_state.pending_authorization_request(session_id)
        )
        if request_payload is None:
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "没有绑定具体动作参数的待审批请求。",
            }
        request = AuthorizationRequest.from_dict(request_payload)
        now = datetime.now(timezone.utc).isoformat()
        task_record = self.task_queue.get(request.task_id)
        task_result = task_record.result if task_record is not None else None
        if (
            not isinstance(task_result, dict)
            or task_result.get("status") != "awaiting_confirmation"
        ):
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "审批请求尚未绑定到已提交的待确认任务结果。",
                "reason_code": "authorization_task_not_pending",
                "authorization": request.to_dict(),
            }
        if request.is_expired(now):
            try:
                with self.runtime_state.transaction():
                    self.runtime_state.resolve_authorization_request(
                        request.request_id,
                        status="expired",
                        decided_by=operator.to_dict(),
                        decided_at=now,
                    )
                    payload = {
                        "status": "expired",
                        "session_id": session_id,
                        "authorization": request.to_dict(),
                        "expired_at": now,
                    }
                    self._append_event(
                        task_id=request.task_id,
                        session_id=session_id,
                        type="authorization.expired",
                        payload=payload,
                    )
                    self._terminalize_waiting_task(
                        task_id=request.task_id,
                        session_id=session_id,
                        status="escalated",
                        message=(
                            "执行授权请求已过期；任务未执行，"
                            "需要操作员重新提交。"
                        ),
                        reason_code="authorization_expired",
                    )
            except StaleRuntimeStateWrite:
                pass
            return False, {
                "status": "expired",
                "session_id": session_id,
                "message": "授权请求已过期，请重新提交任务。",
                "authorization": request.to_dict(),
            }
        decision = ControlPolicy().evaluate_risk(operator, action="task.confirm", risk_level=request.risk_level)
        if decision.status != "allow":
            payload = {
                "status": "denied",
                "session_id": session_id,
                "authorization": request.to_dict(),
                "control": decision.to_dict(),
            }
            self._append_event(
                task_id=request.task_id,
                session_id=session_id,
                type="authorization.denied",
                payload=payload,
            )
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "操作员没有权限批准该任务。",
                "authorization": request.to_dict(),
                "control": decision.to_dict(),
            }
        if request.scope_hash is None or not request.authorized_actions:
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "审批请求缺少具体动作范围，拒绝签发执行授权。",
                "authorization": request.to_dict(),
            }
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(
                seconds=max(
                    1,
                    self.config.authorization_expiry_seconds,
                )
            )
        ).isoformat()
        execution_authorization = (
            self.execution_authorization_authority.issue(
                request_id=request.request_id,
                operator_id=operator.operator_id,
                mission_id=(
                    str(request.structured_task.get("mission_id"))
                    if isinstance(request.structured_task, dict)
                    and request.structured_task.get("mission_id")
                    is not None
                    else None
                ),
                task_id=(
                    str(request.structured_task.get("task_id"))
                    if isinstance(request.structured_task, dict)
                    and request.structured_task.get("task_id") is not None
                    else request.task_id
                ),
                robot_id=request.robot_id or self.config.robot_id,
                scope_hash=request.scope_hash,
                authorized_actions=[
                    dict(action)
                    for action in request.authorized_actions
                ],
                issued_at=now,
                expires_at=expires_at,
            )
        )
        try:
            with self.runtime_state.transaction():
                resolved_request = (
                    self.runtime_state.resolve_authorization_request(
                        request.request_id,
                        status="approved",
                        decided_by=operator.to_dict(),
                        decided_at=now,
                    )
                )
                self.runtime_state.persist_execution_authorization(
                    execution_authorization.to_dict()
                )
        except StaleRuntimeStateWrite:
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "审批请求已被其他决策处理，拒绝重复批准。",
                "reason_code": "authorization_request_stale",
                "authorization": request.to_dict(),
            }
        return True, {
            "status": "approved",
            "session_id": session_id,
            "authorization": resolved_request,
            "execution_authorization": (
                execution_authorization.to_dict()
            ),
            "control": decision.to_dict(),
            "approved_by": operator.to_dict(),
            "approved_at": now,
        }

    def confirm_task(
        self,
        *,
        session_id: str,
        operator: OperatorContext,
    ) -> dict[str, Any]:
        with self._task_lock:
            with self.runtime_state.transaction():
                request_payload = (
                    self.runtime_state.pending_authorization_request(
                        session_id
                    )
                )
                if request_payload is None:
                    return {
                        "status": "denied",
                        "session_id": session_id,
                        "message": "没有绑定具体动作参数的待审批请求。",
                    }
                request = AuthorizationRequest.from_dict(request_payload)
                now = datetime.now(timezone.utc).isoformat()
                if request.is_expired(now):
                    _, expired = self._authorize_confirmation(
                        session_id=session_id,
                        operator=operator,
                    )
                    return expired

                admission_state = self.resource_leases.admission_state()
                if admission_state.get("closed"):
                    return {
                        "status": "blocked",
                        "task_id": request.task_id,
                        "session_id": session_id,
                        "message": "机器人处于急停资源冻结状态，暂不恢复任务。",
                        "resource_admission": admission_state,
                    }
                other_active = [
                    active_control
                    for active_task_id, active_control
                    in self._task_controls.items()
                    if active_task_id != request.task_id
                ]
                if (
                    len(other_active)
                    >= self.config.max_active_execution_tasks
                ):
                    return {
                        "status": "busy",
                        "task_id": request.task_id,
                        "session_id": session_id,
                        "message": (
                            "机器人当前已有其他任务执行，"
                            "请稍后再次确认。"
                        ),
                        "active_tasks": [
                            {
                                "task_id": active_control.task_id,
                                "session_id": active_control.session_id,
                                "command": active_control.command,
                                "started_at": active_control.started_at,
                                "cancel_requested": (
                                    active_control.cancel_event.is_set()
                                ),
                            }
                            for active_control in other_active
                        ],
                    }

                authorized, authorization_payload = (
                    self._authorize_confirmation(
                        session_id=session_id,
                        operator=operator,
                    )
                )
                if not authorized:
                    return authorization_payload
                execution_payload = authorization_payload.get(
                    "execution_authorization"
                )
                if not isinstance(execution_payload, dict):
                    raise RuntimeError(
                        "Approved confirmation did not issue execution "
                        "authorization."
                    )
                execution_authorization = (
                    ExecutionAuthorization.from_dict(execution_payload)
                )
                structured_task = (
                    dict(request.structured_task)
                    if request.structured_task is not None
                    else None
                )
                if structured_task is not None:
                    structured_task["execution_authorization"] = (
                        execution_authorization.to_dict()
                    )
                queue_record = self.task_queue.get(request.task_id)
                if queue_record is None:
                    raise KeyError(
                        f"Task queue record not found: {request.task_id}"
                    )
                resume_result = {
                    "status": "accepted",
                    "task_id": request.task_id,
                    "session_id": session_id,
                    "message": "确认已批准；原任务正在恢复执行。",
                    "authorization_id": (
                        execution_authorization.authorization_id
                    ),
                }
                self.task_queue.update(
                    request.task_id,
                    status="accepted",
                    result=resume_result,
                )
                self._append_event(
                    task_id=request.task_id,
                    session_id=session_id,
                    type="authorization.approved",
                    payload=authorization_payload,
                )
                self._append_event(
                    task_id=request.task_id,
                    session_id=session_id,
                    type="task.resume_scheduled",
                    payload={
                        **resume_result,
                        "request_id": request.request_id,
                        "execution_authorization": (
                            execution_authorization.to_dict()
                        ),
                        "structured_task": structured_task,
                        "requested_by": request.requested_by.to_dict(),
                        "approved_by": operator.to_dict(),
                    },
                )
                control = TaskControl(
                    task_id=request.task_id,
                    session_id=session_id,
                    command=request.command,
                    started_at=(
                        queue_record.started_at
                        or queue_record.created_at
                    ),
                    structured_task=structured_task,
                    execution_authorization=execution_authorization,
                )
            self._task_controls[request.task_id] = control

        self._start_task_worker(
            control,
            request.requested_by,
            resumed=True,
        )
        return {
            "status": "accepted",
            "task_id": request.task_id,
            "session_id": session_id,
            "message": "确认已批准；原任务正在恢复执行。",
            "authorization_id": execution_authorization.authorization_id,
            "approved_by": operator.to_dict(),
        }

    def list_skills(self, session_id: str | None = None) -> dict[str, Any]:
        return self.run_agent("你有哪些技能", session_id=session_id)

    def recent_memory(self, *, session_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        return {
            "records": self.memory.latest_records(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def export_memory_replication(
        self,
        *,
        scope: Any,
        request_auth: Any,
    ) -> dict[str, Any]:
        from fireclaw_core.memory.replication_security import (
            verify_signed_request,
            sign_payload,
        )

        security = self.replication_security
        if security is None or self.memory_replication_exporter is None:
            raise ValueError("authenticated replication is not configured")
        policy = security.peer_policies.get(request_auth.peer_id)
        if policy is None:
            raise ValueError("replication peer policy not found")
        verified = verify_signed_request(
            body={},
            auth=request_auth,
            scope=scope,
            key_provider=security.key_provider,
            peer_id=request_auth.peer_id,
            nonce_cache=security.nonce_cache,
        )
        if not verified.verified:
            raise ValueError(
                f"replication request verification failed: {verified.error_code}"
            )
        batch = self.memory_replication_exporter.export_batch(
            cursor=scope.cursor,
            limit=scope.limit,
            mission_id=scope.mission_id,
            runtime_mode=scope.runtime_mode,
            peer_policy=policy,
        )
        body = batch.to_dict()
        auth = sign_payload(
            payload=body,
            scope=scope,
            key_provider=security.key_provider,
            identity=security.identity,
        )
        return {**body, "auth": auth.to_dict()}

    def recent_events(self, *, session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {
            "events": self.events.latest_events(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def task_trace(self, task_id: str) -> dict[str, Any]:
        with self._task_lock:
            control = self._task_controls.get(task_id)
            task_is_active = control is not None
            structured_task = (
                control.structured_task
                if control is not None
                else None
            )
        with self.runtime_state.transaction():
            events = self.events.events_for_task(task_id)
            result = self._task_result_from_events(events)
            queue_record = self.task_queue.get(task_id)
            if (
                result is None
                and queue_record is not None
                and isinstance(queue_record.result, dict)
            ):
                result = dict(queue_record.result)
            status = self._task_status(
                task_id,
                events,
                result,
                task_is_active=task_is_active,
            )
        if structured_task is None and result is not None:
            structured_task = result.get("structured_task")
        return {
            "task_id": task_id,
            "events": events,
            "result": result,
            "status": status,
            "state": project_task_state(events),
            "queue_record": queue_record.to_dict() if queue_record is not None else None,
            "structured_task": structured_task,
        }

    def state(self) -> dict[str, Any]:
        return {
            "robot_state": asdict(self.robot.get_robot_state()),
            "environment_state": asdict(self.robot.get_environment_state()),
            "task_capacity": self.task_capacity(),
            "active_tasks": self.active_tasks(),
            "task_queue": self.task_queue.summary(),
            "emergency_stop": asdict(self._emergency_stop),
            "resource_admission": self.resource_admission_state(),
            "runtime_storage": self.runtime_storage_state(),
            "network_admission": self._network_guard.snapshot(),
            "runtime_paths": {
                "process_working_directory": str(
                    self._process_working_directory
                ),
                "agent_workspace": str(
                    self.config.deployment_profile.sandbox.workspace_root
                ),
                "allowed_workspace_roots": [
                    str(path)
                    for path in (
                        self.config.deployment_profile.sandbox
                        .allowed_workspace_roots
                    )
                ],
            },
        }

    def runtime_storage_state(self) -> dict[str, Any]:
        if self._runtime_storage_fault is None:
            return {
                "status": "healthy",
                "task_admission_allowed": True,
            }
        return {
            **self._runtime_storage_fault,
            "task_admission_allowed": False,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "robot_id": self.config.robot_id,
            "adapter": self.config.adapter,
            "dry_run": self.config.dry_run,
        }

    def task_capacity(self) -> dict[str, Any]:
        with self._task_lock:
            return self._task_capacity_locked()

    def active_tasks(self) -> list[dict[str, Any]]:
        with self._task_lock:
            return self._active_task_summaries_locked()

    def _wait_until_task_inactive(self, task_id: str, *, timeout_seconds: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            with self._task_lock:
                if task_id not in self._task_controls:
                    return
            time.sleep(0.01)

    def _record_result_events(
        self,
        task_id: str,
        session_id: str,
        result: dict[str, Any],
    ) -> None:
        planning = result.get("planning")
        if planning is not None and not self._has_event_type(task_id, "task.planned"):
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="task.planned",
                payload=planning,
            )

        safety = result.get("safety")
        if safety is not None and not self._has_event_type(task_id, "safety.decided"):
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type="safety.decided",
                payload=safety,
            )

        confirmation = result.get("confirmation")
        if isinstance(confirmation, dict):
            if confirmation.get("status") == "pending":
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type="confirmation.pending",
                    payload=confirmation,
                )
            elif confirmation.get("status") == "confirmed":
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type="confirmation.confirmed",
                    payload=confirmation,
                )

        execution = result.get("execution") or {}
        if not self._has_live_skill_events(task_id):
            for step in execution.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                event_type = "skill.succeeded" if step.get("status") == "succeeded" else "skill.failed"
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type=event_type,
                    payload={
                        "skill_name": step.get("skill_name"),
                        "status": step.get("status"),
                        "error": step.get("error"),
                        "attempt_count": step.get("attempt_count"),
                    },
                )

        with self._task_lock:
            control = self._task_controls.get(task_id)
            queue_record = self.task_queue.get(task_id)
            was_cancel_requested = (
                result.get("status") == "cancelled"
                or (control is not None and control.cancel_event.is_set())
                or (queue_record is not None and queue_record.status == "cancel_requested")
                or self._has_event_type(task_id, "task.cancel_requested")
            )
            pending_authorization = self._pending_authorization_for_task(
                session_id=session_id,
                task_id=task_id,
            )
            if (
                result.get("status") == "awaiting_confirmation"
                and pending_authorization is not None
                and not was_cancel_requested
            ):
                with self.runtime_state.transaction():
                    self._append_event(
                        task_id=task_id,
                        session_id=session_id,
                        type="task.awaiting_confirmation",
                        payload={
                            "status": "awaiting_confirmation",
                            "request_id": pending_authorization.get(
                                "request_id"
                            ),
                            "message": result.get("message"),
                            "result": result,
                        },
                    )
                    self.task_queue.update(
                        task_id,
                        status="awaiting_confirmation",
                        result=result,
                    )
                return

            raw_status = result.get("status")
            if raw_status == "awaiting_confirmation":
                terminal_outcome = replace(
                    build_robot_task_terminal_outcome(
                        "cancelled"
                        if was_cancel_requested
                        else "escalated"
                    ),
                    raw_status="awaiting_confirmation",
                )
            else:
                terminal_outcome = build_robot_task_terminal_outcome(
                    raw_status,
                    cancellation_requested=was_cancel_requested,
                )
            result["terminal_outcome"] = terminal_outcome.to_dict()
            with self.runtime_state.transaction():
                if (
                    raw_status == "awaiting_confirmation"
                    and was_cancel_requested
                    and pending_authorization is not None
                ):
                    decided_at = datetime.now(timezone.utc).isoformat()
                    try:
                        resolved_authorization = (
                            self.runtime_state.resolve_authorization_request(
                                str(pending_authorization["request_id"]),
                                status="cancelled",
                                decided_by=dict(
                                    pending_authorization.get(
                                        "requested_by"
                                    )
                                    or {}
                                ),
                                decided_at=decided_at,
                            )
                        )
                    except StaleRuntimeStateWrite:
                        resolved_authorization = pending_authorization
                    self._append_event(
                        task_id=task_id,
                        session_id=session_id,
                        type="authorization.cancelled",
                        payload={
                            "status": "cancelled",
                            "authorization": resolved_authorization,
                            "cancelled_at": decided_at,
                        },
                    )
                self._append_event(
                    task_id=task_id,
                    session_id=session_id,
                    type=terminal_outcome.event_type,
                    payload={
                        "status": terminal_outcome.status,
                        "raw_status": terminal_outcome.raw_status,
                        "terminal_outcome": terminal_outcome.to_dict(),
                        "message": result.get("message"),
                        "result": result,
                        "cancel_requested": was_cancel_requested,
                    },
                )
                self.task_queue.update(
                    task_id,
                    status=terminal_outcome.status,
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    result=result,
                )

    def _task_result_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.get("type") in ROBOT_TASK_TERMINAL_EVENT_TYPES:
                payload = event.get("payload") or {}
                result = payload.get("result")
                if isinstance(result, dict):
                    status = robot_task_terminal_status_from_event(event)
                    projected = dict(result)
                    if status is not None:
                        raw_status = projected.get("status")
                        if raw_status != status:
                            projected["raw_status"] = raw_status
                        projected["status"] = status
                    return projected
        return None

    def _has_live_skill_events(self, task_id: str) -> bool:
        live_types = {"skill.started", "skill.attempted", "skill.succeeded", "skill.failed"}
        return any(event.get("type") in live_types for event in self.events.events_for_task(task_id))

    def _has_event_type(self, task_id: str, event_type: str) -> bool:
        return any(event.get("type") == event_type for event in self.events.events_for_task(task_id))

    def _create_agent(
        self,
        *,
        task_id: str | None = None,
        session_id: str | None = None,
        cancellation_requested=None,
        execution_authorization: ExecutionAuthorization | None = None,
        operator: OperatorContext | None = None,
    ) -> FireClawAgent:
        resolved_session_id = session_id or self.config.default_session_id
        event_sink = None
        if task_id is not None:
            event_sink = lambda event_type, payload: self._append_event(
                task_id=task_id,
                session_id=resolved_session_id,
                type=event_type,
                payload=payload,
            )
        return FireClawAgent(
            robot=self.robot,
            memory=self.memory,
            dry_run=self.config.dry_run,
            available_sensors=set(self.config.available_sensors) if self.config.available_sensors else None,
            session_id=resolved_session_id,
            event_sink=event_sink,
            cancellation_requested=cancellation_requested,
            task_id=task_id,
            safety_memory_producer=self._safety_memory_producer,
            skill_memory_producer=self._skill_memory_producer,
            embodied_runtime_mode=self.config.embodied_runtime_mode,
            robot_memory_recorder=self.robot_memory_recorder,
            execution_authorization_authority=(
                self.execution_authorization_authority
            ),
            execution_authorization=execution_authorization,
            resource_lease_manager=self.resource_leases,
            authorization_use_recorder=(
                self.runtime_state.record_authorization_use
            ),
            capability_actor=(
                operator or operator_from_payload(None)
            ),
            robot_profile=self.robot_profile,
            deployment_profile=self.config.deployment_profile,
            extension_paths=self.config.extension_paths,
            plugin_configs=self.config.plugin_configs,
            plugin_services=self._extension_services(),
        )

    def _task_status(
        self,
        task_id: str,
        events: list[dict[str, Any]],
        result: dict[str, Any] | None,
        *,
        task_is_active: bool,
    ) -> str:
        queue_record = self.task_queue.get(task_id)
        terminal_status = robot_task_terminal_status_from_trace({
            "events": events,
            "result": result,
            "queue_record": (
                queue_record.to_dict()
                if queue_record is not None
                else None
            ),
        })
        if terminal_status is not None:
            return terminal_status
        if any(event.get("type") == "task.cancel_requested" for event in events):
            return "cancel_requested"
        if task_is_active:
            return "running"
        if queue_record is not None:
            return queue_record.status
        return "unknown"

    def _task_capacity_locked(self) -> dict[str, Any]:
        return {
            "active_execution_tasks": len(self._task_controls),
            "max_active_execution_tasks": self.config.max_active_execution_tasks,
            "available_execution_slots": max(
                0,
                self.config.max_active_execution_tasks - len(self._task_controls),
            ),
        }

    def _active_task_summaries_locked(self) -> list[dict[str, Any]]:
        summaries = []
        for control in self._task_controls.values():
            summaries.append(
                {
                    "task_id": control.task_id,
                    "session_id": control.session_id,
                    "command": control.command,
                    "started_at": control.started_at,
                    "cancel_requested": control.cancel_event.is_set(),
                }
            )
        return summaries

    def _append_event(
        self,
        *,
        task_id: str,
        session_id: str,
        type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = self.events.append(
            task_id=task_id,
            session_id=session_id,
            type=type,
            payload=payload,
        )

        def publish_committed_event() -> None:
            stream_event = StreamEvent(
                event_type=type,
                source=self.config.robot_id,
                robot_id=self.config.robot_id,
                task_id=task_id,
                mission_id=session_id,
                payload=payload,
            )
            self._event_bus.publish(stream_event)
            self._telemetry.record_event(stream_event)

        self.runtime_state.after_commit(publish_committed_event)
        return result

    def _publish_stream_event(
        self,
        event_type: str,
        *,
        task_id: str | None = None,
        mission_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Publish a StreamEvent to the EventBus and TelemetryTracker.

        Use this for direct StreamEvent emission without creating an
        EventLedger record first.  For events that also need ledger
        persistence, use ``_append_event`` instead (it already bridges
        to EventBus/Telemetry).
        """
        event = StreamEvent(
            event_type=event_type,
            source=self.config.robot_id,
            robot_id=self.config.robot_id,
            task_id=task_id,
            mission_id=mission_id,
            payload=payload or {},
        )
        self._event_bus.publish(event)
        self._telemetry.record_event(event)

    def _reconcile_stale_task_queue_records(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        recoverable = (
            self.agent_loop_checkpoints.recoverable()
            if isinstance(
                self.robot_agent_runtime,
                RobotAgentDeliberationRuntime,
            )
            else {}
        )
        for record in self.task_queue.list_records():
            if record.is_terminal:
                continue
            events = self.events.events_for_task(record.task_id)
            if record.status == "awaiting_confirmation":
                pending_payload = self._pending_authorization_for_task(
                    session_id=record.session_id,
                    task_id=record.task_id,
                )
                if pending_payload is not None:
                    request = AuthorizationRequest.from_dict(
                        pending_payload
                    )
                    if request.is_expired(now):
                        recovery_operator = {
                            "operator_id": "gateway-recovery",
                            "display_name": "Gateway Recovery",
                            "role": "system",
                            "control_scopes": [],
                            "source": "gateway_recovery",
                        }
                        try:
                            with self.runtime_state.transaction():
                                self.runtime_state.resolve_authorization_request(
                                    request.request_id,
                                    status="expired",
                                    decided_by=recovery_operator,
                                    decided_at=now,
                                )
                                self._append_event(
                                    task_id=record.task_id,
                                    session_id=record.session_id,
                                    type="authorization.expired",
                                    payload={
                                        "status": "expired",
                                        "authorization": request.to_dict(),
                                        "expired_at": now,
                                        "source": "gateway_recovery",
                                    },
                                )
                                self._terminalize_waiting_task(
                                    task_id=record.task_id,
                                    session_id=record.session_id,
                                    status="escalated",
                                    message=(
                                        "Gateway 恢复时发现执行授权请求"
                                        "已过期；任务未执行。"
                                    ),
                                    reason_code="authorization_expired",
                                )
                        except StaleRuntimeStateWrite:
                            pass
                    continue

            resume_scheduled_index = -1
            resume_started_index = -1
            resume_payload: dict[str, Any] | None = None
            for index, event in enumerate(events):
                if event.get("type") == "task.resume_scheduled":
                    payload = event.get("payload")
                    if isinstance(payload, dict):
                        resume_scheduled_index = index
                        resume_payload = payload
                elif event.get("type") == "task.resume_started":
                    resume_started_index = index
            if (
                resume_payload is not None
                and resume_scheduled_index > resume_started_index
            ):
                execution_payload = resume_payload.get(
                    "execution_authorization"
                )
                if isinstance(execution_payload, dict):
                    execution_authorization = (
                        ExecutionAuthorization.from_dict(
                            execution_payload
                        )
                    )
                    try:
                        authorization_expires_at = datetime.fromisoformat(
                            execution_authorization.expires_at.replace(
                                "Z",
                                "+00:00",
                            )
                        )
                    except ValueError:
                        authorization_expires_at = datetime.min.replace(
                            tzinfo=timezone.utc
                        )
                    if authorization_expires_at > datetime.now(timezone.utc):
                        structured_task = resume_payload.get(
                            "structured_task"
                        )
                        if not isinstance(structured_task, dict):
                            structured_task = next(
                                (
                                    event.get("payload")
                                    for event in reversed(events)
                                    if event.get("type")
                                    == "task.structured_received"
                                    and isinstance(
                                        event.get("payload"),
                                        dict,
                                    )
                                ),
                                None,
                            )
                        if isinstance(structured_task, dict):
                            structured_task = dict(structured_task)
                            structured_task["execution_authorization"] = (
                                execution_authorization.to_dict()
                            )
                        control = TaskControl(
                            task_id=record.task_id,
                            session_id=record.session_id,
                            command=record.command,
                            started_at=(
                                record.started_at or record.created_at
                            ),
                            structured_task=structured_task,
                            execution_authorization=(
                                execution_authorization
                            ),
                        )
                        with self._task_lock:
                            self._task_controls[record.task_id] = control
                        self._start_task_worker(
                            control,
                            operator_from_payload(
                                resume_payload.get("requested_by")
                            ),
                            resumed=True,
                        )
                        continue
                    with self.runtime_state.transaction():
                        self._append_event(
                            task_id=record.task_id,
                            session_id=record.session_id,
                            type="task.resume_rejected",
                            payload={
                                "status": "escalated",
                                "reason_code": (
                                    "execution_authorization_expired"
                                ),
                            },
                        )
                        self._terminalize_waiting_task(
                            task_id=record.task_id,
                            session_id=record.session_id,
                            status="escalated",
                            message=(
                                "Gateway 恢复前执行授权已过期；"
                                "任务未重新执行。"
                            ),
                            reason_code=(
                                "execution_authorization_expired"
                            ),
                        )
                    continue
            structured_payload = next(
                (
                    event.get("payload")
                    for event in reversed(events)
                    if event.get("type") == "task.structured_received"
                    and isinstance(event.get("payload"), dict)
                ),
                None,
            )
            task_object = (
                StructuredRobotTask.from_dict(structured_payload)
                if isinstance(structured_payload, dict)
                else None
            )
            checkpoint = None
            if task_object is not None:
                robot_id = task_object.robot_id or self.config.robot_id
                checkpoint = recoverable.get(
                    f"robot:{robot_id}:task:{task_object.task_id}"
                )
            with self._task_lock:
                has_capacity = (
                    len(self._task_controls)
                    < self.config.max_active_execution_tasks
                )
            if checkpoint is not None and has_capacity:
                operator_payload = next(
                    (
                        event.get("payload")
                        for event in reversed(events)
                        if event.get("type") == "operator.identified"
                        and isinstance(event.get("payload"), dict)
                    ),
                    None,
                )
                control = TaskControl(
                    task_id=record.task_id,
                    session_id=record.session_id,
                    command=record.command,
                    started_at=record.started_at or record.created_at,
                    structured_task=structured_payload,
                )
                with self._task_lock:
                    self._task_controls[record.task_id] = control
                self._append_event(
                    task_id=record.task_id,
                    session_id=record.session_id,
                    type="task.resume_scheduled",
                    payload={
                        "status": "accepted",
                        "task_id": record.task_id,
                        "checkpoint_key": checkpoint.checkpoint_key,
                        "pending_operation_id": (
                            checkpoint.pending_operation.operation_id
                            if checkpoint.pending_operation is not None
                            else None
                        ),
                    },
                )
                self._start_task_worker(
                    control,
                    operator_from_payload(operator_payload),
                    resumed=True,
                )
                continue

            lost_record = self.task_queue.update(
                record.task_id,
                status="lost",
                ended_at=now,
                error="Gateway restarted before terminal result.",
            )
            self._append_event(
                task_id=lost_record.task_id,
                session_id=lost_record.session_id,
                type="task.lost",
                payload={
                    "status": "lost",
                    "task_id": lost_record.task_id,
                    "message": "Gateway restarted before terminal result; task was not replayed.",
                    "terminal_outcome": (
                        build_robot_task_terminal_outcome("lost").to_dict()
                    ),
                    "queue_record": lost_record.to_dict(),
                },
            )

    def _handler_class(self):
        gateway = self

        class GatewayRequestHandler(BaseHTTPRequestHandler):
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
                if parsed.path == "/events/stream":
                    gateway._stream_events(self, parsed)
                    return
                gateway._handle_get(self)

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

        return GatewayRequestHandler

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
        self._network_guard.record_auth_failure(client_host)
        body = json.dumps({"error": "Unauthorized"}, ensure_ascii=False).encode("utf-8")
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header("WWW-Authenticate", "Bearer")
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return None

    def _stream_events(
        self,
        handler: BaseHTTPRequestHandler,
        parsed: Any,
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
            handler.end_headers()

            after_seq = _sse_after_sequence(handler, parsed)
            sent_sequences: set[int] = set()
            if after_seq is not None:
                for event in self._event_bus.get_recent_events(
                    after_sequence=after_seq
                ):
                    handler.wfile.write(
                        event.to_sse_format().encode("utf-8")
                    )
                    handler.wfile.flush()
                    sent_sequences.add(event.sequence)

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

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._write_json(handler, HTTPStatus.OK, self.health())
            return
        if parsed.path == "/state":
            self._write_json(handler, HTTPStatus.OK, self.state())
            return
        if parsed.path == "/resource-admission":
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.resource_admission_state(),
            )
            return
        if parsed.path == "/skills":
            self._write_json(handler, HTTPStatus.OK, self.list_skills(_first(query, "session_id")))
            return
        if parsed.path == "/memory/recent":
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.recent_memory(
                    session_id=_first(query, "session_id"),
                    limit=_int_query(query, "limit", 5),
                ),
            )
            return
        if parsed.path == "/memory/replication":
            try:
                from fireclaw_core.memory.replication_security import (
                    REPLICATION_AUTH_HEADER,
                    ReplicationAuthMetadata,
                    ReplicationRequestScope,
                    decode_auth_header,
                )
                mission_id = _first(query, "mission_id")
                runtime_mode = _first(query, "runtime_mode")
                if not mission_id or not runtime_mode:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "mission_id and runtime_mode are required")
                    return
                scope = ReplicationRequestScope(
                    mission_id=mission_id,
                    runtime_mode=runtime_mode,
                    cursor=_int_query(query, "cursor", 0),
                    limit=_int_query(query, "limit", 200),
                )
                auth_header = handler.headers.get(REPLICATION_AUTH_HEADER, "")
                if not auth_header:
                    self._write_error(handler, HTTPStatus.UNAUTHORIZED, "replication auth header required")
                    return
                request_auth = decode_auth_header(auth_header)
                result = self.export_memory_replication(scope=scope, request_auth=request_auth)
            except ValueError as exc:
                self._write_error(handler, HTTPStatus.FORBIDDEN, "replication verification failed")
                return
            self._write_json(handler, HTTPStatus.OK, result)
            return
        if parsed.path == "/entity-memory/tools":
            if self.entity_memory_tools is None:
                self._write_error(handler, HTTPStatus.NOT_FOUND, "Entity memory is not configured.")
                return
            self._write_json(
                handler,
                HTTPStatus.OK,
                {"tools": self.entity_memory_tools.tool_schemas()},
            )
            return
        if parsed.path == "/events/recent":
            self._write_json(
                handler,
                HTTPStatus.OK,
                self.recent_events(
                    session_id=_first(query, "session_id"),
                    limit=_int_query(query, "limit", 20),
                ),
            )
            return
        if parsed.path == "/events":
            task_id = _first(query, "task_id")
            limit = _int_query(query, "limit", 20)
            if task_id:
                events = self.events.events_for_task(task_id)
            else:
                events = self.events.latest_events(limit=limit)
            self._write_json(handler, HTTPStatus.OK, {"events": events})
            return
        task_events_id = _task_events_path(parsed.path)
        if task_events_id is not None:
            self._write_json(
                handler,
                HTTPStatus.OK,
                {"task_id": task_events_id, "events": self.events.events_for_task(task_events_id)},
            )
            return
        task_id = _task_path(parsed.path)
        if task_id is not None:
            self._write_json(handler, HTTPStatus.OK, self.task_trace(task_id))
            return
        self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")

    def _handle_post(
        self,
        handler: BaseHTTPRequestHandler,
        *,
        principal: AuthenticatedGatewayPrincipal,
    ) -> None:
        parsed = urlparse(handler.path)
        operator = principal.operator
        try:
            payload = self._read_json(handler)
            task_cancel_id = _task_cancel_path(parsed.path)
            if task_cancel_id is not None:
                result = self.cancel_task(task_cancel_id, operator=operator)
                if result["status"] == "not_found":
                    status = HTTPStatus.NOT_FOUND
                elif result["status"] == "denied":
                    status = HTTPStatus.FORBIDDEN
                else:
                    status = HTTPStatus.OK
                self._write_json(handler, status, result)
                return
            if parsed.path == "/tasks":
                command = payload.get("command")
                if not isinstance(command, str) or not command.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'command' is required.")
                    return
                structured_task = payload.get("structured_task")
                if structured_task is not None:
                    if not isinstance(structured_task, dict):
                        self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'structured_task' must be an object when provided.")
                        return
                    raw_skills = structured_task.get("required_skills")
                    if raw_skills is not None and not isinstance(raw_skills, list):
                        self._write_error(handler, HTTPStatus.BAD_REQUEST, "required_skills must be a list")
                        return
                    raw_allowed_skills = structured_task.get("allowed_skills")
                    if raw_allowed_skills is not None and not isinstance(raw_allowed_skills, list):
                        self._write_error(handler, HTTPStatus.BAD_REQUEST, "allowed_skills must be a list")
                        return
                    task_object = StructuredRobotTask.from_dict(structured_task)
                    structured_task_errors = validate_structured_robot_task(task_object)
                    if structured_task_errors:
                        self._write_error(
                            handler,
                            HTTPStatus.BAD_REQUEST,
                            "; ".join(structured_task_errors),
                        )
                        return
                result = self.submit_agent(
                    command,
                    session_id=_payload_session(payload, self.config.default_session_id),
                    operator=operator,
                    dedupe_key=_optional_payload_string(payload, "dedupe_key"),
                    structured_task=structured_task,
                )
                status = _submission_status(result)
                self._write_json(handler, status, result)
                return
            if parsed.path == "/entity-memory/tools/call":
                if self.entity_memory_tools is None:
                    self._write_error(handler, HTTPStatus.NOT_FOUND, "Entity memory is not configured.")
                    return
                mission_id = payload.get("mission_id")
                name = payload.get("name")
                arguments = payload.get("arguments", {})
                if not isinstance(mission_id, str) or not mission_id.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'mission_id' is required.")
                    return
                if not isinstance(name, str) or not name.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'name' is required.")
                    return
                if not isinstance(arguments, dict):
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'arguments' must be an object.")
                    return
                try:
                    result = self.entity_memory_tools.execute(
                        name.strip(),
                        arguments,
                        mission_id=mission_id.strip(),
                    )
                except (TypeError, ValueError) as exc:
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                    return
                self._write_json(handler, HTTPStatus.OK, result)
                return
            if parsed.path == "/emergency-stop":
                result = self.emergency_stop(
                    session_id=_payload_session(payload, self.config.default_session_id),
                    operator=operator,
                    reason=_optional_payload_string(payload, "reason"),
                )
                status = HTTPStatus.FORBIDDEN if result["status"] == "denied" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return
            if parsed.path == "/resource-admission/recovery/request":
                result = self.request_resource_admission_recovery(
                    session_id=_payload_session(
                        payload,
                        self.config.default_session_id,
                    ),
                    operator=operator,
                    reason=_optional_payload_string(payload, "reason"),
                )
                response_status = {
                    "pending_confirmation": HTTPStatus.CREATED,
                    "denied": HTTPStatus.FORBIDDEN,
                    "invalid": HTTPStatus.BAD_REQUEST,
                    "not_frozen": HTTPStatus.CONFLICT,
                    "blocked": HTTPStatus.CONFLICT,
                }.get(str(result.get("status")), HTTPStatus.CONFLICT)
                self._write_json(handler, response_status, result)
                return
            if parsed.path == "/resource-admission/recovery/confirm":
                request_id = _optional_payload_string(payload, "request_id")
                confirmation_phrase = _optional_payload_string(
                    payload,
                    "confirmation_phrase",
                )
                if request_id is None or confirmation_phrase is None:
                    self._write_error(
                        handler,
                        HTTPStatus.BAD_REQUEST,
                        "Fields 'request_id' and 'confirmation_phrase' are required.",
                    )
                    return
                result = self.confirm_resource_admission_recovery(
                    request_id=request_id,
                    confirmation_phrase=confirmation_phrase,
                    operator=operator,
                )
                response_status = {
                    "recovered": HTTPStatus.OK,
                    "denied": HTTPStatus.FORBIDDEN,
                    "expired": HTTPStatus.GONE,
                    "not_found": HTTPStatus.NOT_FOUND,
                    "blocked": HTTPStatus.CONFLICT,
                }.get(str(result.get("status")), HTTPStatus.CONFLICT)
                self._write_json(handler, response_status, result)
                return
            if parsed.path == "/confirm":
                session_id = _payload_session(payload, self.config.default_session_id)
                result = self.confirm_task(
                    session_id=session_id,
                    operator=operator,
                )
                status = str(result.get("status") or "denied")
                if status in {"denied", "expired", "blocked"}:
                    response_status = HTTPStatus.FORBIDDEN
                elif status == "busy":
                    response_status = HTTPStatus.CONFLICT
                else:
                    response_status = _submission_status(result)
                self._write_json(
                    handler,
                    response_status,
                    result,
                )
                return
            if parsed.path == "/cancel":
                result = self.submit_agent(
                    "取消",
                    session_id=_payload_session(payload, self.config.default_session_id),
                    operator=operator,
                )
                self._write_json(
                    handler,
                    _submission_status(result),
                    result,
                )
                return
            self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except GatewayRequestBodyError as exc:
            self._write_error(handler, exc.status, str(exc))

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


def _first(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    return values[0]


def _sse_after_sequence(
    handler: BaseHTTPRequestHandler,
    parsed: Any,
) -> int | None:
    raw = handler.headers.get("Last-Event-ID")
    if raw is None:
        raw = parse_qs(parsed.query).get("after_sequence", [None])[0]
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    value = _first(query, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _payload_session(payload: dict[str, Any], default: str) -> str:
    value = payload.get("session_id")
    if isinstance(value, str) and value.strip():
        return value
    return default


def _optional_payload_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _submission_status(result: dict[str, Any]) -> HTTPStatus:
    if result.get("status") == "busy":
        return HTTPStatus.CONFLICT
    if result.get("status") == "denied":
        return HTTPStatus.FORBIDDEN
    if result.get("status") == "unavailable":
        return HTTPStatus.SERVICE_UNAVAILABLE
    return HTTPStatus.ACCEPTED


def _runtime_storage_error_code(exc: sqlite3.Error | OSError) -> str:
    message = str(exc).lower()
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "runtime_storage_full"
    if "database or disk is full" in message or "disk full" in message:
        return "runtime_storage_full"
    if "locked" in message or "busy" in message:
        return "runtime_database_locked"
    if "malformed" in message or "corrupt" in message:
        return "runtime_database_corrupt"
    if isinstance(exc, OSError) and exc.errno in {
        errno.EIO,
        errno.EROFS,
        errno.EDQUOT,
    }:
        return "runtime_storage_io_error"
    return "runtime_state_unavailable"


def _risk_level_from_confirmation(confirmation: Any) -> str:
    if not isinstance(confirmation, dict):
        return "low"
    reasons = confirmation.get("reasons")
    if not isinstance(reasons, list):
        return "low"
    text = " ".join(str(reason).lower() for reason in reasons)
    if "critical" in text:
        return "critical"
    if "high" in text or "real robot" in text:
        return "high"
    return "low"


def _agent_tool_approval_from_result(
    result: dict[str, Any],
) -> dict[str, Any] | None:
    deliberation = result.get("robot_agent_deliberation")
    observations = (
        deliberation.get("observations")
        if isinstance(deliberation, dict)
        else None
    )
    if not isinstance(observations, list):
        return None
    for observation in reversed(observations):
        if (
            not isinstance(observation, dict)
            or observation.get("operation") != "execute_agent_tool"
            or observation.get("status") != "approval_required"
        ):
            continue
        output = observation.get("output")
        approval = (
            output.get("approval_request")
            if isinstance(output, dict)
            else None
        )
        if not isinstance(approval, dict):
            return None
        scope_hash = approval.get("scope_hash")
        action = approval.get("authorized_action")
        if (
            not isinstance(scope_hash, str)
            or not scope_hash
            or not isinstance(action, dict)
            or not isinstance(action.get("skill_name"), str)
            or not isinstance(action.get("inputs_hash"), str)
            or not isinstance(action.get("action_hash"), str)
        ):
            return None
        return {
            "scope_hash": scope_hash,
            "authorized_action": dict(action),
        }
    return None


def _task_events_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "events":
        return parts[1]
    return None


def _task_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 2 and parts[0] == "tasks":
        return parts[1]
    return None


def _task_cancel_path(path: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) == 3 and parts[0] == "tasks" and parts[2] == "cancel":
        return parts[1]
    return None



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FireClaw local HTTP gateway.")
    parser.add_argument("--config", type=Path, default=None, help="Path to fireclaw.toml config file.")
    parser.add_argument(
        "--runtime-root",
        default=None,
        help=(
            "Stable process working directory. Defaults to the config "
            "directory, FIRECLAW_HOME, or ~/.fireclaw."
        ),
    )
    parser.add_argument("--host", default=None, help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port.")
    parser.add_argument("--adapter", choices=ADAPTER_CHOICES, default=None)
    parser.add_argument("--robot-id", default=None)
    parser.add_argument("--ros1-config", default=None)
    parser.add_argument("--memory-path", default=None)
    parser.add_argument("--event-path", default=None)
    parser.add_argument("--task-queue-path", default=None)
    parser.add_argument(
        "--runtime-state-path",
        default=None,
        help="Path to the authoritative SQLite runtime database.",
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--max-active-execution-tasks", type=int, default=None)
    parser.add_argument("--available-sensor", action="append", default=None)
    parser.add_argument(
        "--api-token",
        default=None,
        help=(
            "Gateway bearer token. Prefer [robot_gateway].api_token or "
            "FIRECLAW_GATEWAY_TOKEN so the secret is not exposed in process args."
        ),
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        default=None,
        help="Enable TLS for the Robot Gateway listener.",
    )
    parser.add_argument("--tls-cert-file", default=None)
    parser.add_argument("--tls-key-file", default=None)
    parser.add_argument("--tls-ca-file", default=None)
    parser.add_argument(
        "--tls-require-client-cert",
        action="store_true",
        default=None,
        help="Require a client certificate signed by --tls-ca-file.",
    )
    parser.add_argument("--real-run", action="store_true", default=None)
    parser.add_argument("--robot-agent", action="store_true", default=None, help="Enable robot-local agent planning for structured tasks.")
    parser.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default=None)
    parser.add_argument("--robot-agent-provider-base-url", default=None)
    parser.add_argument("--robot-agent-provider-api-key", default=None)
    parser.add_argument("--robot-agent-model", default=None)
    parser.add_argument("--robot-agent-catalog", default=None)
    parser.add_argument("--robot-profile", default=None, help="Path to robot capability profile TOML.")
    parser.add_argument("--embodied-memory-path", default=None)
    parser.add_argument("--embodied-memory-index", default=None)
    parser.add_argument(
        "--embodied-runtime-mode",
        choices=("real", "simulation", "replay"),
        default=None,
    )
    args = parser.parse_args(argv)

    from fireclaw_core.gateway.config import find_config, load_config, merge_config

    cfg: dict[str, Any] = {}
    config_path = find_config(args.config)
    if config_path is not None:
        cfg = load_config(config_path)
    merged = merge_config(
        cfg,
        {
            "runtime_root": args.runtime_root,
            "robot_gateway_host": args.host,
            "robot_gateway_port": args.port,
            "robot_gateway_adapter": args.adapter,
            "robot_gateway_robot_id": args.robot_id,
            "robot_gateway_ros1_config": args.ros1_config,
            "robot_gateway_memory_path": args.memory_path,
            "robot_gateway_event_path": args.event_path,
            "robot_gateway_task_queue_path": args.task_queue_path,
            "robot_gateway_runtime_state_path": args.runtime_state_path,
            "robot_gateway_dry_run": False if args.real_run else None,
            "robot_gateway_available_sensors": args.available_sensor,
            "robot_gateway_default_session_id": args.session_id,
            "robot_gateway_max_active_execution_tasks": args.max_active_execution_tasks,
            "robot_gateway_api_token": args.api_token,
            "robot_gateway_tls_enabled": args.tls if args.tls else None,
            "robot_gateway_tls_cert_file": args.tls_cert_file,
            "robot_gateway_tls_key_file": args.tls_key_file,
            "robot_gateway_tls_ca_file": args.tls_ca_file,
            "robot_gateway_tls_require_client_cert": (
                args.tls_require_client_cert
                if args.tls_require_client_cert
                else None
            ),
            "robot_agent_enabled": args.robot_agent if args.robot_agent else None,
            "robot_agent_planner": args.robot_agent_planner,
            "robot_agent_provider_base_url": args.robot_agent_provider_base_url,
            "robot_agent_provider_api_key": args.robot_agent_provider_api_key,
            "robot_agent_model": args.robot_agent_model,
            "robot_agent_model_catalog_path": args.robot_agent_catalog,
            "plugin_paths": None,
            "plugin_configs": None,
            "robot_gateway_profile_path": args.robot_profile,
            "robot_gateway_embodied_memory_path": args.embodied_memory_path,
            "robot_gateway_embodied_memory_index": args.embodied_memory_index,
            "robot_gateway_embodied_runtime_mode": args.embodied_runtime_mode,
        },
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
        return _run_robot_gateway(merged, args)


def _run_robot_gateway(
    merged: dict[str, Any],
    args: argparse.Namespace,
) -> int:
    def configured(key: str, default: Any) -> Any:
        value = merged.get(key)
        return default if value is None else value

    robot_workspace_root = (
        Path("data") / "robot" / "agent-workspace"
    ).resolve(strict=False)
    sensors = merged.get("robot_gateway_available_sensors") or ()
    extension_paths = merged.get("plugin_paths") or ("extensions",)
    if isinstance(extension_paths, str):
        extension_paths = (extension_paths,)
    plugin_configs = merged.get("plugin_configs") or {}
    if not isinstance(plugin_configs, dict):
        raise ValueError("plugin_configs must be an object")

    gateway = FireClawGateway(
        # The deployment profile is immutable for the lifetime of this
        # Gateway process; changing mode requires a restart.
        GatewayConfig(
            host=str(configured("robot_gateway_host", "127.0.0.1")),
            port=int(configured("robot_gateway_port", 8765)),
            adapter=str(configured("robot_gateway_adapter", "dry-run")),
            robot_id=str(configured("robot_gateway_robot_id", "fireclaw-gateway")),
            ros1_config_path=merged.get("robot_gateway_ros1_config"),
            memory_path=str(configured("robot_gateway_memory_path", "memory/fireclaw-gateway.jsonl")),
            event_path=str(configured("robot_gateway_event_path", "memory/fireclaw-gateway-events.jsonl")),
            task_queue_path=str(configured("robot_gateway_task_queue_path", "memory/fireclaw-gateway-tasks.jsonl")),
            runtime_state_path=merged.get("robot_gateway_runtime_state_path"),
            dry_run=bool(configured("robot_gateway_dry_run", True)),
            available_sensors=tuple(str(sensor) for sensor in sensors),
            default_session_id=str(configured("robot_gateway_default_session_id", "default")),
            max_active_execution_tasks=max(
                1,
                int(configured("robot_gateway_max_active_execution_tasks", 1)),
            ),
            api_token=resolve_gateway_api_token(
                merged.get("robot_gateway_api_token"),
            ),
            tls=GatewayTlsServerConfig(
                enabled=bool(configured("robot_gateway_tls_enabled", False)),
                cert_file=merged.get("robot_gateway_tls_cert_file"),
                key_file=merged.get("robot_gateway_tls_key_file"),
                ca_file=merged.get("robot_gateway_tls_ca_file"),
                require_client_cert=bool(
                    merged.get(
                        "robot_gateway_tls_require_client_cert",
                        False,
                    )
                ),
            ),
            network=gateway_network_policy_from_config(
                merged.get("network")
            ),
            robot_agent_enabled=bool(configured("robot_agent_enabled", False)),
            robot_agent_planner=str(configured("robot_agent_planner", "deterministic")),
            robot_agent_provider_base_url=merged.get("robot_agent_provider_base_url"),
            robot_agent_provider_api_key=merged.get("robot_agent_provider_api_key"),
            robot_agent_model=merged.get("robot_agent_model"),
            robot_agent_model_catalog_path=merged.get("robot_agent_model_catalog_path"),
            extension_paths=tuple(str(path) for path in extension_paths),
            plugin_configs={
                str(plugin_id): dict(config)
                for plugin_id, config in plugin_configs.items()
                if isinstance(config, dict)
            },
            robot_profile_path=merged.get("robot_gateway_profile_path"),
            embodied_memory_path=merged.get("robot_gateway_embodied_memory_path"),
            embodied_memory_index_path=merged.get("robot_gateway_embodied_memory_index"),
            embodied_runtime_mode=merged.get("robot_gateway_embodied_runtime_mode"),
            deployment_profile=deployment_profile_from_config(
                merged.get("deployment"),
                role="robot_agent",
                default_workspace_root=robot_workspace_root,
                allowed_workspace_roots=(robot_workspace_root,),
                path_base=Path.cwd(),
            ),
        )
    )
    print(
        json.dumps(
            {
                "status": "starting",
                "base_url": gateway.base_url,
                "adapter": gateway.config.adapter,
                "robot_id": gateway.config.robot_id,
                "runtime_root": str(Path.cwd().resolve(strict=False)),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
