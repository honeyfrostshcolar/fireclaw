from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any, Literal, Mapping


DeploymentMode = Literal["simulation", "real"]
AgentRole = Literal["mission_agent", "robot_agent"]
AgentToolEffect = Literal[
    "read",
    "bounded_mutation",
    "process",
    "physical",
    "host_admin",
    "credential_access",
    "real_hardware",
]
DeploymentToolStatus = Literal["allow", "block", "require_approval"]
DeploymentToolStageStatus = Literal[
    "allow",
    "block",
    "require_approval",
    "not_applicable",
]

DEPLOYMENT_POLICY_ID = "fireclaw.deployment-tool-policy:v1"
_SHA256_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_SANDBOX_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_SANDBOX_OUTPUT_CHARS = 1_000_000
_MAX_SANDBOX_TIMEOUT_SECONDS = 300.0
_MAX_SANDBOX_MEMORY_MB = 4_096
_MAX_SANDBOX_CPUS = 8.0
_MAX_SANDBOX_PIDS = 1_024
_MAX_SANDBOX_FILE_BYTES = 16 * 1024 * 1024
_MAX_SANDBOX_WORKSPACE_BYTES = 1024 * 1024 * 1024
_MAX_SANDBOX_WORKSPACE_FILES = 100_000
_MAX_SANDBOX_CONCURRENT_PROCESSES = 8
_VALID_MODES = frozenset({"simulation", "real"})
_VALID_ROLES = frozenset({"mission_agent", "robot_agent"})
_VALID_EFFECTS = frozenset({
    "read",
    "bounded_mutation",
    "process",
    "physical",
    "host_admin",
    "credential_access",
    "real_hardware",
})
_SIMULATION_HARD_DENIED_EFFECTS = frozenset({
    "host_admin",
    "credential_access",
    "real_hardware",
})
_REAL_HARD_DENIED_EFFECTS = frozenset({
    "process",
    "host_admin",
    "credential_access",
    "real_hardware",
})


@dataclass(frozen=True)
class DeploymentToolStageDecision:
    stage: str
    status: DeploymentToolStageStatus
    reason_code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class DeploymentToolDecision:
    policy_id: str
    profile_id: str
    mode: DeploymentMode
    role: AgentRole
    tool_name: str
    effect: AgentToolEffect
    status: DeploymentToolStatus
    stages: tuple[DeploymentToolStageDecision, ...]

    @property
    def blocking_stage(self) -> DeploymentToolStageDecision | None:
        blocked = next(
            (stage for stage in self.stages if stage.status == "block"),
            None,
        )
        if blocked is not None:
            return blocked
        return next(
            (
                stage
                for stage in self.stages
                if stage.status == "require_approval"
            ),
            None,
        )

    @property
    def reason_code(self) -> str:
        stage = self.blocking_stage
        return stage.reason_code if stage is not None else "deployment_tool_allowed"

    @property
    def message(self) -> str:
        stage = self.blocking_stage
        return (
            stage.message
            if stage is not None
            else "Deployment profile allows this tool."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "role": self.role,
            "tool_name": self.tool_name,
            "effect": self.effect,
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True)
class SandboxProfile:
    enabled: bool = False
    backend: str = "docker"
    workspace_root: Path = Path("data/fireclaw-sandbox")
    image: str | None = None
    image_digest: str | None = None
    network: str = "none"
    max_timeout_seconds: float = 30.0
    max_output_chars: int = 20_000
    max_output_bytes: int = 64_000
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 128
    max_file_bytes: int = 2 * 1024 * 1024
    max_workspace_bytes: int = 64 * 1024 * 1024
    max_workspace_files: int = 4_096
    max_concurrent_processes: int = 1
    allowed_workspace_roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        from fireclaw_core.infra.path_security import (
            validate_sandbox_workspace_root,
        )

        allowed_roots = tuple(
            path.expanduser().resolve(strict=False)
            for path in self.allowed_workspace_roots
        )
        root = validate_sandbox_workspace_root(
            self.workspace_root,
            allowed_roots=allowed_roots,
        )
        object.__setattr__(self, "workspace_root", root)
        object.__setattr__(
            self,
            "allowed_workspace_roots",
            allowed_roots,
        )
        image = self.image.strip() if self.image else None
        image_digest = (
            self.image_digest.strip().lower()
            if self.image_digest
            else None
        )
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "image_digest", image_digest)
        if image_digest is not None and not _SHA256_IMAGE_ID_PATTERN.fullmatch(
            image_digest
        ):
            raise ValueError(
                "Sandbox image_digest must be a Docker sha256 image ID."
            )
        if image_digest is not None and image is None:
            raise ValueError(
                "Sandbox image_digest requires a configured image reference."
            )
        if self.enabled and image is not None and image_digest is None:
            raise ValueError(
                "Enabled Docker process sandboxes require image_digest; "
                "mutable image tags cannot be executed without an immutable pin."
            )
        if self.backend != "docker":
            raise ValueError(
                "FireClaw computer tool sandbox backend must be 'docker'; "
                "host subprocess fallback is prohibited."
            )
        if self.network != "none":
            raise ValueError(
                "Sandbox network must be 'none'; generic Docker bridge access "
                "is prohibited. Expose reviewed network operations as typed Tools."
            )
        if self.max_timeout_seconds <= 0:
            raise ValueError("Sandbox max_timeout_seconds must be positive.")
        if self.max_timeout_seconds > _MAX_SANDBOX_TIMEOUT_SECONDS:
            raise ValueError(
                "Sandbox max_timeout_seconds exceeds the hard safety limit."
            )
        if self.max_output_chars <= 0:
            raise ValueError("Sandbox max_output_chars must be positive.")
        if self.max_output_chars > _MAX_SANDBOX_OUTPUT_CHARS:
            raise ValueError(
                "Sandbox max_output_chars exceeds the hard safety limit."
            )
        if self.max_output_bytes <= 0:
            raise ValueError("Sandbox max_output_bytes must be positive.")
        if self.max_output_bytes > _MAX_SANDBOX_OUTPUT_BYTES:
            raise ValueError(
                "Sandbox max_output_bytes exceeds the hard safety limit."
            )
        if self.memory_mb < 64:
            raise ValueError("Sandbox memory_mb must be at least 64.")
        if self.memory_mb > _MAX_SANDBOX_MEMORY_MB:
            raise ValueError("Sandbox memory_mb exceeds the hard safety limit.")
        if self.cpus <= 0:
            raise ValueError("Sandbox cpus must be positive.")
        if self.cpus > _MAX_SANDBOX_CPUS:
            raise ValueError("Sandbox cpus exceeds the hard safety limit.")
        if self.pids_limit < 16:
            raise ValueError("Sandbox pids_limit must be at least 16.")
        if self.pids_limit > _MAX_SANDBOX_PIDS:
            raise ValueError("Sandbox pids_limit exceeds the hard safety limit.")
        if self.max_file_bytes <= 0:
            raise ValueError("Sandbox max_file_bytes must be positive.")
        if self.max_file_bytes > _MAX_SANDBOX_FILE_BYTES:
            raise ValueError(
                "Sandbox max_file_bytes exceeds the hard safety limit."
            )
        if self.max_workspace_bytes <= 0:
            raise ValueError("Sandbox max_workspace_bytes must be positive.")
        if self.max_workspace_bytes > _MAX_SANDBOX_WORKSPACE_BYTES:
            raise ValueError(
                "Sandbox max_workspace_bytes exceeds the hard safety limit."
            )
        if self.max_workspace_files <= 0:
            raise ValueError("Sandbox max_workspace_files must be positive.")
        if self.max_workspace_files > _MAX_SANDBOX_WORKSPACE_FILES:
            raise ValueError(
                "Sandbox max_workspace_files exceeds the hard safety limit."
            )
        if self.max_concurrent_processes <= 0:
            raise ValueError(
                "Sandbox max_concurrent_processes must be positive."
            )
        if (
            self.max_concurrent_processes
            > _MAX_SANDBOX_CONCURRENT_PROCESSES
        ):
            raise ValueError(
                "Sandbox max_concurrent_processes exceeds the hard safety limit."
            )

    @property
    def process_ready(self) -> bool:
        return bool(self.enabled and self.image and self.image_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "workspace_root": str(self.workspace_root),
            "image": self.image,
            "image_digest": self.image_digest,
            "image_pinned": bool(self.image and self.image_digest),
            "network": self.network,
            "max_timeout_seconds": self.max_timeout_seconds,
            "max_output_chars": self.max_output_chars,
            "max_output_bytes": self.max_output_bytes,
            "memory_mb": self.memory_mb,
            "cpus": self.cpus,
            "pids_limit": self.pids_limit,
            "max_file_bytes": self.max_file_bytes,
            "max_workspace_bytes": self.max_workspace_bytes,
            "max_workspace_files": self.max_workspace_files,
            "max_concurrent_processes": self.max_concurrent_processes,
            "allowed_workspace_roots": [
                str(path) for path in self.allowed_workspace_roots
            ],
            "process_ready": self.process_ready,
        }


@dataclass(frozen=True)
class DeploymentProfile:
    mode: DeploymentMode
    role: AgentRole
    sandbox: SandboxProfile
    allow: tuple[str, ...] | None = None
    also_allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(
                "deployment mode must be one of: real, simulation"
            )
        if self.role not in _VALID_ROLES:
            raise ValueError(
                "deployment role must be one of: mission_agent, robot_agent"
            )
        for field_name, selectors in (
            ("allow", self.allow or ()),
            ("also_allow", self.also_allow),
            ("deny", self.deny),
        ):
            if any(
                not isinstance(selector, str) or not selector.strip()
                for selector in selectors
            ):
                raise ValueError(
                    f"deployment tools.{field_name} must contain "
                    "non-empty strings"
                )

    @property
    def profile_id(self) -> str:
        return f"{self.role}.{self.mode}"

    def evaluate(
        self,
        *,
        tool_name: str,
        effect: AgentToolEffect,
        requires_sandbox: bool,
    ) -> DeploymentToolDecision:
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"Unsupported Agent Tool effect: {effect!r}")
        stages = (
            self._selector_stage(tool_name, effect),
            self._sandbox_stage(
                tool_name,
                effect,
                requires_sandbox=requires_sandbox,
            ),
            self._effect_stage(tool_name, effect),
        )
        status: DeploymentToolStatus = "allow"
        if any(stage.status == "block" for stage in stages):
            status = "block"
        elif any(stage.status == "require_approval" for stage in stages):
            status = "require_approval"
        return DeploymentToolDecision(
            policy_id=DEPLOYMENT_POLICY_ID,
            profile_id=self.profile_id,
            mode=self.mode,
            role=self.role,
            tool_name=tool_name,
            effect=effect,
            status=status,
            stages=stages,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": DEPLOYMENT_POLICY_ID,
            "profile_id": self.profile_id,
            "mode": self.mode,
            "role": self.role,
            "tools": {
                "allow": list(self.allow) if self.allow is not None else None,
                "also_allow": list(self.also_allow),
                "deny": list(self.deny),
            },
            "sandbox": self.sandbox.to_dict(),
        }

    def _selector_stage(
        self,
        tool_name: str,
        effect: AgentToolEffect,
    ) -> DeploymentToolStageDecision:
        evidence = {
            "allow": list(self.allow) if self.allow is not None else None,
            "also_allow": list(self.also_allow),
            "deny": list(self.deny),
        }
        if _matches_any(self.deny, tool_name, effect):
            return _stage(
                "tool_selector",
                "block",
                "tool_explicitly_denied",
                f"Tool {tool_name!r} is denied by the deployment profile.",
                evidence,
            )
        selectors = (
            (*self.allow, *self.also_allow)
            if self.allow is not None
            else ()
        )
        if self.allow is not None and not _matches_any(
            selectors,
            tool_name,
            effect,
        ):
            return _stage(
                "tool_selector",
                "block",
                "tool_not_in_allowlist",
                f"Tool {tool_name!r} is outside the deployment allowlist.",
                evidence,
            )
        return _stage(
            "tool_selector",
            "allow",
            "tool_selector_allowed",
            "Tool name passed deployment allow/deny filtering.",
            evidence,
        )

    def _sandbox_stage(
        self,
        tool_name: str,
        effect: AgentToolEffect,
        *,
        requires_sandbox: bool,
    ) -> DeploymentToolStageDecision:
        evidence = {
            "requires_sandbox": requires_sandbox,
            "sandbox": self.sandbox.to_dict(),
        }
        if not requires_sandbox:
            return _stage(
                "sandbox",
                "not_applicable",
                "sandbox_not_required",
                "Tool does not require the computer sandbox.",
                evidence,
            )
        if not self.sandbox.enabled:
            return _stage(
                "sandbox",
                "block",
                "sandbox_disabled",
                f"Tool {tool_name!r} requires an enabled sandbox.",
                evidence,
            )
        if effect == "process" and not self.sandbox.process_ready:
            return _stage(
                "sandbox",
                "block",
                "sandbox_process_backend_unavailable",
                "Process tools require a configured sandbox image.",
                evidence,
            )
        return _stage(
            "sandbox",
            "allow",
            "sandbox_ready",
            "The configured sandbox can contain this tool.",
            evidence,
        )

    def _effect_stage(
        self,
        tool_name: str,
        effect: AgentToolEffect,
    ) -> DeploymentToolStageDecision:
        hard_denied = (
            _SIMULATION_HARD_DENIED_EFFECTS
            if self.mode == "simulation"
            else _REAL_HARD_DENIED_EFFECTS
        )
        evidence = {
            "mode": self.mode,
            "role": self.role,
            "effect": effect,
            "hard_denied_effects": sorted(hard_denied),
        }
        if effect in hard_denied:
            return _stage(
                "effect_policy",
                "block",
                "tool_effect_forbidden",
                (
                    f"Deployment profile {self.profile_id!r} forbids "
                    f"{effect!r} tools such as {tool_name!r}."
                ),
                evidence,
            )
        if self.mode == "real" and effect == "bounded_mutation":
            return _stage(
                "effect_policy",
                "require_approval",
                "real_mutation_requires_approval",
                "Real deployment requires backend approval for bounded mutation.",
                evidence,
            )
        return _stage(
            "effect_policy",
            "allow",
            "tool_effect_allowed",
            "Deployment mode allows this tool effect.",
            evidence,
        )


def deployment_profile_from_config(
    value: Mapping[str, Any] | None,
    *,
    role: AgentRole,
    default_workspace_root: str | Path,
    allowed_workspace_roots: tuple[str | Path, ...] = (),
    path_base: str | Path | None = None,
) -> DeploymentProfile:
    raw = dict(value or {})
    mode_value = raw.get("mode", "real")
    if not isinstance(mode_value, str) or mode_value not in _VALID_MODES:
        raise ValueError("deployment.mode must be 'real' or 'simulation'")
    mode: DeploymentMode = mode_value  # type: ignore[assignment]

    tools_root = _mapping(raw.get("tools"))
    role_tools = _mapping(tools_root.get(role))
    allow = _optional_selectors(role_tools, "allow")
    also_allow = _selectors(role_tools, "also_allow")
    deny = _selectors(role_tools, "deny")

    sandbox_root = _mapping(raw.get("sandbox"))
    role_sandbox = _mapping(sandbox_root.get(role))
    workspace_value = role_sandbox.get(
        "workspace_root",
        str(default_workspace_root),
    )
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise ValueError(
            f"deployment.sandbox.{role}.workspace_root must be a path string"
        )
    workspace_path = Path(workspace_value).expanduser()
    if not workspace_path.is_absolute() and path_base is not None:
        workspace_path = Path(path_base).expanduser() / workspace_path
    sandbox = SandboxProfile(
        enabled=_boolean(role_sandbox, "enabled", False),
        backend=_string(role_sandbox, "backend", "docker"),
        workspace_root=workspace_path,
        image=_optional_string(role_sandbox, "image"),
        image_digest=_optional_string(role_sandbox, "image_digest"),
        network=_string(role_sandbox, "network", "none"),
        max_timeout_seconds=_positive_number(
            role_sandbox,
            "max_timeout_seconds",
            30.0,
        ),
        max_output_chars=_positive_int(
            role_sandbox,
            "max_output_chars",
            20_000,
        ),
        max_output_bytes=_positive_int(
            role_sandbox,
            "max_output_bytes",
            64_000,
        ),
        memory_mb=_positive_int(role_sandbox, "memory_mb", 512),
        cpus=_positive_number(role_sandbox, "cpus", 1.0),
        pids_limit=_positive_int(role_sandbox, "pids_limit", 128),
        max_file_bytes=_positive_int(
            role_sandbox,
            "max_file_bytes",
            2 * 1024 * 1024,
        ),
        max_workspace_bytes=_positive_int(
            role_sandbox,
            "max_workspace_bytes",
            64 * 1024 * 1024,
        ),
        max_workspace_files=_positive_int(
            role_sandbox,
            "max_workspace_files",
            4_096,
        ),
        max_concurrent_processes=_positive_int(
            role_sandbox,
            "max_concurrent_processes",
            1,
        ),
        allowed_workspace_roots=tuple(
            Path(path) for path in allowed_workspace_roots
        ),
    )
    return DeploymentProfile(
        mode=mode,
        role=role,
        sandbox=sandbox,
        allow=allow,
        also_allow=also_allow,
        deny=deny,
    )


def validate_robot_deployment_binding(
    profile: DeploymentProfile,
    *,
    dry_run: bool,
    embodied_runtime_mode: str | None,
) -> None:
    if profile.role != "robot_agent":
        raise ValueError("Robot deployment binding requires robot_agent role.")
    if (
        profile.mode == "simulation"
        and not dry_run
        and embodied_runtime_mode != "simulation"
    ):
        raise ValueError(
            "A simulation deployment with live adapter execution requires "
            "robot_gateway.embodied_runtime_mode='simulation'. This prevents "
            "simulation tool policy from silently binding to real hardware."
        )


def _matches_any(
    selectors: tuple[str, ...],
    tool_name: str,
    effect: AgentToolEffect,
) -> bool:
    return any(
        _matches_selector(selector, tool_name, effect)
        for selector in selectors
    )


def _matches_selector(
    selector: str,
    tool_name: str,
    effect: AgentToolEffect,
) -> bool:
    normalized = selector.strip()
    if normalized in {"*", "group:all"}:
        return True
    if normalized == "group:computer":
        return effect in {"read", "bounded_mutation", "process"}
    if normalized == f"group:{effect}":
        return True
    return fnmatchcase(tool_name, normalized)


def _stage(
    stage: str,
    status: DeploymentToolStageStatus,
    reason_code: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> DeploymentToolStageDecision:
    return DeploymentToolStageDecision(
        stage=stage,
        status=status,
        reason_code=reason_code,
        message=message,
        evidence=evidence or {},
    )


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("deployment configuration sections must be tables")
    return dict(value)


def _optional_selectors(
    mapping: Mapping[str, Any],
    key: str,
) -> tuple[str, ...] | None:
    if key not in mapping:
        return None
    return _selectors(mapping, key)


def _selectors(
    mapping: Mapping[str, Any],
    key: str,
) -> tuple[str, ...]:
    value = mapping.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip()
        for item in value
    ):
        raise ValueError(
            f"deployment tool policy {key} must be a list of strings"
        )
    return tuple(item.strip() for item in value)


def _boolean(
    mapping: Mapping[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = mapping.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"deployment sandbox {key} must be a boolean")
    return value


def _string(
    mapping: Mapping[str, Any],
    key: str,
    default: str,
) -> str:
    value = mapping.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"deployment sandbox {key} must be a string")
    return value.strip()


def _optional_string(
    mapping: Mapping[str, Any],
    key: str,
) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"deployment sandbox {key} must be a string")
    return value.strip()


def _positive_number(
    mapping: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    value = mapping.get(key, default)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(
            f"deployment sandbox {key} must be a positive number"
        )
    return float(value)


def _positive_int(
    mapping: Mapping[str, Any],
    key: str,
    default: int,
) -> int:
    value = mapping.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(
            f"deployment sandbox {key} must be a positive integer"
        )
    return value
