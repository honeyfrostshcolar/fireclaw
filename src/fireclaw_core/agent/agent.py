from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Protocol

from fireclaw_core.approval.execution_authorization import (
    ExecutionAuthorization,
    HmacExecutionAuthorizationAuthority,
    VerifiedExecutionAuthorization,
    authorized_action,
    execution_scope_hash,
)
from fireclaw_core.execution.action_runtime import RobotActionRuntime, RobotAdapterActionBackend
from fireclaw_core.execution.runtime import SandboxedSkillExecutor
from fireclaw_core.execution.execution_event_producer import (
    RobotExecutionEventProducer,
)
from fireclaw_core.execution.executor import CancellationCheck, ExecutionEventSink, ExecutionResult, PlanExecutor
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.memory.embodied_memory import EmbodiedMemoryProducer
from fireclaw_core.memory.robot_memory import RobotMemoryRecorder, RobotMemorySnapshot
from fireclaw_core.planner.planner import (
    CHINESE_DIGITS,
    Plan,
    PlannerContext,
    PlanningResult,
    PlanStep,
    RuleBasedPlanner,
)
from fireclaw_core.agent.robot import DryRunRobotAdapter, RobotAdapter
from fireclaw_core.safety.safety import SafetyDecision, SafetyGate
from fireclaw_core.execution.skills import create_default_skill_registry
from fireclaw_core.task.task_contract import StructuredRobotTask, planning_result_from_structured_task
from fireclaw_core.infra.workspace_skills import WorkspaceSkillLoadError, load_workspace_skills
from fireclaw_core.context.manager import StructuredSemanticCompactor
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.capability import (
    CAPABILITY_POLICY_ID,
    CapabilityActor,
    CapabilityPolicyContext,
    CapabilityPolicyDecision,
    CapabilityPolicyPipeline,
    CapabilityPolicyProjection,
    CapabilityRobotProfile,
    CapabilityRuntimeState,
)
from fireclaw_core.policy.deployment import DeploymentProfile


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


@dataclass(frozen=True)
class DeliberatedStepExecution:
    """One SafetyGate-checked Robot Agent step and its internal result."""

    payload: dict[str, Any]
    execution_result: ExecutionResult | None


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
        safety_memory_producer: EmbodiedMemoryProducer | None = None,
        skill_memory_producer: EmbodiedMemoryProducer | None = None,
        embodied_runtime_mode: str | None = None,
        robot_memory_recorder: RobotMemoryRecorder | None = None,
        execution_event_producer: RobotExecutionEventProducer | None = None,
        execution_authorization_authority: (
            HmacExecutionAuthorizationAuthority | None
        ) = None,
        execution_authorization: ExecutionAuthorization | None = None,
        resource_lease_manager: Any | None = None,
        authorization_use_recorder: Any | None = None,
        plugin_host: FireClawPluginHost | None = None,
        capability_actor: CapabilityActor | Any | None = None,
        robot_profile: Any | None = None,
        deployment_profile: DeploymentProfile | None = None,
        workspace_skill_executor: SandboxedSkillExecutor | None = None,
    ) -> None:
        self.robot = robot or DryRunRobotAdapter(robot_id="fireclaw-dry-run")
        self.memory = memory or JsonlMemoryStore("memory/fireclaw-runs.jsonl")
        self.dry_run = dry_run
        # If no sensors specified, infer from robot adapter when possible
        self._available_sensors_override = available_sensors is not None
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
        self._robot_memory_recorder = robot_memory_recorder
        self._execution_event_producer = (
            execution_event_producer or RobotExecutionEventProducer()
        )
        self._execution_authorization_authority = (
            execution_authorization_authority
        )
        self._execution_authorization = execution_authorization
        self._last_authorization_error: str | None = None
        self._resource_lease_manager = resource_lease_manager
        self.capability_actor = (
            CapabilityActor.from_value(capability_actor)
            if capability_actor is not None
            else CapabilityActor(
                actor_id="local-operator",
                role="operator",
                scopes=frozenset({"task.submit"}),
                source="local",
            )
        )
        self.capability_robot_profile = CapabilityRobotProfile.from_value(
            robot_profile
        )
        action_runtime = RobotActionRuntime(
            backend=RobotAdapterActionBackend(self.robot),
            event_sink=event_sink,
            task_id=task_id,
        )
        self.plugin_host = plugin_host or FireClawPluginHost()
        self.registry = create_default_skill_registry(
            self.robot,
            action_runtime=action_runtime,
            plugin_host=self.plugin_host,
        )
        self.skill_load_errors: list[WorkspaceSkillLoadError] = []
        if workspace_skills_dir is not None:
            workspace_result = load_workspace_skills(
                workspace_skills_dir,
                plugin_host=self.plugin_host,
                deployment_profile=deployment_profile,
                sandbox_executor=workspace_skill_executor,
            )
            self.skill_load_errors = workspace_result.errors
        self.capability_policy = CapabilityPolicyPipeline(
            plugin_host=self.plugin_host,
            registry=self.registry,
        )
        self.safety = SafetyGate(
            memory_producer=safety_memory_producer,
            runtime_mode=embodied_runtime_mode,
        )
        self.executor = PlanExecutor(
            self.registry,
            event_sink=event_sink,
            cancellation_requested=cancellation_requested,
            memory_producer=skill_memory_producer,
            runtime_mode=embodied_runtime_mode,
            mission_id=session_id,
            robot_id=getattr(self.robot, "robot_id", None),
            subtask_id=task_id,
            resource_lease_manager=resource_lease_manager,
            authorization_use_recorder=authorization_use_recorder,
        )

    def _safety_available_sensors(self) -> set[str] | None:
        if self._available_sensors_override:
            return self.available_sensors
        return None

    def project_skill_capabilities(
        self,
        structured_task: StructuredRobotTask,
        *,
        skill_names: set[str] | tuple[str, ...] | list[str],
        robot_state: Any | None = None,
    ) -> CapabilityPolicyProjection:
        context = self._capability_policy_context(
            phase="planning",
            structured_task=structured_task,
            robot_state=(
                robot_state
                if robot_state is not None
                else self._get_robot_state()
            ),
        )
        projection = self.capability_policy.project(
            skill_names,
            context=context,
        )
        self._emit_event(
            "capability.policy_projection",
            projection.to_dict(),
        )
        return projection

    def _capability_policy_context(
        self,
        *,
        phase: str,
        structured_task: StructuredRobotTask | None,
        robot_state: Any,
        safety_decision: SafetyDecision | None = None,
        execution_authorization: (
            VerifiedExecutionAuthorization | None
        ) = None,
    ) -> CapabilityPolicyContext:
        robot_id = str(
            (
                structured_task.robot_id
                if structured_task is not None
                else None
            )
            or getattr(self.robot, "robot_id", None)
            or "unknown-robot"
        )
        allowed_skills: frozenset[str] | None = None
        if structured_task is not None:
            from fireclaw_core.agent.robot_agent import (
                envelope_from_structured_task,
            )

            envelope = envelope_from_structured_task(
                structured_task,
                fallback_robot_id=robot_id,
            )
            allowed_skills = frozenset(envelope.allowed_skills)
        return CapabilityPolicyContext(
            phase=phase,  # type: ignore[arg-type]
            actor=self.capability_actor,
            robot_id=robot_id,
            mission_id=(
                structured_task.mission_id
                if structured_task is not None
                and structured_task.mission_id is not None
                else self.session_id
            ),
            task_id=(
                structured_task.task_id
                if structured_task is not None
                else self.task_id
            ),
            delegated_operator_id=(
                structured_task.operator_id
                if structured_task is not None
                else None
            ),
            allowed_skills=allowed_skills,
            target=(
                dict(structured_task.target)
                if structured_task is not None
                else {}
            ),
            robot_profile=self.capability_robot_profile,
            runtime_state=CapabilityRuntimeState.from_values(
                robot_state,
                emergency_stop_active=self._resource_admission_closed(),
            ),
            dry_run=self.dry_run,
            safety_status=(
                safety_decision.status
                if safety_decision is not None
                else None
            ),
            safety_reasons=(
                tuple(safety_decision.reasons)
                if safety_decision is not None
                else ()
            ),
            execution_authorization=execution_authorization,
        )

    def _resource_admission_closed(self) -> bool:
        admission_state = getattr(
            self._resource_lease_manager,
            "admission_state",
            None,
        )
        if not callable(admission_state):
            return False
        try:
            state = admission_state()
        except Exception:
            return True
        return not isinstance(state, dict) or bool(state.get("closed"))

    def _execute_policy_checked_plan(
        self,
        *,
        planning_result: PlanningResult,
        structured_task: StructuredRobotTask | None,
        safety_decision: SafetyDecision,
        safety_event_id: str | None,
        execution_authorization: (
            VerifiedExecutionAuthorization | None
        ),
        robot_state: Any,
        operation_id: str | None,
    ) -> tuple[
        ExecutionResult | None,
        dict[str, Any],
    ]:
        plan = planning_result.plan
        preflight: list[CapabilityPolicyDecision] = []
        execution: list[CapabilityPolicyDecision] = []
        if plan is not None:
            context = self._capability_policy_context(
                phase="execution",
                structured_task=structured_task,
                robot_state=robot_state,
                safety_decision=safety_decision,
                execution_authorization=execution_authorization,
            )
            preflight = [
                self.capability_policy.evaluate(
                    step.skill_name,
                    dict(step.inputs),
                    context=context,
                )
                for step in plan.steps
            ]
            self._emit_event(
                "capability.policy_preflight",
                {
                    "policy_id": CAPABILITY_POLICY_ID,
                    "phase": "execution",
                    "decisions": [
                        decision.to_dict() for decision in preflight
                    ],
                },
            )

        execution_result: ExecutionResult | None = None
        if safety_decision.status == "allow" and plan is not None:
            def check_capability(
                skill: Any,
                inputs: dict[str, Any],
            ) -> CapabilityPolicyDecision:
                live_context = self._capability_policy_context(
                    phase="execution",
                    structured_task=structured_task,
                    robot_state=self._get_robot_state(),
                    safety_decision=safety_decision,
                    execution_authorization=execution_authorization,
                )
                decision = self.capability_policy.evaluate(
                    skill.name,
                    inputs,
                    context=live_context,
                )
                execution.append(decision)
                return decision

            execution_result = self.executor.execute(
                plan,
                parent_memory_event_id=safety_event_id,
                operation_id=operation_id,
                execution_authorization=execution_authorization,
                capability_policy_check=check_capability,
            )
        return execution_result, {
            "policy_id": CAPABILITY_POLICY_ID,
            "preflight": [
                decision.to_dict() for decision in preflight
            ],
            "execution": [
                decision.to_dict() for decision in execution
            ],
        }

    def _authorization_for(
        self,
        structured_task: StructuredRobotTask | None,
    ) -> ExecutionAuthorization | None:
        if (
            structured_task is not None
            and structured_task.execution_authorization is not None
        ):
            return structured_task.execution_authorization
        return self._execution_authorization

    def _verify_execution_authorization(
        self,
        *,
        command: str,
        planning_result: PlanningResult,
        structured_task: StructuredRobotTask | None,
    ) -> VerifiedExecutionAuthorization | None:
        authorization = self._authorization_for(structured_task)
        if authorization is None:
            self._last_authorization_error = "authorization_missing"
            return None
        if self._execution_authorization_authority is None:
            self._last_authorization_error = "authorization_verifier_unavailable"
            return None
        if planning_result.plan is None:
            self._last_authorization_error = "authorization_plan_missing"
            return None
        actions = [
            authorized_action(step.skill_name, step.inputs)
            for step in planning_result.plan.steps
        ]
        scope_hash = execution_scope_hash(
            command=command,
            structured_task=(
                structured_task.to_dict()
                if structured_task is not None
                else None
            ),
            actions=actions,
        )
        verification = self._execution_authorization_authority.verify(
            authorization,
            robot_id=str(
                getattr(self.robot, "robot_id", None) or "unknown-robot"
            ),
            scope_hash=scope_hash,
            action_hashes={
                str(action["action_hash"]) for action in actions
            },
        )
        self._last_authorization_error = verification.error_code
        return verification.grant if verification.verified else None

    def _record_robot_snapshot(
        self, robot_state: Any, environment_state: Any
    ) -> RobotMemorySnapshot:
        if self._robot_memory_recorder is None:
            return RobotMemorySnapshot()
        return self._robot_memory_recorder.record_snapshot(
            mission_id=self.session_id,
            robot_state=robot_state,
            environment_state=environment_state,
            subtask_id=self.task_id,
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
        memory_snapshot = self._record_robot_snapshot(robot_state_object, environment_state_object)
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        verified_authorization = self._verify_execution_authorization(
            command=resolved_command,
            planning_result=planning_result,
            structured_task=None,
        )
        safety_decision, safety_event_id = self.safety.evaluate_with_memory_event(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self._safety_available_sensors(),
            execution_authorization=verified_authorization,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
            mission_id=self.session_id,
            subtask_id=self.task_id,
            evidence_event_ids=memory_snapshot.evidence_event_ids,
        )
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result, capability_policy_manifest = (
            self._execute_policy_checked_plan(
                planning_result=planning_result,
                structured_task=None,
                safety_decision=safety_decision,
                safety_event_id=safety_event_id,
                execution_authorization=verified_authorization,
                robot_state=robot_state_object,
                operation_id=(
                    verified_authorization.authorization_id
                    if verified_authorization is not None
                    else self.task_id
                ),
            )
        )

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
            "capability_policy": capability_policy_manifest,
            "confirmation": self._confirmation_to_dict(
                safety_decision,
                verified_authorization,
            ),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }

        self._attach_execution_event(
            result,
            execution_result=execution_result,
            structured_task=None,
        )
        self._append_memory_result(result)
        return result

    def run_structured_task(self, task: StructuredRobotTask) -> dict[str, Any]:
        return self.run_planning_result(
            command=task.command or task.task_type,
            structured_task=task,
            planning_result=planning_result_from_structured_task(
                task,
                skill_catalog=self.registry,
            ),
        )

    def run_planning_result(
        self,
        *,
        command: str,
        planning_result: PlanningResult,
        structured_task: StructuredRobotTask | None = None,
    ) -> dict[str, Any]:
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        memory_snapshot = self._record_robot_snapshot(robot_state_object, environment_state_object)
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        verified_authorization = self._verify_execution_authorization(
            command=command,
            planning_result=planning_result,
            structured_task=structured_task,
        )
        safety_decision, safety_event_id = self.safety.evaluate_with_memory_event(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self._safety_available_sensors(),
            execution_authorization=verified_authorization,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
            mission_id=self.session_id,
            subtask_id=self.task_id,
            evidence_event_ids=memory_snapshot.evidence_event_ids,
        )
        if structured_task is not None:
            self._emit_event("task.structured_received", structured_task.to_dict())
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result, capability_policy_manifest = (
            self._execute_policy_checked_plan(
                planning_result=planning_result,
                structured_task=structured_task,
                safety_decision=safety_decision,
                safety_event_id=safety_event_id,
                execution_authorization=verified_authorization,
                robot_state=robot_state_object,
                operation_id=(
                    verified_authorization.authorization_id
                    if verified_authorization is not None
                    else self.task_id
                ),
            )
        )

        status = self._resolve_status(safety_decision, execution_result)
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "message": self._resolve_message(planning_result, safety_decision, execution_result),
            "dry_run": self.dry_run,
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "capability_policy": capability_policy_manifest,
            "confirmation": self._confirmation_to_dict(
                safety_decision,
                verified_authorization,
            ),
            "robot_state": robot_state,
            "environment_state": environment_state,
            "memory_error": None,
        }
        result["structured_task"] = structured_task.to_dict() if structured_task is not None else None
        # Note: session info (session_id, turn_index) is intentionally omitted here
        # to match the pre-existing run_structured_task() behavior. Session tracking
        # lives in the run() path for interactive commands.
        self._attach_execution_event(
            result,
            execution_result=execution_result,
            structured_task=structured_task,
        )
        self._append_memory_result(result)
        return result

    def execute_deliberated_step(
        self,
        *,
        command: str,
        planning_result: PlanningResult,
        structured_task: StructuredRobotTask,
        operation_id: str | None = None,
    ) -> DeliberatedStepExecution:
        """Safety-check and execute one step without finalizing the task.

        A recoverable failed step remains local evidence for the next Robot
        Agent turn. Mission invalidation is produced only when the bounded
        deliberation itself terminates unsuccessfully.
        """

        if planning_result.plan is None or len(planning_result.plan.steps) != 1:
            raise ValueError(
                "Robot Agent deliberation requires exactly one planned step"
            )
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        memory_snapshot = self._record_robot_snapshot(
            robot_state_object,
            environment_state_object,
        )
        verified_authorization = self._verify_execution_authorization(
            command=command,
            planning_result=planning_result,
            structured_task=structured_task,
        )
        safety_decision, safety_event_id = self.safety.evaluate_with_memory_event(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self._safety_available_sensors(),
            execution_authorization=verified_authorization,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
            mission_id=self.session_id,
            subtask_id=self.task_id,
            evidence_event_ids=memory_snapshot.evidence_event_ids,
        )
        self._emit_event(
            "robot_agent.step_planned",
            self._planning_to_dict(planning_result),
        )
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result, capability_policy_manifest = (
            self._execute_policy_checked_plan(
                planning_result=planning_result,
                structured_task=structured_task,
                safety_decision=safety_decision,
                safety_event_id=safety_event_id,
                execution_authorization=verified_authorization,
                robot_state=robot_state_object,
                operation_id=operation_id,
            )
        )
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": self._resolve_status(
                safety_decision,
                execution_result,
            ),
            "message": self._resolve_message(
                planning_result,
                safety_decision,
                execution_result,
            ),
            "dry_run": self.dry_run,
            "planning": self._planning_to_dict(planning_result),
            "safety": asdict(safety_decision),
            "execution": self._execution_to_dict(execution_result),
            "capability_policy": capability_policy_manifest,
            "confirmation": self._confirmation_to_dict(
                safety_decision,
                verified_authorization,
            ),
            "robot_state": self._state_snapshot(self._get_robot_state()),
            "environment_state": self._state_snapshot(
                self._get_environment_state()
            ),
            "structured_task": structured_task.to_dict(),
            "memory_error": None,
        }
        return DeliberatedStepExecution(
            payload=payload,
            execution_result=execution_result,
        )

    def finalize_deliberated_task(
        self,
        *,
        command: str,
        structured_task: StructuredRobotTask,
        loop_result: Any,
        step_executions: list[DeliberatedStepExecution],
    ) -> dict[str, Any]:
        """Create one terminal task result from a bounded Robot Agent run."""

        execution_steps = [
            step
            for run in step_executions
            if run.execution_result is not None
            for step in run.execution_result.steps
        ]
        if loop_result.status == "completed":
            execution_status = "succeeded"
            status = "completed"
        elif loop_result.status == "cancelled":
            execution_status = "cancelled"
            status = "cancelled"
        else:
            execution_status = "failed"
            last_observation = (
                loop_result.observations[-1]
                if loop_result.observations
                else None
            )
            status = (
                "awaiting_confirmation"
                if (
                    loop_result.status == "escalated"
                    and getattr(last_observation, "status", None)
                    == "approval_required"
                )
                else loop_result.status
            )
        aggregate_execution = ExecutionResult(
            status=execution_status,
            steps=execution_steps,
        )
        latest_step = (
            step_executions[-1].payload if step_executions else None
        )
        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "status": status,
            "message": loop_result.message,
            "dry_run": self.dry_run,
            "planning": {
                "status": "deliberated",
                "message": loop_result.message,
                "intent": structured_task.task_type,
                "target_floor": structured_task.target.get("floor"),
                "plan": None,
            },
            "safety": (
                latest_step.get("safety")
                if isinstance(latest_step, dict)
                else None
            ),
            "execution": self._execution_to_dict(aggregate_execution),
            "capability_policy": (
                latest_step.get("capability_policy")
                if isinstance(latest_step, dict)
                else None
            ),
            "confirmation": (
                latest_step.get("confirmation")
                if isinstance(latest_step, dict)
                else None
            ),
            "robot_state": self._state_snapshot(self._get_robot_state()),
            "environment_state": self._state_snapshot(
                self._get_environment_state()
            ),
            "structured_task": structured_task.to_dict(),
            "robot_agent_deliberation": loop_result.to_dict(),
            "memory_error": None,
        }
        self._attach_execution_event(
            result,
            execution_result=aggregate_execution,
            structured_task=structured_task,
        )
        self._append_memory_result(result)
        return result

    def _attach_execution_event(
        self,
        result: dict[str, Any],
        *,
        execution_result: ExecutionResult | None,
        structured_task: StructuredRobotTask | None,
    ) -> None:
        try:
            event = self._execution_event_producer.produce(
                execution_result=execution_result,
                mission_id=self.session_id,
                robot_id=getattr(self.robot, "robot_id", None),
                runtime_task_id=self.task_id,
                plan_node_id=(
                    structured_task.task_id
                    if structured_task is not None
                    else None
                ),
            )
        except Exception as exc:
            result["execution_event_error"] = str(exc)
            return
        if event is None:
            return
        event_payload = event.to_dict()
        result["invalidation_event"] = event_payload
        self._emit_event(
            "mission.plan_invalidated",
            {"invalidation_event": event_payload},
        )

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
        point_match = re.search(
            r"(?:坐标|目标点|点)?\s*[（(]?\s*"
            r"(-?\d+(?:\.\d+)?)\s*[,，]\s*"
            r"(-?\d+(?:\.\d+)?)\s*[)）]?",
            command,
        )
        if point_match is not None:
            previous = self._latest_session_record()
            if previous is not None and previous.get("status") == "clarify":
                return (
                    "去坐标 "
                    f"({point_match.group(1)}, {point_match.group(2)}) 救人",
                    True,
                )
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
        records = self._recent_session_records(limit=50)
        compaction_manifest = None
        if len(records) > 5:
            summary, compaction = StructuredSemanticCompactor().compact(
                "session_history",
                records[:-5],
            )
            records = [summary, *records[-5:]]
            compaction_manifest = compaction.to_dict()
        context_id = (
            f"{self.session_id}:robot-interactive-context:{turn_index}"
        )
        context_manifest = {
            "context_id": context_id,
            "scope": "robot_interactive_planner",
            "semantic_compaction": compaction_manifest,
        }
        context_envelope = {
            "context_id": context_id,
            "authoritative": {
                "session_id": self.session_id,
                "turn_index": turn_index,
                "skills": self.registry.list_metadata(),
            },
            "continuity": {},
            "advisory": {"session_history": records},
            "context_policy": {
                "safety_critical_context_preserved": True,
                "semantic_compaction": (
                    compaction_manifest is not None
                ),
            },
        }
        return PlannerContext(
            session_id=self.session_id,
            turn_index=turn_index,
            recent_records=records,
            skills=self.registry.list_metadata(),
            context_envelope=context_envelope,
            context_manifest=context_manifest,
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
        if (
            execution_result is not None
            and execution_result.status == "failed"
            and execution_result.steps
            and execution_result.steps[-1].error
        ):
            return str(execution_result.steps[-1].error)
        return planning_result.message

    def _planning_to_dict(self, planning_result: PlanningResult) -> dict[str, Any]:
        return {
            "status": planning_result.status,
            "message": planning_result.message,
            "intent": planning_result.intent,
            "target_floor": planning_result.target_floor,
            "target_pose": planning_result.target_pose,
            "plan": asdict(planning_result.plan) if planning_result.plan is not None else None,
        }

    def _execution_to_dict(self, execution_result: ExecutionResult | None) -> dict[str, Any] | None:
        if execution_result is None:
            return None
        return asdict(execution_result)

    def _confirmation_to_dict(
        self,
        safety_decision: SafetyDecision,
        execution_authorization: (
            VerifiedExecutionAuthorization | None
        ) = None,
    ) -> dict[str, Any] | None:
        if execution_authorization is not None:
            return {
                "status": "confirmed",
                "authorization_id": (
                    execution_authorization.authorization_id
                ),
                "request_id": execution_authorization.request_id,
                "operator_id": execution_authorization.operator_id,
                "scope_hash": execution_authorization.scope_hash,
                "expires_at": execution_authorization.expires_at,
            }
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
        pending_command = str(
            pending.get("session", {}).get("resolved_command")
            or pending.get("command")
            or ""
        )
        execution_authorization = self._verify_execution_authorization(
            command=pending_command,
            planning_result=planning_result,
            structured_task=None,
        )
        robot_state_object = self._get_robot_state()
        environment_state_object = self._get_environment_state()
        memory_snapshot = self._record_robot_snapshot(robot_state_object, environment_state_object)
        robot_state = self._state_snapshot(robot_state_object)
        environment_state = self._state_snapshot(environment_state_object)
        safety_decision, safety_event_id = self.safety.evaluate_with_memory_event(
            planning_result,
            self.registry,
            dry_run=self.dry_run,
            available_sensors=self._safety_available_sensors(),
            execution_authorization=execution_authorization,
            robot_state=robot_state_object,
            environment_state=environment_state_object,
            mission_id=self.session_id,
            subtask_id=self.task_id,
            evidence_event_ids=memory_snapshot.evidence_event_ids,
        )
        self._emit_event("task.planned", self._planning_to_dict(planning_result))
        self._emit_event("safety.decided", asdict(safety_decision))

        execution_result, capability_policy_manifest = (
            self._execute_policy_checked_plan(
                planning_result=planning_result,
                structured_task=None,
                safety_decision=safety_decision,
                safety_event_id=safety_event_id,
                execution_authorization=execution_authorization,
                robot_state=robot_state_object,
                operation_id=(
                    execution_authorization.authorization_id
                    if execution_authorization is not None
                    else self.task_id
                ),
            )
        )

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
            "capability_policy": capability_policy_manifest,
            "confirmation": (
                {
                    "status": "confirmed",
                    "authorization_id": (
                        execution_authorization.authorization_id
                        if execution_authorization is not None
                        else None
                    ),
                    "pending_turn_index": pending.get("session", {}).get(
                        "turn_index"
                    ),
                    "pending_command": pending.get("command"),
                }
                if safety_decision.status == "allow"
                else self._confirmation_to_dict(safety_decision)
            ),
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
            target_pose=(
                dict(planning["target_pose"])
                if isinstance(planning.get("target_pose"), dict)
                else None
            ),
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
