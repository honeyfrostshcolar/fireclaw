from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable, Literal

from fireclaw_core.plugin.plugin_host import FireClawPluginHost


InputCoercion = Literal["identity", "float", "integer", "string", "object"]
ActionInputBuilder = Callable[[Any, dict[str, Any]], dict[str, Any]]
PhysicalActionHandler = Callable[..., Any]
OperatorMessageBuilder = Callable[[dict[str, Any]], str]

_MISSING = object()


@dataclass(frozen=True)
class TaskInputBinding:
    """Bind one host-owned task target value to one tool input.

    Bindings are part of the trusted skill definition. The model may propose
    the resulting tool input, but protected values must still equal the task
    contract at the Robot Agent policy boundary.
    """

    input_name: str
    source_paths: tuple[tuple[str, ...], ...]
    required: bool = False
    protected: bool = True
    default: Any = _MISSING
    coercion: InputCoercion = "identity"

    def __post_init__(self) -> None:
        if not self.input_name.strip():
            raise ValueError("task input binding input_name must not be empty")
        if not self.source_paths:
            raise ValueError("task input binding requires at least one source path")
        if any(not path or any(not part for part in path) for path in self.source_paths):
            raise ValueError("task input binding source paths must not be empty")

    def resolve(self, target: dict[str, Any]) -> tuple[bool, Any]:
        for path in self.source_paths:
            found, value = _read_path(target, path)
            if found:
                return True, _coerce(value, self.coercion)
        if self.default is not _MISSING:
            return True, _coerce(self.default, self.coercion)
        return False, None

    def to_metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "input_name": self.input_name,
            "source_paths": [".".join(path) for path in self.source_paths],
            "required": self.required,
            "protected": self.protected,
            "coercion": self.coercion,
        }
        if self.default is not _MISSING:
            result["default"] = self.default
        return result


@dataclass(frozen=True)
class PhysicalSkillPlugin:
    """Unbound physical capability definition, analogous to an OpenClaw tool plugin."""

    plugin_id: str
    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    output_schema: dict[str, Any]
    action: str
    action_input_builder: ActionInputBuilder
    domain: str
    safety_class: str
    # New Plugin-owned physical Tools provide this handler directly.  The
    # optional field keeps old RobotAdapter method bindings working while the
    # migration is completed.
    action_handler: PhysicalActionHandler | None = None
    risk_level: str = "low"
    dry_run_only: bool = True
    allow_real_robot: bool = False
    max_attempts: int = 1
    idempotent: bool = False
    timeout_seconds: float | None = None
    required_sensors: tuple[str, ...] = ()
    sensor_alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)
    failure_categories: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    degraded_mode_policy: str | None = None
    task_input_bindings: tuple[TaskInputBinding, ...] = ()
    auto_include_for_target: bool = False
    task_type: str | None = None
    default_followup_skills: tuple[str, ...] = ()
    robot_agent_supplemental: bool = False
    resource_locks: tuple[str, ...] = ("robot",)
    success_evidence: tuple[str, ...] = ("robot_action_succeeded",)
    operator_started_message: OperatorMessageBuilder | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

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
                raise ValueError(f"physical skill plugin {field_name} must not be empty")
        if self.parameters.get("type") != "object":
            raise ValueError("physical skill plugin parameters must be an object schema")
        if self.output_schema.get("type") != "object":
            raise ValueError("physical skill plugin output_schema must be an object schema")
        if self.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("physical skill plugin has invalid risk_level")
        if self.max_attempts < 1:
            raise ValueError("physical skill plugin max_attempts must be positive")
        if self.max_attempts > 1 and not self.idempotent:
            raise ValueError(
                "physical skill plugin must be idempotent when max_attempts > 1"
            )
        binding_names = [binding.input_name for binding in self.task_input_bindings]
        if len(binding_names) != len(set(binding_names)):
            raise ValueError("physical skill plugin has duplicate task input bindings")

    def task_inputs(self, target: dict[str, Any]) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for binding in self.task_input_bindings:
            found, value = binding.resolve(target)
            if found:
                inputs[binding.input_name] = value
            elif binding.required:
                raise ValueError(
                    f"skill {self.name!r} requires task target value for "
                    f"{binding.input_name!r}"
                )
        return inputs

    def protected_input_errors(
        self,
        *,
        target: dict[str, Any],
        proposed_inputs: dict[str, Any],
    ) -> list[str]:
        errors: list[str] = []
        for binding in self.task_input_bindings:
            if not binding.protected:
                continue
            found, expected = binding.resolve(target)
            if not found:
                continue
            proposed = proposed_inputs.get(binding.input_name, _MISSING)
            if proposed is _MISSING:
                if binding.default is not _MISSING:
                    continue
                errors.append(
                    f"skill {self.name!r} omits protected input "
                    f"{binding.input_name!r}"
                )
                continue
            try:
                normalized = _coerce(proposed, binding.coercion)
            except (TypeError, ValueError):
                normalized = proposed
            if normalized != expected:
                errors.append(
                    f"skill {self.name!r} uses {binding.input_name} "
                    f"{proposed!r}, expected {expected!r}"
                )
        return errors

    def matches_target(self, target: dict[str, Any]) -> bool:
        if not self.auto_include_for_target or not self.task_input_bindings:
            return False
        return all(
            binding.resolve(target)[0]
            for binding in self.task_input_bindings
            if binding.required
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            **dict(self.metadata),
            "plugin_id": self.plugin_id,
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "parameters": dict(self.parameters),
            "input_schema": dict(self.parameters),
            "output_schema": dict(self.output_schema),
            "action_binding": self.action,
            "domain": self.domain,
            "safety_class": self.safety_class,
            "risk_level": self.risk_level,
            "required_sensors": list(self.required_sensors),
            "sensor_alternatives": {
                sensor: list(alternatives)
                for sensor, alternatives in self.sensor_alternatives.items()
            },
            "task_input_bindings": [
                binding.to_metadata() for binding in self.task_input_bindings
            ],
            "task_type": self.task_type,
            "default_followup_skills": list(self.default_followup_skills),
            "robot_agent_supplemental": self.robot_agent_supplemental,
            "resource_locks": list(self.resource_locks),
            "success_evidence": list(self.success_evidence),
        }


class PhysicalSkillCatalog:
    """Compatibility projection over the unified plugin host."""

    def __init__(self, host: FireClawPluginHost | None = None) -> None:
        self.host = host or FireClawPluginHost()

    @property
    def plugins(self) -> dict[str, PhysicalSkillPlugin]:
        return {
            item.contribution_id: item.value
            for item in self.host.contributions("physical_capability")
            if isinstance(item.value, PhysicalSkillPlugin)
        }

    def register(
        self,
        plugin: PhysicalSkillPlugin,
        *,
        replace: bool = False,
    ) -> None:
        existing = self.host.get("physical_capability", plugin.name)
        if existing is not None and not replace:
            raise ValueError(f"Physical skill plugin already registered: {plugin.name}")
        if existing is not None:
            self.host.dispose_plugin(existing.owner_plugin_id)
        self.host.activate(
            plugin.plugin_id,
            lambda api: api.register_physical_capability(plugin),
            name=plugin.label,
            description=plugin.description,
            source="builtin_physical_skill",
            trust_level="trusted",
        )

    def get(self, name: str) -> PhysicalSkillPlugin | None:
        item = self.host.get("physical_capability", name)
        if item is None or not isinstance(item.value, PhysicalSkillPlugin):
            return None
        return item.value

    def names(self) -> set[str]:
        return {
            item.contribution_id
            for item in self.host.contributions("physical_capability")
        }

    def auto_skills_for_target(self, target: dict[str, Any]) -> list[str]:
        return [
            plugin.name
            for plugin in self.plugins.values()
            if plugin.matches_target(target)
        ]

    def skill_chain_for_capability(self, capability: str) -> list[str]:
        plugin = self.get(capability)
        if plugin is None:
            return [capability]
        return [plugin.name, *plugin.default_followup_skills]

    def task_type_for_capability(self, capability: str) -> str:
        plugin = self.get(capability)
        if plugin is None or plugin.task_type is None:
            return capability
        return plugin.task_type

    def supplemental_skill_names(self) -> tuple[str, ...]:
        return tuple(
            plugin.name
            for plugin in self.plugins.values()
            if plugin.robot_agent_supplemental
        )


def define_physical_skill_plugin(
    *,
    plugin_id: str,
    name: str,
    label: str,
    description: str,
    parameters: dict[str, Any],
    output_schema: dict[str, Any],
    action: str,
    action_input_builder: ActionInputBuilder,
    domain: str,
    safety_class: str,
    **kwargs: Any,
) -> PhysicalSkillPlugin:
    """Define a trusted physical skill without touching the Agent runtime."""

    return PhysicalSkillPlugin(
        plugin_id=plugin_id,
        name=name,
        label=label,
        description=description,
        parameters=dict(parameters),
        output_schema=dict(output_schema),
        action=action,
        action_input_builder=action_input_builder,
        domain=domain,
        safety_class=safety_class,
        **kwargs,
    )


def validate_object_schema(
    schema: dict[str, Any],
    value: Any,
    *,
    path: str = "inputs",
) -> list[str]:
    """Validate the JSON-schema subset used by FireClaw tool contracts."""

    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors: list[str] = []
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    required = required if isinstance(required, list) else []
    for key in required:
        if key not in value:
            errors.append(f"{path}.{key} is required")
    if schema.get("additionalProperties") is False:
        for key in value:
            if key not in properties:
                errors.append(f"{path}.{key} is not allowed")
    for key, item in value.items():
        item_schema = properties.get(key)
        if not isinstance(item_schema, dict):
            continue
        errors.extend(_validate_schema_value(item_schema, item, f"{path}.{key}"))
    return errors


def _validate_schema_value(
    schema: dict[str, Any],
    value: Any,
    path: str,
) -> list[str]:
    expected = schema.get("type")
    if isinstance(expected, list):
        types = expected
    else:
        types = [expected]
    if expected is not None and not any(_matches_type(value, item) for item in types):
        return [f"{path} must be of type {expected}"]
    errors: list[str] = []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(float(value)):
            errors.append(f"{path} must be finite")
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            errors.append(f"{path} must be at least {schema['minimum']}")
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            errors.append(f"{path} must be at most {schema['maximum']}")
    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path} must contain at least {min_length} characters")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path} must contain at most {max_length} characters")
    if isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path} must contain at least {min_items} items")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path} must contain at most {max_items} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_schema_value(
                        item_schema,
                        item,
                        f"{path}[{index}]",
                    )
                )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path} must be one of {enum}")
    if isinstance(value, dict) and schema.get("type") == "object":
        errors.extend(validate_object_schema(schema, value, path=path))
    return errors


def _matches_type(value: Any, expected: Any) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _read_path(
    payload: dict[str, Any],
    path: tuple[str, ...],
) -> tuple[bool, Any]:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _coerce(value: Any, coercion: InputCoercion) -> Any:
    if coercion == "identity":
        return value
    if coercion == "float":
        if isinstance(value, bool):
            raise ValueError("boolean cannot be coerced to float")
        result = float(value)
        if not isfinite(result):
            raise ValueError("number must be finite")
        return result
    if coercion == "integer":
        if isinstance(value, bool):
            raise ValueError("boolean cannot be coerced to integer")
        return int(value)
    if coercion == "string":
        result = str(value)
        if not result:
            raise ValueError("string must not be empty")
        return result
    if coercion == "object":
        if not isinstance(value, dict):
            raise ValueError("value must be an object")
        return dict(value)
    raise ValueError(f"unsupported input coercion: {coercion}")
