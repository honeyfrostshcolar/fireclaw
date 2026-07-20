from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from queue import Empty as QueueEmpty, Queue
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from fireclaw_core.gateway.method_scopes import authorize_method

from fireclaw_core.monitoring.stream_events import EventBus, StreamEvent, TelemetryTracker

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.gateway.control import AuthorizationRequest, ControlPolicy, OperatorContext, operator_from_payload
from fireclaw_core.monitoring.event_ledger import EventLedger
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer, EmbodiedMemoryStore
from fireclaw_core.memory.robot_memory import RobotMemoryRecorder
from fireclaw_core.memory.working_memory import EmbodiedWorkingMemory
from fireclaw_core.memory.entity_memory import EntityMemoryService
from fireclaw_core.memory.entity_extraction import EntityExtractionPipeline
from fireclaw_core.memory.entity_tools import EntityMemoryTools
from fireclaw_core.memory.reconciliation import EmbodiedMemoryReplicationExporter
from fireclaw_core.planner.planner_builder import build_provider_runtime
from fireclaw_core.agent.robot_agent import (
    DeterministicRobotAgentPlanner,
    LLMRobotAgentPlanner,
    RobotAgentRuntime,
)
from fireclaw_core.agent.skill_inventory import build_robot_skill_inventory
from fireclaw_core.agent.robot_tools import build_robot_skill_tools
from fireclaw_core.execution.runtime_config import ADAPTER_CHOICES, create_robot_adapter
from fireclaw_core.task.task_queue import JsonlTaskQueue
from fireclaw_core.task.task_contract import StructuredRobotTask, validate_structured_robot_task
from fireclaw_core.task.task_state import project_task_state


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
    workspace_skills_dir: str | None = "skills"
    dry_run: bool = True
    available_sensors: tuple[str, ...] = ()
    default_session_id: str = "default"
    max_active_execution_tasks: int = 1
    authorization_expiry_seconds: int = 300
    api_token: str | None = None
    robot_agent_enabled: bool = False
    robot_agent_planner: str = "deterministic"
    robot_agent_provider_base_url: str | None = None
    robot_agent_provider_api_key: str | None = None
    robot_agent_model: str | None = None
    robot_profile_path: str | None = None
    embodied_memory_path: str | None = None
    embodied_memory_index_path: str | None = None
    embodied_runtime_mode: str | None = None


@dataclass
class TaskControl:
    task_id: str
    session_id: str
    command: str
    started_at: str
    structured_task: dict[str, Any] | None = None
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
    from dataclasses import replace

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
    def __init__(self, config: GatewayConfig) -> None:
        self.robot_profile = load_gateway_robot_profile(config)
        resolved_config = resolve_gateway_config_with_profile(config, self.robot_profile)
        self.config = resolved_config
        self.robot = create_robot_adapter(resolved_config.adapter, resolved_config.robot_id, config_path=resolved_config.ros1_config_path)
        apply_gateway_dry_run_to_robot(self.robot, resolved_config.dry_run)
        attach_profile_sensor_discovery(self.robot, self.robot_profile)
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
        self.events = EventLedger(resolved_config.event_path)
        self.task_queue = JsonlTaskQueue(resolved_config.task_queue_path)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._event_lock = threading.Lock()
        self._task_threads: dict[str, threading.Thread] = {}
        self._task_controls: dict[str, TaskControl] = {}
        self._task_lock = threading.Lock()
        self._emergency_stop = EmergencyStopState()
        self._authorization_lock = threading.Lock()
        self._pending_authorizations_by_session: dict[str, AuthorizationRequest] = {}
        self._event_bus = EventBus()
        self._telemetry = TelemetryTracker()
        self._reconcile_stale_task_queue_records()
        self.robot_agent_runtime = self._build_robot_agent_runtime()

    def _validate_robot_profile(self) -> None:
        if self.robot_profile is None:
            return
        from fireclaw_core.agent.robot_profile import validate_robot_capability_profile
        from fireclaw_core.execution.skills import create_default_skill_registry
        from fireclaw_core.ros.ros1_config import load_ros1_adapter_config

        ros1_config = None
        if self.robot_profile.ros1_config is not None:
            ros1_config = load_ros1_adapter_config(self.robot_profile.ros1_config)
        registry = create_default_skill_registry(self.robot)
        errors = validate_robot_capability_profile(
            self.robot_profile,
            registry,
            ros1_config=ros1_config,
        )
        if errors:
            raise ValueError("Invalid robot profile: " + "; ".join(errors))

    @property
    def base_url(self) -> str:
        if self._server is None:
            return f"http://{self.config.host}:{self.config.port}"
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        if self._server is not None:
            return
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        handler_class = self._handler_class()
        self._server = ThreadingHTTPServer((self.config.host, self.config.port), handler_class)
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
    ) -> dict[str, Any]:
        task_id = f"task-{uuid4().hex}"
        resolved_session_id = session_id or self.config.default_session_id
        resolved_operator = operator or operator_from_payload(None)
        control_decision = ControlPolicy().evaluate(resolved_operator, "task.submit")
        duplicate = self.task_queue.find_non_terminal_by_dedupe_key(dedupe_key)
        if duplicate is not None:
            return {
                "status": "duplicate",
                "task_id": duplicate.task_id,
                "session_id": duplicate.session_id,
                "dedupe_key": dedupe_key,
                "message": "任务已存在，返回现有未完成任务。",
            }
        started_at = datetime.now(timezone.utc).isoformat()
        self.task_queue.create(
            task_id=task_id,
            session_id=resolved_session_id,
            command=command,
            created_at=started_at,
            dedupe_key=dedupe_key,
        )
        control = TaskControl(
            task_id=task_id,
            session_id=resolved_session_id,
            command=command,
            started_at=started_at,
            structured_task=structured_task,
        )
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
        if control_decision.status != "allow":
            with self._task_lock:
                self._task_controls.pop(task_id, None)
            self.task_queue.update(
                task_id,
                status="denied",
                ended_at=datetime.now(timezone.utc).isoformat(),
                error="Operator is not authorized to submit tasks.",
            )
            return {
                "status": "denied",
                "task_id": task_id,
                "session_id": resolved_session_id,
                "message": "操作员没有权限提交任务。",
                "control": control_decision.to_dict(),
            }

        def worker() -> None:
            try:
                self.task_queue.update(
                    task_id,
                    status="running",
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                self._publish_stream_event(
                    "task.running",
                    task_id=task_id,
                )
                self._execute_agent_task(
                    command=command,
                    session_id=resolved_session_id,
                    task_id=task_id,
                    record_received=False,
                    cancellation_requested=control.cancel_event.is_set,
                    operator=resolved_operator,
                    structured_task=control.structured_task,
                )
            except Exception as exc:
                failed_result = {
                    "status": "failed",
                    "task_id": task_id,
                    "session_id": resolved_session_id,
                    "message": str(exc),
                }
                self.task_queue.update(
                    task_id,
                    status="failed",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    error=str(exc),
                    result=failed_result,
                )
                self._append_event(
                    task_id=task_id,
                    session_id=resolved_session_id,
                    type="task.failed",
                    payload={
                        "status": "failed",
                        "message": str(exc),
                        "result": failed_result,
                    },
                )
            finally:
                with self._task_lock:
                    self._task_threads.pop(task_id, None)
                    self._task_controls.pop(task_id, None)

        thread = threading.Thread(target=worker, daemon=True, name=f"fireclaw-task-{task_id}")
        with self._task_lock:
            self._task_threads[task_id] = thread
        thread.start()
        return {
            "status": "accepted",
            "task_id": task_id,
            "session_id": resolved_session_id,
            "message": "任务已接收，正在后台执行。",
        }

    def cancel_task(self, task_id: str, operator: OperatorContext | None = None) -> dict[str, Any]:
        resolved_operator = operator or operator_from_payload(None)
        control_decision = ControlPolicy().evaluate(resolved_operator, "task.cancel")
        if control_decision.status == "allow":
            with self._task_lock:
                control = self._task_controls.get(task_id)
                queue_record = self.task_queue.get(task_id)
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

    def _build_robot_agent_runtime(self) -> RobotAgentRuntime | None:
        if not self.config.robot_agent_enabled:
            return None
        if self.config.robot_agent_planner == "deterministic":
            return RobotAgentRuntime(planner=DeterministicRobotAgentPlanner())
        if self.config.robot_agent_planner == "llm":
            runtime = build_provider_runtime(
                provider_base_url=self.config.robot_agent_provider_base_url,
                provider_api_key=self.config.robot_agent_provider_api_key,
                model=self.config.robot_agent_model,
            )
            return RobotAgentRuntime(
                planner=LLMRobotAgentPlanner(
                    runtime,
                    memory_tool_executor=(
                        self.entity_memory_tools.execute
                        if self.entity_memory_tools is not None
                        else None
                    ),
                )
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
    ) -> dict[str, Any]:
        assert self.robot_agent_runtime is not None

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type=event_type,
                payload=payload,
            )

        # Start with task-required skills plus always-exposed skills
        candidate_skills = set(task_object.required_skills) | {"report_status", "return_to_safe_zone"}

        # If profile is loaded, constrain to profile's llm_exposed_skills
        if self.robot_profile is not None:
            profile_exposed = set(self.robot_profile.llm_exposed_skills)
            candidate_skills = candidate_skills & profile_exposed

        exposed_skill_names = tuple(
            skill_name
            for skill_name in agent.registry.names()
            if skill_name in candidate_skills
        )
        skill_tools = build_robot_skill_tools(
            agent.registry,
            exposed_skill_names=exposed_skill_names,
        )
        skill_metadata = [
            metadata
            for metadata in agent.registry.list_metadata()
            if metadata["name"] in exposed_skill_names
        ]

        robot_state_object = agent._get_robot_state()
        robot_state = agent._state_snapshot(robot_state_object)
        runtime_sensors = robot_state.get("available_sensors")
        context = {
            "robot_state": robot_state,
            "environment_state": agent._state_snapshot(agent._get_environment_state()),
            "available_sensors": sorted(runtime_sensors if runtime_sensors is not None else agent.available_sensors),
            "skill_tools": skill_tools,
            "skill_metadata": skill_metadata,
            "skill_inventory": build_robot_skill_inventory(
                registry=agent.registry,
                primitive_skills=self.robot_profile.primitive_skills if self.robot_profile else tuple(agent.registry.names()),
                composite_chains=self.robot_profile.capability_skill_chains if self.robot_profile else {},
                verified_sensors=set(runtime_sensors if runtime_sensors is not None else agent.available_sensors),
            ),
        }
        if self.entity_memory_tools is not None and task_object.mission_id:
            context["memory_tools"] = self.entity_memory_tools.tool_schemas()
        planning_result = self.robot_agent_runtime.plan_structured_task(
            task_object,
            fallback_robot_id=self.config.robot_id,
            context=context,
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
                )
            else:
                result = agent.run_structured_task(task_object)
        else:
            result = agent.run(command)
        result["task_id"] = task_id
        self._record_result_events(task_id, session_id, result)
        self._record_authorization_request_if_needed(
            task_id=task_id,
            session_id=session_id,
            command=command,
            result=result,
            operator=operator or operator_from_payload(None),
        )
        return result

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
        risk_level = _risk_level_from_confirmation(result.get("confirmation"))
        decision = ControlPolicy().evaluate_risk(operator, action="task.confirm", risk_level=risk_level)
        if decision.status != "approval_required":
            return
        requested_at_dt = datetime.now(timezone.utc)
        expires_at_dt = requested_at_dt + timedelta(seconds=max(0, self.config.authorization_expiry_seconds))
        request = AuthorizationRequest(
            request_id=f"auth-{uuid4().hex}",
            task_id=task_id,
            session_id=session_id,
            command=command,
            requested_by=operator,
            required_scope="safety.override",
            risk_level=risk_level,
            requested_at=requested_at_dt.isoformat(),
            expires_at=expires_at_dt.isoformat(),
        )
        with self._authorization_lock:
            self._pending_authorizations_by_session[session_id] = request
        self._append_event(
            task_id=task_id,
            session_id=session_id,
            type="authorization.requested",
            payload={
                "authorization": request.to_dict(),
                "control": decision.to_dict(),
            },
        )

    def _authorize_confirmation(
        self,
        *,
        session_id: str,
        operator: OperatorContext,
    ) -> tuple[bool, dict[str, Any]]:
        with self._authorization_lock:
            request = self._pending_authorizations_by_session.get(session_id)
        if request is None:
            decision = ControlPolicy().evaluate(operator, "task.confirm")
            if decision.status == "allow":
                return True, {"control": decision.to_dict(), "authorization": None}
            return False, {
                "status": "denied",
                "session_id": session_id,
                "message": "操作员没有权限确认任务。",
                "control": decision.to_dict(),
            }
        now = datetime.now(timezone.utc).isoformat()
        if request.is_expired(now):
            with self._authorization_lock:
                self._pending_authorizations_by_session.pop(session_id, None)
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
        with self._authorization_lock:
            self._pending_authorizations_by_session.pop(session_id, None)
        return True, {
            "status": "approved",
            "session_id": session_id,
            "authorization": request.to_dict(),
            "control": decision.to_dict(),
            "approved_by": operator.to_dict(),
            "approved_at": now,
        }

    def list_skills(self, session_id: str | None = None) -> dict[str, Any]:
        return self.run_agent("你有哪些技能", session_id=session_id)

    def recent_memory(self, *, session_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        return {
            "records": self.memory.latest_records(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def recent_events(self, *, session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return {
            "events": self.events.latest_events(limit=limit, session_id=session_id),
            "session_id": session_id,
            "limit": limit,
        }

    def task_trace(self, task_id: str) -> dict[str, Any]:
        events = self.events.events_for_task(task_id)
        result = self._task_result_from_events(events)
        queue_record = self.task_queue.get(task_id)
        structured_task = None
        with self._task_lock:
            control = self._task_controls.get(task_id)
            if control is not None:
                structured_task = control.structured_task
        if structured_task is None and result is not None:
            structured_task = result.get("structured_task")
        return {
            "task_id": task_id,
            "events": events,
            "result": result,
            "status": self._task_status(task_id, events, result),
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
            terminal_status = "cancelled" if was_cancel_requested else "completed"
            final_type = "task.cancelled" if terminal_status == "cancelled" else "task.completed"
            self._append_event(
                task_id=task_id,
                session_id=session_id,
                type=final_type,
                payload={
                    "status": terminal_status if was_cancel_requested else result.get("status"),
                    "message": result.get("message"),
                    "result": result,
                    "cancel_requested": was_cancel_requested,
                },
            )
            self.task_queue.update(
                task_id,
                status=terminal_status,
                ended_at=datetime.now(timezone.utc).isoformat(),
                result=result,
            )

    def _task_result_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        for event in reversed(events):
            if event.get("type") in {"task.completed", "task.cancelled", "task.failed"}:
                payload = event.get("payload") or {}
                result = payload.get("result")
                if isinstance(result, dict):
                    if event.get("type") == "task.cancelled":
                        return {**result, "status": "cancelled"}
                    return result
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
            workspace_skills_dir=self.config.workspace_skills_dir,
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
        )

    def _task_status(
        self,
        task_id: str,
        events: list[dict[str, Any]],
        result: dict[str, Any] | None,
    ) -> str:
        if result is not None:
            status = result.get("status")
            return status if isinstance(status, str) else "completed"
        if any(event.get("type") == "task.cancel_requested" for event in events):
            return "cancel_requested"
        with self._task_lock:
            if task_id in self._task_controls:
                return "running"
        queue_record = self.task_queue.get(task_id)
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
        with self._event_lock:
            result = self.events.append(
                task_id=task_id,
                session_id=session_id,
                type=type,
                payload=payload,
            )
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
        lost_records = self.task_queue.mark_non_terminal_lost(
            ended_at=now,
            error="Gateway restarted before terminal result.",
        )
        for record in lost_records:
            self._append_event(
                task_id=record.task_id,
                session_id=record.session_id,
                type="task.lost",
                payload={
                    "status": "lost",
                    "task_id": record.task_id,
                    "message": "Gateway restarted before terminal result; task was not replayed.",
                    "queue_record": record.to_dict(),
                },
            )

    def _handler_class(self):
        gateway = self

        class GatewayRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if not gateway._check_auth(self):
                    return
                parsed = urlparse(self.path)
                # Health check bypasses scope enforcement
                if parsed.path == "/health":
                    gateway._handle_get(self)
                    return
                scopes = _extract_scopes_from_header(self)
                result = authorize_method(f"GET {parsed.path}", scopes)
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                # SSE long-lived response — handle before normal dispatch
                if parsed.path == "/events/stream":
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    # Cursor replay: parse Last-Event-ID header or after_sequence query param
                    after_seq: int | None = None
                    last_event_id = self.headers.get("Last-Event-ID")
                    if last_event_id is not None:
                        try:
                            after_seq = int(last_event_id)
                        except ValueError:
                            after_seq = None
                    else:
                        qs = parse_qs(parsed.query)
                        raw = qs.get("after_sequence", [None])[0]
                        if raw is not None:
                            try:
                                after_seq = int(raw)
                            except ValueError:
                                after_seq = None
                    # Replay recent events after cursor
                    if after_seq is not None:
                        for evt in gateway._event_bus.get_recent_events(after_sequence=after_seq):
                            try:
                                self.wfile.write(evt.to_sse_format().encode("utf-8"))
                                self.wfile.flush()
                            except Exception:
                                return
                    # Track sequences already sent to avoid duplicates
                    sent_sequences: set[int] = set()
                    if after_seq is not None:
                        sent_sequences = {e.sequence for e in gateway._event_bus.get_recent_events(after_sequence=after_seq)}
                    event_queue: Queue[StreamEvent | None] = Queue()
                    def _on_event(event: StreamEvent) -> None:
                        event_queue.put(event)
                    token = gateway._event_bus.subscribe(_on_event)
                    try:
                        while True:
                            try:
                                event = event_queue.get(timeout=15.0)
                                if event is None:
                                    break
                                if event.sequence in sent_sequences:
                                    continue
                                self.wfile.write(event.to_sse_format().encode("utf-8"))
                                self.wfile.flush()
                            except QueueEmpty:
                                try:
                                    self.wfile.write(b": heartbeat\n\n")
                                    self.wfile.flush()
                                except Exception:
                                    break
                            except Exception:
                                break
                    finally:
                        gateway._event_bus.unsubscribe(token)
                    return
                gateway._handle_get(self)

            def do_POST(self) -> None:
                if not gateway._check_auth(self):
                    return
                parsed = urlparse(self.path)
                scopes = _extract_scopes_from_header(self)
                result = authorize_method(f"POST {parsed.path}", scopes)
                if not result.allowed:
                    gateway._write_error(
                        self, HTTPStatus.FORBIDDEN,
                        f"Missing required scope: {result.missing_scope}",
                    )
                    return
                gateway._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        return GatewayRequestHandler

    def _check_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        if self.config.api_token is None:
            return True
        parsed = urlparse(handler.path)
        if parsed.path == "/health":
            return True
        auth_header = handler.headers.get("Authorization", "")
        if auth_header == f"Bearer {self.config.api_token}":
            return True
        body = json.dumps({"error": "Unauthorized"}, ensure_ascii=False).encode("utf-8")
        handler.send_response(HTTPStatus.UNAUTHORIZED)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return False

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._write_json(handler, HTTPStatus.OK, self.health())
            return
        if parsed.path == "/state":
            self._write_json(handler, HTTPStatus.OK, self.state())
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
            if self.memory_replication_exporter is None:
                self._write_error(handler, HTTPStatus.NOT_FOUND, "Embodied memory is not configured.")
                return
            try:
                batch = self.memory_replication_exporter.export_batch(
                    cursor=_int_query(query, "cursor", 0),
                    limit=_int_query(query, "limit", 200),
                    mission_id=_first(query, "mission_id"),
                    runtime_mode=_first(query, "runtime_mode"),
                )
            except ValueError as exc:
                self._write_error(handler, HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._write_json(handler, HTTPStatus.OK, batch.to_dict())
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

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        try:
            payload = self._read_json(handler)
            task_cancel_id = _task_cancel_path(parsed.path)
            if task_cancel_id is not None:
                result = self.cancel_task(task_cancel_id, operator=operator_from_payload(payload.get("operator")))
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
                    operator=operator_from_payload(payload.get("operator")),
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
                    operator=operator_from_payload(payload.get("operator")),
                    reason=_optional_payload_string(payload, "reason"),
                )
                status = HTTPStatus.FORBIDDEN if result["status"] == "denied" else HTTPStatus.OK
                self._write_json(handler, status, result)
                return
            if parsed.path == "/confirm":
                session_id = _payload_session(payload, self.config.default_session_id)
                operator = operator_from_payload(payload.get("operator"))
                authorized, authorization_payload = self._authorize_confirmation(
                    session_id=session_id,
                    operator=operator,
                )
                if not authorized:
                    self._write_json(handler, HTTPStatus.FORBIDDEN, authorization_payload)
                    return
                authorization = authorization_payload.get("authorization")
                if isinstance(authorization, dict) and isinstance(authorization.get("task_id"), str):
                    self._wait_until_task_inactive(authorization["task_id"])
                result = self.submit_agent(
                    "确认执行",
                    session_id=session_id,
                    operator=operator,
                )
                if authorization_payload.get("status") == "approved" and result.get("task_id"):
                    self._append_event(
                        task_id=result["task_id"],
                        session_id=session_id,
                        type="authorization.approved",
                        payload=authorization_payload,
                    )
                self._write_json(
                    handler,
                    _submission_status(result),
                    result,
                )
                return
            if parsed.path == "/cancel":
                result = self.submit_agent(
                    "取消",
                    session_id=_payload_session(payload, self.config.default_session_id),
                    operator=operator_from_payload(payload.get("operator")),
                )
                self._write_json(
                    handler,
                    _submission_status(result),
                    result,
                )
                return
            self._write_error(handler, HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except json.JSONDecodeError:
            self._write_error(handler, HTTPStatus.BAD_REQUEST, "Request body must be valid JSON.")

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        length = int(handler.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = handler.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise json.JSONDecodeError("JSON body must be an object.", raw.decode("utf-8"), 0)
        return value

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
    return HTTPStatus.ACCEPTED


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



def _extract_scopes_from_header(handler: BaseHTTPRequestHandler) -> set[str]:
    """Extract operator scopes from X-Operator-Scopes header or default to read-only."""
    scopes_header = handler.headers.get("X-Operator-Scopes", "")
    if scopes_header:
        return {s.strip() for s in scopes_header.split(",") if s.strip()}
    return {"state.read"}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the FireClaw local HTTP gateway.")
    parser.add_argument("--config", type=Path, default=None, help="Path to fireclaw.toml config file.")
    parser.add_argument("--host", default=None, help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port.")
    parser.add_argument("--adapter", choices=ADAPTER_CHOICES, default=None)
    parser.add_argument("--robot-id", default=None)
    parser.add_argument("--ros1-config", default=None)
    parser.add_argument("--memory-path", default=None)
    parser.add_argument("--event-path", default=None)
    parser.add_argument("--task-queue-path", default=None)
    parser.add_argument("--skills-dir", default=None)
    parser.add_argument("--no-workspace-skills", action="store_true", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--max-active-execution-tasks", type=int, default=None)
    parser.add_argument("--available-sensor", action="append", default=None)
    parser.add_argument("--real-run", action="store_true", default=None)
    parser.add_argument("--robot-agent", action="store_true", default=None, help="Enable robot-local agent planning for structured tasks.")
    parser.add_argument("--robot-agent-planner", choices=["deterministic", "llm"], default=None)
    parser.add_argument("--robot-agent-provider-base-url", default=None)
    parser.add_argument("--robot-agent-provider-api-key", default=None)
    parser.add_argument("--robot-agent-model", default=None)
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
            "robot_gateway_host": args.host,
            "robot_gateway_port": args.port,
            "robot_gateway_adapter": args.adapter,
            "robot_gateway_robot_id": args.robot_id,
            "robot_gateway_ros1_config": args.ros1_config,
            "robot_gateway_memory_path": args.memory_path,
            "robot_gateway_event_path": args.event_path,
            "robot_gateway_task_queue_path": args.task_queue_path,
            "robot_gateway_workspace_skills_dir": args.skills_dir,
            "robot_gateway_dry_run": False if args.real_run else None,
            "robot_gateway_available_sensors": args.available_sensor,
            "robot_gateway_default_session_id": args.session_id,
            "robot_gateway_max_active_execution_tasks": args.max_active_execution_tasks,
            "robot_agent_enabled": args.robot_agent if args.robot_agent else None,
            "robot_agent_planner": args.robot_agent_planner,
            "robot_agent_provider_base_url": args.robot_agent_provider_base_url,
            "robot_agent_provider_api_key": args.robot_agent_provider_api_key,
            "robot_agent_model": args.robot_agent_model,
            "robot_gateway_profile_path": args.robot_profile,
            "robot_gateway_embodied_memory_path": args.embodied_memory_path,
            "robot_gateway_embodied_memory_index": args.embodied_memory_index,
            "robot_gateway_embodied_runtime_mode": args.embodied_runtime_mode,
        },
    )

    workspace_skills_dir = merged.get("robot_gateway_workspace_skills_dir", "skills")
    if args.no_workspace_skills:
        workspace_skills_dir = None
    sensors = merged.get("robot_gateway_available_sensors") or ()

    gateway = FireClawGateway(
        GatewayConfig(
            host=str(merged.get("robot_gateway_host", "127.0.0.1")),
            port=int(merged.get("robot_gateway_port", 8765)),
            adapter=str(merged.get("robot_gateway_adapter", "dry-run")),
            robot_id=str(merged.get("robot_gateway_robot_id", "fireclaw-gateway")),
            ros1_config_path=merged.get("robot_gateway_ros1_config"),
            memory_path=str(merged.get("robot_gateway_memory_path", "memory/fireclaw-gateway.jsonl")),
            event_path=str(merged.get("robot_gateway_event_path", "memory/fireclaw-gateway-events.jsonl")),
            task_queue_path=str(merged.get("robot_gateway_task_queue_path", "memory/fireclaw-gateway-tasks.jsonl")),
            workspace_skills_dir=None if workspace_skills_dir is None else str(workspace_skills_dir),
            dry_run=bool(merged.get("robot_gateway_dry_run", True)),
            available_sensors=tuple(str(sensor) for sensor in sensors),
            default_session_id=str(merged.get("robot_gateway_default_session_id", "default")),
            max_active_execution_tasks=max(1, int(merged.get("robot_gateway_max_active_execution_tasks", 1))),
            api_token=merged.get("robot_gateway_api_token"),
            robot_agent_enabled=bool(merged.get("robot_agent_enabled", False)),
            robot_agent_planner=str(merged.get("robot_agent_planner", "deterministic")),
            robot_agent_provider_base_url=merged.get("robot_agent_provider_base_url"),
            robot_agent_provider_api_key=merged.get("robot_agent_provider_api_key"),
            robot_agent_model=merged.get("robot_agent_model"),
            robot_profile_path=merged.get("robot_gateway_profile_path"),
            embodied_memory_path=merged.get("robot_gateway_embodied_memory_path"),
            embodied_memory_index_path=merged.get("robot_gateway_embodied_memory_index"),
            embodied_runtime_mode=merged.get("robot_gateway_embodied_runtime_mode"),
        )
    )
    print(
        json.dumps(
            {
                "status": "starting",
                "base_url": f"http://{gateway.config.host}:{gateway.config.port}",
                "adapter": gateway.config.adapter,
                "robot_id": gateway.config.robot_id,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    gateway.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
