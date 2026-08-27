"""Stable data contracts for LLM-facing Tools contributed by Plugins.

This module deliberately has no dependency on FireClaw's agent, policy, or
registry implementation.  It is the boundary that an independently packaged
Python Plugin can target.  The host remains responsible for projecting a
``ToolSpec`` through deployment policy and for executing its handler.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Dict, Literal, Mapping, Protocol


DeploymentMode = Literal["simulation", "real"]
AgentRole = Literal["mission_agent", "robot_agent"]
ToolEffect = Literal[
    "read",
    "bounded_mutation",
    "process",
    "host_admin",
    "credential_access",
    "real_hardware",
]
ToolHandler = Callable[[Mapping[str, Any]], Any]
PhysicalToolHandler = Callable[..., Any]
InputCoercion = Literal["identity", "float", "integer", "string", "object"]

_MISSING = object()

_VALID_MODES = frozenset({"simulation", "real"})
_VALID_ROLES = frozenset({"mission_agent", "robot_agent"})
_VALID_EFFECTS = frozenset(
    {
        "read",
        "bounded_mutation",
        "process",
        "host_admin",
        "credential_access",
        "real_hardware",
    }
)
_MAX_TOOL_TIMEOUT_SECONDS = 300.0
_MAX_PHYSICAL_TOOL_TIMEOUT_SECONDS = 1800.0
_MAX_PHYSICAL_CANCELLATION_ACK_SECONDS = 30.0
_MAX_TOOL_RESULT_BYTES = 1024 * 1024


class PluginApi(Protocol):
    """Stable subset of the injected Plugin API for Tool extensions.

    The host may expose additional contribution methods, but Tool-only
    extensions can type their entrypoint against this narrow contract.
    """

    id: str
    name: str
    version: str | None
    root_dir: Path
    mode: DeploymentMode
    role: str
    config: Mapping[str, Any]
    services: Mapping[str, Any]

    def register_tool(
        self,
        tool: ToolSpec,
        *,
        name: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def register_physical_tool(self, tool: "PhysicalToolSpec") -> None:
        """Register one physical Tool and its trusted execution handler."""
        ...

    def register_service(
        self,
        service_id: str,
        service: Any,
        *,
        data_only: bool = False,
    ) -> None:
        """Register one Plugin-owned service behind the trusted host boundary."""
        ...


@dataclass(frozen=True)
class ToolSpec:
    """Host-neutral description of one non-physical Agent Tool.

    ``handler`` is intentionally a callable rather than a ROS or shell
    command.  The extension owns the trusted adapter behind it; the host
    still applies role/mode policy, argument validation, authorization, and
    execution limits before invoking the callback.
    """

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: ToolHandler
    effect: ToolEffect = "read"
    roles: tuple[AgentRole, ...] = ("mission_agent", "robot_agent")
    modes: tuple[DeploymentMode, ...] = ("simulation", "real")
    requires_sandbox: bool = False
    result_authority: Literal["advisory"] = "advisory"
    max_execution_seconds: float = 30.0
    max_result_bytes: int = 256 * 1024
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = self.name.strip() if isinstance(self.name, str) else ""
        if not name:
            raise ValueError("ToolSpec name must be a non-empty string.")
        object.__setattr__(self, "name", name)

        description = (
            self.description.strip()
            if isinstance(self.description, str)
            else ""
        )
        if not description:
            raise ValueError("ToolSpec description must be a non-empty string.")
        object.__setattr__(self, "description", description)

        if not isinstance(self.input_schema, Mapping):
            raise TypeError("ToolSpec input_schema must be an object mapping.")
        schema = deepcopy(dict(self.input_schema))
        if schema.get("type") != "object":
            raise ValueError("ToolSpec input_schema must describe an object.")
        object.__setattr__(self, "input_schema", schema)

        if not callable(self.handler):
            raise TypeError("ToolSpec handler must be callable.")
        if self.effect not in _VALID_EFFECTS:
            raise ValueError(f"Unsupported ToolSpec effect: {self.effect!r}")

        roles = tuple(self.roles)
        if not roles or any(role not in _VALID_ROLES for role in roles):
            raise ValueError(
                "ToolSpec roles must contain only mission_agent or robot_agent."
            )
        object.__setattr__(self, "roles", roles)

        modes = tuple(self.modes)
        if not modes or any(mode not in _VALID_MODES for mode in modes):
            raise ValueError(
                "ToolSpec modes must contain only simulation or real."
            )
        object.__setattr__(self, "modes", modes)

        if not isinstance(self.requires_sandbox, bool):
            raise TypeError("ToolSpec requires_sandbox must be boolean.")
        if self.result_authority != "advisory":
            raise ValueError(
                "ToolSpec result_authority must be 'advisory' in API v1."
            )
        if (
            isinstance(self.max_execution_seconds, bool)
            or not isinstance(self.max_execution_seconds, (int, float))
            or not isfinite(float(self.max_execution_seconds))
            or self.max_execution_seconds <= 0
            or self.max_execution_seconds > _MAX_TOOL_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "ToolSpec max_execution_seconds is outside the API v1 limit."
            )
        if (
            isinstance(self.max_result_bytes, bool)
            or not isinstance(self.max_result_bytes, int)
            or self.max_result_bytes <= 0
            or self.max_result_bytes > _MAX_TOOL_RESULT_BYTES
        ):
            raise ValueError(
                "ToolSpec max_result_bytes is outside the API v1 limit."
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("ToolSpec metadata must be an object mapping.")
        object.__setattr__(self, "metadata", deepcopy(dict(self.metadata)))


@dataclass(frozen=True)
class TaskInputBindingSpec:
    """Public task-contract binding used by physical Plugin Tools."""

    input_name: str
    source_paths: tuple[tuple[str, ...], ...]
    required: bool = False
    protected: bool = True
    default: Any = _MISSING
    coercion: InputCoercion = "identity"

    def __post_init__(self) -> None:
        if not isinstance(self.input_name, str) or not self.input_name.strip():
            raise ValueError("TaskInputBindingSpec input_name must not be empty.")
        paths = tuple(tuple(str(part) for part in path) for path in self.source_paths)
        if not paths or any(not path or any(not part for part in path) for path in paths):
            raise ValueError("TaskInputBindingSpec source_paths must not be empty.")
        object.__setattr__(self, "input_name", self.input_name.strip())
        object.__setattr__(self, "source_paths", paths)
        if self.coercion not in {"identity", "float", "integer", "string", "object"}:
            raise ValueError("TaskInputBindingSpec has invalid coercion.")

    @property
    def has_default(self) -> bool:
        return self.default is not _MISSING


@dataclass(frozen=True)
class PhysicalToolSpec:
    """Host-neutral contract for one physical Tool supplied by a Plugin.

    The handler is owned by the Plugin and may call ROS, a robot SDK, or a
    bounded runtime service supplied through ``PluginApi.services``.  The
    FireClaw host adds lifecycle events, policy checks, authorization and
    cancellation around it; the core does not require a same-named method on
    ``RobotAdapter``.
    """

    plugin_id: str
    name: str
    label: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    action: str
    handler: PhysicalToolHandler
    input_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    domain: str = "robot"
    safety_class: str = "physical_action"
    risk_level: str = "low"
    dry_run_only: bool = True
    allow_real_robot: bool = False
    max_attempts: int = 1
    idempotent: bool = False
    timeout_seconds: float | None = None
    cancellation_ack_timeout_seconds: float = 2.0
    required_sensors: tuple[str, ...] = ()
    sensor_alternatives: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    failure_categories: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    degraded_mode_policy: str | None = None
    task_input_bindings: tuple[TaskInputBindingSpec, ...] = ()
    auto_include_for_target: bool = False
    task_type: str | None = None
    default_followup_tools: tuple[str, ...] = ()
    robot_agent_supplemental: bool = False
    resource_locks: tuple[str, ...] = ("robot",)
    success_evidence: tuple[str, ...] = ("robot_action_succeeded",)
    operator_started_message: Callable[[dict[str, Any]], str] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "plugin_id",
            "name",
            "label",
            "description",
            "action",
            "domain",
            "safety_class",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"PhysicalToolSpec {field_name} must not be empty.")
        object.__setattr__(self, "input_schema", deepcopy(dict(self.input_schema)))
        object.__setattr__(self, "output_schema", deepcopy(dict(self.output_schema)))
        if self.input_schema.get("type") != "object":
            raise ValueError("PhysicalToolSpec input_schema must be an object schema.")
        if self.output_schema.get("type") != "object":
            raise ValueError("PhysicalToolSpec output_schema must be an object schema.")
        if not callable(self.handler):
            raise TypeError("PhysicalToolSpec handler must be callable.")
        if self.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("PhysicalToolSpec has invalid risk_level.")
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("PhysicalToolSpec max_attempts must be positive.")
        if self.max_attempts > 1 and not self.idempotent:
            raise ValueError(
                "PhysicalToolSpec must be idempotent when max_attempts > 1."
            )
        if self.timeout_seconds is not None:
            if (
                isinstance(self.timeout_seconds, bool)
                or not isinstance(self.timeout_seconds, (int, float))
                or not isfinite(float(self.timeout_seconds))
                or self.timeout_seconds <= 0
                or self.timeout_seconds > _MAX_PHYSICAL_TOOL_TIMEOUT_SECONDS
            ):
                raise ValueError("PhysicalToolSpec timeout is outside API limits.")
        if (
            isinstance(self.cancellation_ack_timeout_seconds, bool)
            or not isinstance(
                self.cancellation_ack_timeout_seconds,
                (int, float),
            )
            or not isfinite(float(self.cancellation_ack_timeout_seconds))
            or self.cancellation_ack_timeout_seconds <= 0
            or self.cancellation_ack_timeout_seconds
            > _MAX_PHYSICAL_CANCELLATION_ACK_SECONDS
        ):
            raise ValueError(
                "PhysicalToolSpec cancellation acknowledgement timeout is "
                "outside API limits."
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("PhysicalToolSpec metadata must be an object mapping.")
        object.__setattr__(self, "metadata", deepcopy(dict(self.metadata)))
        object.__setattr__(self, "sensor_alternatives", deepcopy(dict(self.sensor_alternatives)))
        object.__setattr__(self, "task_input_bindings", tuple(self.task_input_bindings))


__all__ = [
    "AgentRole",
    "DeploymentMode",
    "PluginApi",
    "PhysicalToolHandler",
    "PhysicalToolSpec",
    "TaskInputBindingSpec",
    "ToolEffect",
    "ToolHandler",
    "ToolSpec",
]
