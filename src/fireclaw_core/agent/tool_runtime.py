from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal
from uuid import uuid4

from fireclaw_core.approval.execution_authorization import (
    VerifiedExecutionAuthorization,
    authorized_action,
    canonical_json_hash,
    execution_action_hash,
)
from fireclaw_core.execution.skill_plugin import validate_object_schema
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.plugin.trusted_callback import (
    TrustedCallbackTimeout,
    invoke_trusted_callback,
    validate_json_result_size,
)
from fireclaw_core.policy.deployment import (
    DEPLOYMENT_POLICY_ID,
    AgentRole,
    AgentToolEffect,
    DeploymentMode,
    DeploymentProfile,
    DeploymentToolDecision,
)


AgentToolHandler = Callable[[dict[str, Any]], Any]
AuthorizationUseRecorder = Callable[..., bool]
AgentToolExecutionStatus = Literal[
    "executed",
    "blocked",
    "approval_required",
    "error",
]
AgentToolAuthority = Literal["advisory"]
AGENT_TOOL_AUTHORIZATION_SCOPE_POLICY = (
    "fireclaw.agent-tool-authorization-scope:v1"
)
_AUTHORIZATION_CONTEXT_KEYS = (
    "mission_id",
    "task_id",
    "robot_id",
    "snapshot_id",
    "session_id",
)
_MAX_AGENT_TOOL_TIMEOUT_SECONDS = 300.0
_MAX_AGENT_TOOL_RESULT_BYTES = 1024 * 1024
_BEFORE_TOOL_HOOK_TIMEOUT_SECONDS = 2.0
_BEFORE_TOOL_HOOK_RESULT_BYTES = 64 * 1024


@dataclass(frozen=True)
class AgentTool:
    """A non-physical tool contributed through the shared Plugin Host."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: AgentToolHandler
    effect: AgentToolEffect = "read"
    roles: tuple[AgentRole, ...] = ("mission_agent", "robot_agent")
    modes: tuple[DeploymentMode, ...] = ("simulation", "real")
    requires_sandbox: bool = False
    result_authority: AgentToolAuthority = "advisory"
    max_execution_seconds: float = 30.0
    max_result_bytes: int = 256 * 1024
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Agent Tool name must be a non-empty string.")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError(
                f"Agent Tool {self.name!r} description must not be empty."
            )
        if self.effect == "physical":
            raise ValueError(
                "Physical actions must be registered as Skill contributions "
                "and pass the capability and SafetyGate pipeline."
            )
        if self.input_schema.get("type") != "object":
            raise ValueError(
                f"Agent Tool {self.name!r} input_schema must describe an object."
            )
        if not callable(self.handler):
            raise TypeError(f"Agent Tool {self.name!r} handler must be callable.")
        if not self.roles or not self.modes:
            raise ValueError(
                f"Agent Tool {self.name!r} must declare roles and modes."
            )
        if (
            self.max_execution_seconds <= 0
            or self.max_execution_seconds
            > _MAX_AGENT_TOOL_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"Agent Tool {self.name!r} max_execution_seconds is outside "
                "the hard safety range."
            )
        if (
            self.max_result_bytes <= 0
            or self.max_result_bytes > _MAX_AGENT_TOOL_RESULT_BYTES
        ):
            raise ValueError(
                f"Agent Tool {self.name!r} max_result_bytes is outside the "
                "hard safety range."
            )

    def validate_arguments(self, arguments: Any) -> list[str]:
        return validate_object_schema(
            self.input_schema,
            arguments,
            path="arguments",
        )

    def to_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.input_schema),
            },
        }


@dataclass(frozen=True)
class AgentToolProjection:
    name: str
    owner_plugin_id: str
    tool: AgentTool
    decision: DeploymentToolDecision

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner_plugin_id": self.owner_plugin_id,
            "effect": self.tool.effect,
            "requires_sandbox": self.tool.requires_sandbox,
            "result_authority": self.tool.result_authority,
            "decision": self.decision.to_dict(),
        }


@dataclass(frozen=True)
class AgentToolExecution:
    invocation_id: str
    tool_name: str
    status: AgentToolExecutionStatus
    arguments_hash: str
    decision: DeploymentToolDecision | None
    output: Any = None
    error_code: str | None = None
    message: str | None = None
    approval_request: dict[str, Any] | None = None
    authorization_id: str | None = None
    authorization_scope_hash: str | None = None
    authorization_operation_id: str | None = None
    result_authority: AgentToolAuthority = "advisory"

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "arguments_hash": self.arguments_hash,
            "decision": (
                self.decision.to_dict() if self.decision is not None else None
            ),
            "output": deepcopy(self.output),
            "error_code": self.error_code,
            "message": self.message,
            "approval_request": deepcopy(self.approval_request),
            "authorization_id": self.authorization_id,
            "authorization_scope_hash": self.authorization_scope_hash,
            "authorization_operation_id": self.authorization_operation_id,
            "result_authority": self.result_authority,
        }


class AgentToolRuntime:
    """Projects and executes non-physical tools through one policy boundary.

    This mirrors OpenClaw's layered tool projection and final-argument
    interception. FireClaw additionally separates physical skills entirely:
    this runtime cannot register or execute a physical action.
    """

    def __init__(
        self,
        *,
        plugin_host: FireClawPluginHost,
        profile: DeploymentProfile,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        authorization_use_recorder: AuthorizationUseRecorder | None = None,
    ) -> None:
        self.plugin_host = plugin_host
        self.profile = profile
        self._event_sink = event_sink
        self._authorization_use_recorder = authorization_use_recorder

    def projections(self, *, include_blocked: bool = False) -> tuple[AgentToolProjection, ...]:
        values: list[AgentToolProjection] = []
        for contribution in self.plugin_host.contributions("tool"):
            tool = contribution.value
            if not isinstance(tool, AgentTool):
                continue
            decision = self._evaluate_tool(tool)
            if decision.status == "block" and not include_blocked:
                continue
            values.append(
                AgentToolProjection(
                    name=tool.name,
                    owner_plugin_id=contribution.owner_plugin_id,
                    tool=tool,
                    decision=decision,
                )
            )
        return tuple(sorted(values, key=lambda item: item.name))

    def tool_schemas(self) -> list[dict[str, Any]]:
        return [
            projection.tool.to_tool_schema()
            for projection in self.projections()
        ]

    def manifest(self, *, include_blocked: bool = True) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "tools": [
                item.to_manifest()
                for item in self.projections(include_blocked=include_blocked)
            ],
        }

    def exposure_manifest(self) -> dict[str, Any]:
        """Return the small host-authoritative projection needed by the LLM."""

        return {
            "policy_id": DEPLOYMENT_POLICY_ID,
            "profile_id": self.profile.profile_id,
            "mode": self.profile.mode,
            "role": self.profile.role,
            "tools": [
                {
                    "name": item.name,
                    "effect": item.tool.effect,
                    "status": item.decision.status,
                    "result_authority": item.tool.result_authority,
                }
                for item in self.projections()
            ],
        }

    def has_tool(self, name: str) -> bool:
        return any(item.name == name for item in self.projections())

    def projection(self, name: str) -> AgentToolProjection | None:
        return next(
            (item for item in self.projections() if item.name == name),
            None,
        )

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        authorization: VerifiedExecutionAuthorization | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentToolExecution:
        invocation_id = f"agent-tool-{uuid4().hex}"
        initial_arguments = deepcopy(arguments)
        arguments_hash = execution_action_hash(name, initial_arguments)
        contribution = self.plugin_host.get("tool", name)
        if contribution is None or not isinstance(contribution.value, AgentTool):
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=None,
                    error_code="agent_tool_unavailable",
                    message="Agent Tool is not registered.",
                )
            )
        tool = contribution.value
        decision = self._evaluate_tool(tool)
        if decision.status == "block":
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code=decision.reason_code,
                    message=decision.message,
                )
            )

        errors = tool.validate_arguments(initial_arguments)
        if errors:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code="agent_tool_arguments_invalid",
                    message="; ".join(errors),
                )
            )

        execution_context = context or {}
        hook_result = self._run_before_tool_call_hooks(
            tool=tool,
            owner_plugin_id=contribution.owner_plugin_id,
            invocation_id=invocation_id,
            arguments=initial_arguments,
            context=execution_context,
        )
        if hook_result["blocked"]:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code=str(
                        hook_result.get("error_code")
                        or "before_tool_call_blocked"
                    ),
                    message=str(
                        hook_result.get("message")
                        or "A before_tool_call hook blocked execution."
                    ),
                )
            )

        final_arguments = hook_result["arguments"]
        arguments_hash = execution_action_hash(name, final_arguments)
        final_errors = tool.validate_arguments(final_arguments)
        if final_errors:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code="adjusted_agent_tool_arguments_invalid",
                    message="; ".join(final_errors),
                )
            )

        approval_required = (
            decision.status == "require_approval"
            or bool(hook_result["require_approval"])
        )
        authorization_scope_hash, authorization_scope_context = (
            _agent_tool_authorization_scope(
                profile=self.profile,
                owner_plugin_id=contribution.owner_plugin_id,
                tool=tool,
                arguments=final_arguments,
                context=execution_context,
            )
        )
        if approval_required and not _authorization_allows(
            authorization,
            name,
            final_arguments,
            required_scope_hash=authorization_scope_hash,
        ):
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="approval_required",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code="agent_tool_exact_approval_required",
                    message=(
                        "Backend approval must authorize this exact tool name "
                        "and final argument hash."
                    ),
                    approval_request={
                        "policy_id": AGENT_TOOL_AUTHORIZATION_SCOPE_POLICY,
                        "profile_id": self.profile.profile_id,
                        "scope_hash": authorization_scope_hash,
                        "scope_context": authorization_scope_context,
                        "authorized_action": authorized_action(
                            name,
                            final_arguments,
                        ),
                    },
                    authorization_scope_hash=authorization_scope_hash,
                )
            )

        # Re-resolve after hooks and approval so plugin disposal/replacement
        # cannot execute a stale handler.
        current = self.plugin_host.get("tool", name)
        if (
            current is None
            or current.owner_plugin_id != contribution.owner_plugin_id
            or current.value is not tool
        ):
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=decision,
                    error_code="agent_tool_changed_before_execution",
                    message="Agent Tool ownership changed before execution.",
                )
            )
        final_decision = self._evaluate_tool(tool)
        if final_decision.status == "block":
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="blocked",
                    arguments_hash=arguments_hash,
                    decision=final_decision,
                    error_code=final_decision.reason_code,
                    message=final_decision.message,
                )
            )

        authorization_operation_id: str | None = None
        if approval_required:
            assert authorization is not None
            authorization_operation_id = (
                "agent-tool:"
                f"{authorization.authorization_id}:"
                f"{arguments_hash}"
            )
            if self._authorization_use_recorder is None:
                return self._finish(
                    AgentToolExecution(
                        invocation_id=invocation_id,
                        tool_name=name,
                        status="blocked",
                        arguments_hash=arguments_hash,
                        decision=final_decision,
                        error_code=(
                            "agent_tool_authorization_consumption_unavailable"
                        ),
                        message=(
                            "Approved Agent Tool execution requires a "
                            "persistent one-time authorization consumer."
                        ),
                        authorization_id=authorization.authorization_id,
                        authorization_scope_hash=authorization_scope_hash,
                        authorization_operation_id=(
                            authorization_operation_id
                        ),
                    )
                )
            try:
                consumed = bool(
                    self._authorization_use_recorder(
                        authorization_id=authorization.authorization_id,
                        operation_id=authorization_operation_id,
                        action_hash=arguments_hash,
                        used_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
            except Exception as exc:
                return self._finish(
                    AgentToolExecution(
                        invocation_id=invocation_id,
                        tool_name=name,
                        status="blocked",
                        arguments_hash=arguments_hash,
                        decision=final_decision,
                        error_code=(
                            "agent_tool_authorization_consumption_failed"
                        ),
                        message=(
                            "The authorization use ledger failed closed with "
                            f"{type(exc).__name__}."
                        ),
                        authorization_id=authorization.authorization_id,
                        authorization_scope_hash=authorization_scope_hash,
                        authorization_operation_id=(
                            authorization_operation_id
                        ),
                    )
                )
            if not consumed:
                return self._finish(
                    AgentToolExecution(
                        invocation_id=invocation_id,
                        tool_name=name,
                        status="blocked",
                        arguments_hash=arguments_hash,
                        decision=final_decision,
                        error_code="agent_tool_authorization_already_used",
                        message=(
                            "This exact Agent Tool authorization was already "
                            "consumed or is no longer active."
                        ),
                        authorization_id=authorization.authorization_id,
                        authorization_scope_hash=authorization_scope_hash,
                        authorization_operation_id=(
                            authorization_operation_id
                        ),
                    )
                )

        try:
            output = invoke_trusted_callback(
                tool.handler,
                deepcopy(final_arguments),
                timeout_seconds=tool.max_execution_seconds,
            )
            validate_json_result_size(
                output,
                max_bytes=tool.max_result_bytes,
                description=f"Agent Tool {tool.name!r} result",
            )
        except TrustedCallbackTimeout:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="error",
                    arguments_hash=arguments_hash,
                    decision=final_decision,
                    error_code="agent_tool_handler_timed_out",
                    message=(
                        f"Agent Tool exceeded {tool.max_execution_seconds} "
                        "seconds."
                    ),
                    authorization_id=(
                        authorization.authorization_id
                        if authorization is not None
                        else None
                    ),
                    authorization_scope_hash=authorization_scope_hash,
                    authorization_operation_id=authorization_operation_id,
                )
            )
        except ValueError as exc:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="error",
                    arguments_hash=arguments_hash,
                    decision=final_decision,
                    error_code="agent_tool_result_invalid",
                    message=str(exc)[:500],
                    authorization_id=(
                        authorization.authorization_id
                        if authorization is not None
                        else None
                    ),
                    authorization_scope_hash=authorization_scope_hash,
                    authorization_operation_id=authorization_operation_id,
                )
            )
        except Exception as exc:
            return self._finish(
                AgentToolExecution(
                    invocation_id=invocation_id,
                    tool_name=name,
                    status="error",
                    arguments_hash=arguments_hash,
                    decision=final_decision,
                    error_code="agent_tool_handler_failed",
                    message=(
                        f"Agent Tool failed with {type(exc).__name__}: "
                        f"{str(exc)[:500]}"
                    ),
                    authorization_id=(
                        authorization.authorization_id
                        if authorization is not None
                        else None
                    ),
                    authorization_scope_hash=authorization_scope_hash,
                    authorization_operation_id=authorization_operation_id,
                )
            )
        return self._finish(
            AgentToolExecution(
                invocation_id=invocation_id,
                tool_name=name,
                status="executed",
                arguments_hash=arguments_hash,
                decision=final_decision,
                output=output,
                authorization_id=(
                    authorization.authorization_id
                    if authorization is not None
                    else None
                ),
                authorization_scope_hash=authorization_scope_hash,
                authorization_operation_id=authorization_operation_id,
            )
        )

    def _evaluate_tool(self, tool: AgentTool) -> DeploymentToolDecision:
        decision = self.profile.evaluate(
            tool_name=tool.name,
            effect=tool.effect,
            requires_sandbox=tool.requires_sandbox,
        )
        if self.profile.role not in tool.roles:
            return _replace_decision_with_block(
                decision,
                reason_code="agent_tool_role_mismatch",
                message=(
                    f"Agent Tool {tool.name!r} is not declared for "
                    f"{self.profile.role!r}."
                ),
            )
        if self.profile.mode not in tool.modes:
            return _replace_decision_with_block(
                decision,
                reason_code="agent_tool_mode_mismatch",
                message=(
                    f"Agent Tool {tool.name!r} is not declared for "
                    f"{self.profile.mode!r}."
                ),
            )
        return decision

    def _run_before_tool_call_hooks(
        self,
        *,
        tool: AgentTool,
        owner_plugin_id: str,
        invocation_id: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        current_arguments = deepcopy(arguments)
        require_approval = False
        for contribution in self.plugin_host.contributions("hook"):
            metadata = contribution.metadata
            if metadata.get("hook_type") != "before_tool_call":
                continue
            hook_name = str(metadata.get("hook_name") or "")
            if hook_name not in {"*", tool.name}:
                continue
            payload = {
                "invocation_id": invocation_id,
                "tool_name": tool.name,
                "tool_owner_plugin_id": owner_plugin_id,
                "profile": self.profile.to_dict(),
                "arguments": deepcopy(current_arguments),
                "context": deepcopy(context),
            }
            try:
                effect = invoke_trusted_callback(
                    contribution.value,
                    payload,
                    timeout_seconds=_BEFORE_TOOL_HOOK_TIMEOUT_SECONDS,
                )
                validate_json_result_size(
                    effect,
                    max_bytes=_BEFORE_TOOL_HOOK_RESULT_BYTES,
                    description=(
                        f"Hook {contribution.contribution_id!r} result"
                    ),
                )
            except TrustedCallbackTimeout:
                return {
                    "blocked": True,
                    "error_code": "before_tool_call_hook_timed_out",
                    "message": (
                        f"Hook {contribution.contribution_id!r} timed out."
                    ),
                    "arguments": current_arguments,
                    "require_approval": require_approval,
                }
            except Exception as exc:
                return {
                    "blocked": True,
                    "error_code": "before_tool_call_hook_failed",
                    "message": (
                        f"Hook {contribution.contribution_id!r} failed with "
                        f"{type(exc).__name__}."
                    ),
                    "arguments": current_arguments,
                    "require_approval": require_approval,
                }
            if effect is None:
                continue
            if not isinstance(effect, dict):
                return {
                    "blocked": True,
                    "error_code": "before_tool_call_hook_invalid",
                    "message": (
                        f"Hook {contribution.contribution_id!r} returned an "
                        "invalid effect."
                    ),
                    "arguments": current_arguments,
                    "require_approval": require_approval,
                }
            if effect.get("block"):
                return {
                    "blocked": True,
                    "error_code": str(
                        effect.get("reason_code")
                        or "before_tool_call_blocked"
                    ),
                    "message": str(
                        effect.get("message")
                        or "A before_tool_call hook blocked execution."
                    ),
                    "arguments": current_arguments,
                    "require_approval": require_approval,
                }
            adjusted = effect.get("arguments")
            if adjusted is not None:
                if not isinstance(adjusted, dict):
                    return {
                        "blocked": True,
                        "error_code": "before_tool_call_arguments_invalid",
                        "message": "Hook-adjusted arguments must be an object.",
                        "arguments": current_arguments,
                        "require_approval": require_approval,
                    }
                current_arguments = deepcopy(adjusted)
            require_approval = require_approval or bool(
                effect.get("require_approval")
            )
        return {
            "blocked": False,
            "arguments": current_arguments,
            "require_approval": require_approval,
        }

    def _finish(self, result: AgentToolExecution) -> AgentToolExecution:
        if self._event_sink is not None:
            self._event_sink(
                {
                    "type": "agent_tool.execution",
                    "payload": result.to_dict(),
                }
            )
        return result


def register_agent_tool(
    host: FireClawPluginHost,
    tool: AgentTool,
    *,
    owner_plugin_id: str,
    source: str = "agent_tool",
) -> None:
    host.activate(
        owner_plugin_id,
        lambda api: api.register_tool(
            tool,
            metadata={
                "tool_class": "agent_tool",
                "effect": tool.effect,
                "roles": list(tool.roles),
                "modes": list(tool.modes),
                "requires_sandbox": tool.requires_sandbox,
            },
        ),
        name=tool.name,
        description=tool.description,
        source=source,
        trust_level="trusted",
    )


def _authorization_allows(
    authorization: VerifiedExecutionAuthorization | None,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    required_scope_hash: str,
) -> bool:
    if authorization is None:
        return False
    try:
        expires_at = datetime.fromisoformat(
            authorization.expires_at.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        return False
    if datetime.now(timezone.utc) >= expires_at.astimezone(timezone.utc):
        return False
    return (
        authorization.scope_hash == required_scope_hash
        and authorization.authorizes(tool_name, arguments)
    )


def _agent_tool_authorization_scope(
    *,
    profile: DeploymentProfile,
    owner_plugin_id: str,
    tool: AgentTool,
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    scope_context = {
        key: value
        for key in _AUTHORIZATION_CONTEXT_KEYS
        if isinstance((value := context.get(key)), str) and value
    }
    tool_contract_hash = canonical_json_hash({
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "effect": tool.effect,
        "roles": list(tool.roles),
        "modes": list(tool.modes),
        "requires_sandbox": tool.requires_sandbox,
    })
    return (
        canonical_json_hash({
            "policy_id": AGENT_TOOL_AUTHORIZATION_SCOPE_POLICY,
            "deployment_policy_id": DEPLOYMENT_POLICY_ID,
            "profile_id": profile.profile_id,
            "owner_plugin_id": owner_plugin_id,
            "tool_contract_hash": tool_contract_hash,
            "action_hash": execution_action_hash(tool.name, arguments),
            "context": scope_context,
        }),
        scope_context,
    )


def _replace_decision_with_block(
    decision: DeploymentToolDecision,
    *,
    reason_code: str,
    message: str,
) -> DeploymentToolDecision:
    from fireclaw_core.policy.deployment import DeploymentToolStageDecision

    return DeploymentToolDecision(
        policy_id=decision.policy_id,
        profile_id=decision.profile_id,
        mode=decision.mode,
        role=decision.role,
        tool_name=decision.tool_name,
        effect=decision.effect,
        status="block",
        stages=(
            *decision.stages,
            DeploymentToolStageDecision(
                stage="tool_contract",
                status="block",
                reason_code=reason_code,
                message=message,
            ),
        ),
    )
