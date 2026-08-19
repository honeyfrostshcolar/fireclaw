from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from queue import Empty as QueueEmpty, Full as QueueFull, Queue
import threading
import time
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
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
from fireclaw_core.agent.robot_registry import RobotRegistry
from fireclaw_core.infra.operator_readiness import build_operator_doctor_snapshot
from fireclaw_core.infra.session_lineage import JsonlSessionLineageStore, validate_resume_ownership
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
from fireclaw_core.mission.mission_run import MissionRunManager
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
        """Report structured readiness snapshot for the active robot/fleet."""
        fleet_doctor = self.fleet_doctor()
        doctor_snapshot = build_operator_doctor_snapshot(report=fleet_doctor)
        fleet_state = self.fleet_state()
        active_robot_id = None
        enabled = self.registry.enabled_entries(include_stale=True)
        if enabled:
            active_robot_id = enabled[0].robot_id
        return {
            "status": "ok",
            "schema_version": 1,
            "kind": "readiness_snapshot",
            "active_robot_id": active_robot_id,
            "phase": doctor_snapshot.get("phase", "ready"),
            "safe_state": doctor_snapshot.get("safe_state", "unknown"),
            "reason_code": doctor_snapshot.get("reason_code", "ready"),
            "summary": doctor_snapshot.get("summary", "System ready."),
            "fleet_state": fleet_state,
            "fleet_doctor": fleet_doctor,
            "doctor_snapshot": doctor_snapshot,
        }

    def plan_mission(
        self,
        task_description: str,
        *,
        profile: str | None = None,
        operator: OperatorContext | None = None,
    ) -> dict[str, Any]:
        if not task_description or not task_description.strip():
            from fireclaw_core.errors.friendly_errors import resolve_friendly_error
            resp = resolve_friendly_error(
                "planner_failed",
                {"goal": "", "reason": "Field 'task_description' or 'command' is required."},
            )
            return {
                "status": "error",
                "message": resp.what_happened,
                "error": resp.to_dict(),
            }
        task_desc = task_description.strip()
        target_robot = None
        enabled = self.registry.enabled_entries(include_stale=True)
        if enabled:
            target_robot = enabled[0].robot_id
        if not target_robot:
            target_robot = "turtlebot3_burger"

        if "搜" in task_desc or "救" in task_desc:
            intent = "前往二楼搜索人员" if "二楼" in task_desc else "搜索与救援被困人员"
            steps = [
                "1. move_base 导航至目标搜索区域",
                "2. 启动视觉与红外热成像传感器搜索被困人员",
                "3. 标记坐标并向指挥网关上报",
            ]
            risk_level = "medium"
        elif "火" in task_desc or "温" in task_desc or "侦" in task_desc:
            intent = "火情与温度态势侦察"
            steps = [
                "1. 导航至火场边缘安全侦察位姿",
                "2. 多传感器多波段探测火源分布与温度梯度",
                "3. 生成热点态势图并实时同步网关",
            ]
            risk_level = "high"
        elif "返" in task_desc or "回" in task_desc or "停" in task_desc:
            intent = "返回安全待命点"
            steps = [
                "1. 中止或收尾当前非关键动作",
                "2. 计算全局无碰撞返航路径",
                "3. 导航返回初始安全待命点并锁定底盘",
            ]
            risk_level = "low"
        elif "图" in task_desc or "巡" in task_desc:
            intent = "全区域巡航与建图"
            steps = [
                "1. 启动激光雷达 SLAM 全区拓扑巡航",
                "2. 实时融合激光点云构建环境态势地图",
                "3. 保存地图快照并同步至本地控制台",
            ]
            risk_level = "low"
        else:
            intent = task_desc
            steps = [
                f"1. 导航至任务指定目标区域",
                f"2. 执行动作: {task_desc[:25]}",
                f"3. 状态校验与网关汇报",
            ]
            risk_level = "medium"

        raw_plan = {
            "intent": intent,
            "target_robot": target_robot,
            "steps": steps,
            "risk_level": risk_level,
            "profile": profile,
            "task_description": task_desc,
        }

        return {
            "status": "ok",
            "task_description": task_desc,
            "intent": intent,
            "target_robot": target_robot,
            "steps": steps,
            "risk_level": risk_level,
            "raw_plan": raw_plan,
        }

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
        self.publish_event(
            event_type,
            "mission-run",
            mission_id=mission_id,
            payload=payload,
        )
        if event_type == "mission.report_ready":
            self.publish_event(
                "mission.final_report_ready",
                "mission-run",
                mission_id=mission_id,
                payload=payload,
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
            elif mission_id is not None:
                history = self.get_mission_events(mission_id, limit=200)
                for value in history.get("events", []):
                    event = StreamEvent(
                        event_type=value.get("type", "unknown"),
                        source="mission-history",
                        mission_id=mission_id,
                        payload=value.get("payload", value),
                    )
                    handler.wfile.write(
                        event.to_sse_format().encode("utf-8")
                    )
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

            if path == "/plan-mission":
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
                    profile=_optional_string(payload, "profile"),
                    operator=operator,
                )
                self._write_json(handler, HTTPStatus.OK, result)
                return

            if path in ("/missions", "/tasks"):
                command = payload.get("command") or payload.get("task") or payload.get("goal") or payload.get("task_description")
                if not isinstance(command, str) or not command.strip():
                    self._write_error(handler, HTTPStatus.BAD_REQUEST, "Field 'command' or 'task' is required.")
                    return
                result = self.submit_mission(
                    command,
                    session_id=_optional_string(payload, "session_id") or _optional_string(payload, "task_id"),
                    operator=operator,
                    use_scheduler=payload.get("use_scheduler", True),
                    background=(
                        payload.get("background")
                        if isinstance(payload.get("background"), bool)
                        else None
                    ),
                )
                if "mission_id" in result and "task_id" not in result:
                    result["task_id"] = result["mission_id"]
                status = _mission_submit_status(result)
                self._write_json(handler, status, result)
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
