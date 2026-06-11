from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Protocol

from fireclaw_core.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.executor import CancellationCheck, ExecutionEventSink, ExecutionResult, PlanExecutor
from fireclaw_core.memory import JsonlMemoryStore
from fireclaw_core.planner import (
    CHINESE_DIGITS,
    Plan,
    PlannerContext,
    PlanningResult,
    PlanStep,
    RuleBasedPlanner,
)
from fireclaw_core.robot import DryRunRobotAdapter, RobotAdapter
from fireclaw_core.safety import SafetyDecision, SafetyGate
from fireclaw_core.skills import create_default_skill_registry
from fireclaw_core.task_contract import StructuredRobotTask, planning_result_from_structured_task
from fireclaw_core.workspace_skills import WorkspaceSkillLoadError, load_workspace_skills


class MemoryStore(Protocol):
    def append(self, record: dict[str, Any]) -> None:
        ...

    def latest_records(
        self,
        limit: int = 5,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ...


class Planner(Protocol):
    def plan(
        self,
        command: str,
        context: PlannerContext | None = None,
    ) -> PlanningResult:
        ...


class FireClawAgent:
    def __init__(
        self,
        *,
        robot: RobotAdapter | None = None,
        memory: MemoryStore | None = None,
        workspace_skills_dir: str | Path | None = None,
        dry_run: bool = True,
        available_sensors: set[str] | None = None,
        session_id: str = "default",
        planner: Planner | None = None,
        event_sink: ExecutionEventSink | None = None,
        cancellation_requested: CancellationCheck | None = None,
        task_id: str | None = None,
    ) -> None:
        self.robot = robot or DryRunRobotAdapter(robot_id="fireclaw-dry-run")
        self.memory = memory or JsonlMemoryStore("memory/fireclaw-runs.jsonl")
        self.dry_run = dry_run
        # If no sensors specified, infer from robot adapter when possible
        if available_sensors is not None:
            self.available_sensors = available_sensors
        elif hasattr(self.robot, "available_sensors"):
            self.available_sensors = set(self.robot.available_sensors)
        else:
            self.available_sensors = set()
        self.session_id = session_id
        self.planner = planner or RuleBasedPlanner()
        self._event_sink = event_sink
        self._cancellation_requested = cancellation_requested
        self.task_id = task_id
        action_runtime = RobotActionRuntime(
            backend=RobotAdapterActionBackend(self.robot),
            event_sink=event_sink,
            task_id=task_id,
        )
        self.registry = create_default_skill_registry(self.robot, action_runtime=action_runtime)
        self.skill_load_errors: list[WorkspaceSkillLoadError] = []
        if workspace_skills_dir is not None:
            workspace_result = load_workspace_skills(workspace_skills_dir)
            self.registry.extend(workspace_result.skills)
            self.skill_load_errors = workspace_result.errors
        self.safety = SafetyGate()
        self.executor = PlanExecutor(
            self.registry,
            event_sink=event_sink,
            cancellation_requested=cancellation_requested,
        )

    def run(self, command: str) -> dict[str, Any]:
        if self._is_confirmation_command(command):
            return self._confirm_pending_plan(command)
        if self._is_cancellation_command(command):
            return self._cancel_pending_plan(command)
        if self._is_memory_search_command(command):
            return self._retrieve_memory(command)
        if self._is_memory_recall_command(command):
            return self._recall_memory(command)
        if self._is_skill_listing_command(command):
            return self._list_skills(command)

        turn_index = self._next_turn_index()
        resolved_command, context_used = self._resolve_command_from_session(command)
        planner_context = self._build_planner_context(turn_index)
        planning_result = self.planner.plan(resolved_command, context=planner_context)
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision = self.safety.evaluate(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self.available_sensors,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
        )
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and planning_result.plan is not None:
            execution_result = self.executor.execute(planning_result.plan)

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "session": {
                "session_id": self.session_id,
                "turn_index": turn_index,
                "resolved_command": resolved_command,
                "context_used": context_used,
            },
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "confirmation": self._confirmation_to_dict(safety_decision),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }

        self._append_memory_result(result)
        return result

    def run_structured_task(self, task: StructuredRobotTask) -> dict[str, Any]:
        planning_result = planning_result_from_structured_task(task)
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision = self.safety.evaluate(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self.available_sensors,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
        )
        self._emit_event("task.structured_received", task.to_dict())
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and planning_result.plan is not None:
            execution_result = self.executor.execute(planning_result.plan)

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": task.command or task.task_type,
            "structured_task": task.to_dict(),
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "confirmation": self._confirmation_to_dict(safety_decision),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        self._append_memory_result(result)
        return result

    def _next_turn_index(self) -> int:
        try:
            records = self.memory.latest_records(limit=1000000, session_id=self.session_id)
        except TypeError:
            records = self.memory.latest_records(limit=1000000)
            records = [
                record
                for record in records
                if record.get("session", {}).get("session_id") == self.session_id
            ]
        except Exception:
            return 1
        turn_indices = [
            record.get("session", {}).get("turn_index")
            for record in records
            if isinstance(record.get("session", {}).get("turn_index"), int)
        ]
        if not turn_indices:
            return 1
        return max(turn_indices) + 1

    def _resolve_command_from_session(self, command: str) -> tuple[str, bool]:
        if "救人" in command:
            return command, False
        floor_match = re.search(r"([0-9]+|[一二三四五六七八九十])楼", command)
        if floor_match is None:
            return command, False
        previous = self._latest_session_record()
        if previous is None or previous.get("status") != "clarify":
            return command, False
        return f"去{floor_match.group(1)}楼救人", True

    def _latest_session_record(self) -> dict[str, Any] | None:
        try:
            records = self.memory.latest_records(limit=1, session_id=self.session_id)
        except TypeError:
            records = self.memory.latest_records(limit=5)
            records = [
                record
                for record in records
                if record.get("session", {}).get("session_id") == self.session_id
            ]
        except Exception:
            return None
        if not records:
            return None
        return records[-1]

    def _build_planner_context(self, turn_index: int) -> PlannerContext:
        return PlannerContext(
            session_id=self.session_id,
            turn_index=turn_index,
            recent_records=self._recent_session_records(limit=5),
            skills=self.registry.list_metadata(),
        )

    def _recent_session_records(self, limit: int) -> list[dict[str, Any]]:
        try:
            return self.memory.latest_records(limit=limit, session_id=self.session_id)
        except TypeError:
            records = self.memory.latest_records(limit=limit)
            return [
                record
                for record in records
                if record.get("session", {}).get("session_id") == self.session_id
            ]
        except Exception:
            return []

    def _resolve_status(
        self,
        safety_decision: SafetyDecision,
        execution_result: ExecutionResult | None,
    ) -> str:
        if safety_decision.status in {"clarify", "block"}:
            return safety_decision.status
        if safety_decision.status == "require_confirmation":
            return "awaiting_confirmation"
        if execution_result is None:
            return "failed"
        return execution_result.status

    def _resolve_message(
        self,
        planning_result: PlanningResult,
        safety_decision: SafetyDecision,
        execution_result: ExecutionResult | None,
    ) -> str:
        if safety_decision.status in {"clarify", "block", "require_confirmation"}:
            return "; ".join(safety_decision.reasons)
        if execution_result is not None and execution_result.status == "succeeded":
            return "FireClaw dry-run rescue plan completed."
        if execution_result is not None and execution_result.status == "cancelled":
            return "任务已取消。"
        return planning_result.message

    def _planning_to_dict(self, planning_result: PlanningResult) -> dict[str, Any]:
        return {
            "status": planning_result.status,
            "message": planning_result.message,
            "intent": planning_result.intent,
            "target_floor": planning_result.target_floor,
            "plan": asdict(planning_result.plan) if planning_result.plan is not None else None,
        }

    def _execution_to_dict(self, execution_result: ExecutionResult | None) -> dict[str, Any] | None:
        if execution_result is None:
            return None
        return asdict(execution_result)

    def _confirmation_to_dict(self, safety_decision: SafetyDecision) -> dict[str, Any] | None:
        if safety_decision.status != "require_confirmation":
            return None
        return {
            "status": "pending",
            "reasons": list(safety_decision.reasons),
        }

    def _append_memory_result(self, result: dict[str, Any]) -> None:
        try:
            self.memory.append(result)
        except Exception as exc:  # Memory failures should be visible but not mask execution.
            result["memory_error"] = str(exc)

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event_type, payload)
        except Exception:
            return

    def _get_robot_state(self) -> Any:
        get_robot_state = getattr(self.robot, "get_robot_state", None)
        if get_robot_state is None:
            return None
        try:
            return get_robot_state()
        except Exception:
            return None

    def _get_environment_state(self) -> Any:
        get_environment_state = getattr(self.robot, "get_environment_state", None)
        if get_environment_state is None:
            return None
        try:
            return get_environment_state()
        except Exception:
            return None

    def _state_snapshot(self, state: Any) -> dict[str, Any] | None:
        if state is None:
            return None
        return asdict(state)

    def _is_memory_recall_command(self, command: str) -> bool:
        recall_terms = ("之前", "回忆", "记得", "历史", "做过")
        task_terms = ("任务", "做过", "执行", "记录")
        return any(term in command for term in recall_terms) and any(
            term in command for term in task_terms
        )

    def _is_memory_search_command(self, command: str) -> bool:
        memory_terms = ("之前", "上次", "历史", "记录", "查", "查询", "找")
        detail_terms = ("成功", "失败", "原因", "救人")
        has_floor = self._extract_floor(command) is not None
        return any(term in command for term in memory_terms) and (
            has_floor or any(term in command for term in detail_terms)
        )

    def _is_skill_listing_command(self, command: str) -> bool:
        skill_terms = ("技能", "skill", "能力")
        list_terms = ("哪些", "列表", "列出", "有什么", "能做什么")
        lowered = command.lower()
        return any(term in lowered for term in skill_terms) and any(
            term in lowered for term in list_terms
        )

    def _is_confirmation_command(self, command: str) -> bool:
        return command.strip() in {"确认", "确认执行", "同意执行"}

    def _is_cancellation_command(self, command: str) -> bool:
        return command.strip() in {"取消", "取消执行", "不要执行"}

    def _list_skills(self, command: str) -> dict[str, Any]:
        skills = self.registry.list_metadata()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": "skills",
            "message": f"当前注册了 {len(skills)} 个技能。",
            "dry_run": self.dry_run,
            "planning": None,
            "safety": None,
            "execution": None,
            "skills": skills,
            "skill_load_errors": [asdict(error) for error in self.skill_load_errors],
            "memory_error": None,
        }

    def _recall_memory(self, command: str) -> dict[str, Any]:
        try:
            try:
                records = self.memory.latest_records(limit=5, session_id=self.session_id)
                if not records:
                    legacy_records = self.memory.latest_records(limit=5)
                    if legacy_records and not any("session" in record for record in legacy_records):
                        records = legacy_records
            except TypeError:
                records = self.memory.latest_records(limit=5)
        except Exception as exc:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "command": command,
                "status": "failed",
                "message": "读取记忆失败。",
                "dry_run": self.dry_run,
                "planning": None,
                "safety": None,
                "execution": None,
                "memory": {"records": []},
                "memory_error": str(exc),
            }
        if records:
            message = f"找到 {len(records)} 条最近任务记录。"
        else:
            message = "没有找到之前的任务记录。"
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": "recalled",
            "message": message,
            "dry_run": self.dry_run,
            "planning": None,
            "safety": None,
            "execution": None,
            "memory": {"records": records},
            "memory_error": None,
        }

    def _retrieve_memory(self, command: str) -> dict[str, Any]:
        query = self._build_memory_query(command)
        try:
            search_records = getattr(self.memory, "search_records", None)
            if search_records is not None:
                records = search_records(**query)
            else:
                records = self._filter_memory_records(query)
        except Exception as exc:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "command": command,
                "status": "failed",
                "message": "查询记忆失败。",
                "dry_run": self.dry_run,
                "planning": None,
                "safety": None,
                "execution": None,
                "memory": {"query": query, "records": []},
                "memory_error": str(exc),
            }

        if records:
            message = f"找到 {len(records)} 条匹配记忆记录。"
        else:
            message = "没有找到匹配的记忆记录。"
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": "retrieved",
            "message": message,
            "dry_run": self.dry_run,
            "planning": None,
            "safety": None,
            "execution": None,
            "memory": {"query": query, "records": records},
            "memory_error": None,
        }

    def _build_memory_query(self, command: str) -> dict[str, Any]:
        status: str | None = None
        if "成功" in command:
            status = "succeeded"
        elif "失败" in command or "原因" in command:
            status = "failed"

        intent = "rescue_victim" if "救人" in command else None
        return {
            "session_id": self.session_id,
            "status": status,
            "intent": intent,
            "target_floor": self._extract_floor(command),
            "command_contains": None,
            "limit": 5,
        }

    def _filter_memory_records(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            records = self.memory.latest_records(limit=1000000, session_id=query["session_id"])
        except TypeError:
            records = self.memory.latest_records(limit=1000000)

        matches: list[dict[str, Any]] = []
        for record in records:
            if record.get("session", {}).get("session_id") != query["session_id"]:
                continue
            if query["status"] is not None and record.get("status") != query["status"]:
                continue
            planning = record.get("planning") or {}
            if query["intent"] is not None and planning.get("intent") != query["intent"]:
                continue
            if (
                query["target_floor"] is not None
                and planning.get("target_floor") != query["target_floor"]
            ):
                continue
            matches.append(record)
        return matches[: query["limit"]]

    def _confirm_pending_plan(self, command: str) -> dict[str, Any]:
        pending = self._latest_pending_confirmation_record()
        if pending is None:
            return self._no_pending_confirmation_result(command)

        turn_index = self._next_turn_index()
        planning_result = self._planning_result_from_record(pending)
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision = self.safety.evaluate(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self.available_sensors,
            operator_confirmed=True,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
        )
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and planning_result.plan is not None:
            execution_result = self.executor.execute(planning_result.plan)

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "session": {
                "session_id": self.session_id,
                "turn_index": turn_index,
                "resolved_command": pending.get("session", {}).get(
                    "resolved_command",
                    pending.get("command"),
                ),
                "context_used": True,
            },
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "confirmation": {
                "status": "confirmed",
                "pending_turn_index": pending.get("session", {}).get("turn_index"),
                "pending_command": pending.get("command"),
            },
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        self._append_memory_result(result)
        return result

    def _cancel_pending_plan(self, command: str) -> dict[str, Any]:
        pending = self._latest_pending_confirmation_record()
        if pending is None:
            return self._no_pending_confirmation_result(command)

        turn_index = self._next_turn_index()
        robot_state = self._state_snapshot(self._get_robot_state())
        environment_state = self._state_snapshot(self._get_environment_state())
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": "cancelled",
            "message": "已取消待确认任务。",
            "dry_run": self.dry_run,
            "session": {
                "session_id": self.session_id,
                "turn_index": turn_index,
                "resolved_command": command,
                "context_used": True,
            },
            "planning": pending.get("planning"),
            "safety": pending.get("safety"),
            "execution": None,
            "confirmation": {
                "status": "cancelled",
                "pending_turn_index": pending.get("session", {}).get("turn_index"),
                "pending_command": pending.get("command"),
            },
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        self._append_memory_result(result)
        return result

    def _no_pending_confirmation_result(self, command: str) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": "clarify",
            "message": "没有待确认的任务。",
            "dry_run": self.dry_run,
            "planning": None,
            "safety": None,
            "execution": None,
            "confirmation": None,
            "robot_state": self._state_snapshot(self._get_robot_state()),
            "environment_state": self._state_snapshot(self._get_environment_state()),
            "memory_error": None,
        }

    def _latest_pending_confirmation_record(self) -> dict[str, Any] | None:
        try:
            records = self.memory.latest_records(limit=1000000, session_id=self.session_id)
        except TypeError:
            records = self.memory.latest_records(limit=1000000)
            records = [
                record
                for record in records
                if record.get("session", {}).get("session_id") == self.session_id
            ]
        except Exception:
            return None

        resolved_turns = {
            (record.get("confirmation") or {}).get("pending_turn_index")
            for record in records
            if (record.get("confirmation") or {}).get("status") in {"confirmed", "cancelled"}
        }
        for record in reversed(records):
            turn_index = record.get("session", {}).get("turn_index")
            if turn_index in resolved_turns:
                continue
            if (
                record.get("status") == "awaiting_confirmation"
                and (record.get("confirmation") or {}).get("status") == "pending"
            ):
                return record
        return None

    def _planning_result_from_record(self, record: dict[str, Any]) -> PlanningResult:
        planning = record.get("planning") or {}
        plan_payload = planning.get("plan")
        plan: Plan | None = None
        if isinstance(plan_payload, dict):
            steps = [
                PlanStep(
                    skill_name=str(step.get("skill_name")),
                    inputs=dict(step.get("inputs") or {}),
                )
                for step in plan_payload.get("steps", [])
                if isinstance(step, dict)
            ]
            plan = Plan(intent=str(plan_payload.get("intent") or planning.get("intent")), steps=steps)

        return PlanningResult(
            status=str(planning.get("status") or "planned"),
            message=str(planning.get("message") or "Recovered pending plan."),
            intent=planning.get("intent"),
            target_floor=planning.get("target_floor"),
            plan=plan,
        )

    def _extract_floor(self, command: str) -> int | None:
        match = re.search(r"([0-9]+|[一二三四五六七八九十])楼", command)
        if match is None:
            return None
        token = match.group(1)
        if token.isdigit():
            return int(token)
        return CHINESE_DIGITS.get(token)
