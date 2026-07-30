from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from fireclaw_core.execution.runtime import SandboxedSkillExecutor
from fireclaw_core.execution.skills import Skill
from fireclaw_core.infra.skill_manifest import (
    load_subprocess_skill_from_manifest,
)
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import DeploymentProfile


@dataclass(frozen=True)
class WorkspaceSkillLoadError:
    path: str
    message: str


@dataclass(frozen=True)
class WorkspaceSkillLoadResult:
    skills: list[Skill]
    errors: list[WorkspaceSkillLoadError]


def load_workspace_skills(
    root: str | Path,
    *,
    plugin_host: FireClawPluginHost | None = None,
    deployment_profile: DeploymentProfile | None = None,
    sandbox_executor: SandboxedSkillExecutor | None = None,
    inspect_only: bool = False,
) -> WorkspaceSkillLoadResult:
    root_path = Path(root)
    if not root_path.exists():
        return WorkspaceSkillLoadResult(skills=[], errors=[])

    skills: list[Skill] = []
    errors: list[WorkspaceSkillLoadError] = []
    for manifest_path in sorted(root_path.glob("**/*.skill.json")):
        try:
            inspected = load_subprocess_skill_from_manifest(
                manifest_path,
                inspect_only=True,
            )
            deployment_decision = None
            if inspect_only:
                loaded = inspected
            else:
                if deployment_profile is None:
                    raise ValueError(
                        "Legacy executable Skill manifests require an explicit "
                        "deployment profile."
                    )
                deployment_decision = deployment_profile.evaluate(
                    tool_name=inspected.name,
                    effect="process",
                    requires_sandbox=True,
                )
                if deployment_decision.status != "allow":
                    raise ValueError(
                        "Legacy executable Skill manifest blocked by deployment "
                        f"policy {deployment_decision.profile_id!r}: "
                        f"{deployment_decision.status}."
                    )
                if sandbox_executor is None:
                    raise ValueError(
                        "Legacy executable Skill manifest passed policy but no "
                        "sandbox executor is configured."
                    )
                loaded = load_subprocess_skill_from_manifest(
                    manifest_path,
                    sandbox_executor=sandbox_executor,
                )
            plugin_id = str(
                loaded.metadata.get("plugin_id")
                or f"fireclaw.workspace.{loaded.name}"
            )
            skill = replace(
                loaded,
                metadata={
                    **dict(loaded.metadata),
                    "plugin_id": plugin_id,
                    "source": "workspace_skill",
                    "manifest_path": str(manifest_path),
                    "legacy_manifest": True,
                    "effect": "process",
                    "requires_sandbox": True,
                    "deployment_decision": (
                        deployment_decision.to_dict()
                        if deployment_decision is not None
                        else None
                    ),
                },
            )
            if plugin_host is not None and not inspect_only:
                plugin_host.activate(
                    plugin_id,
                    lambda api, value=skill: api.register_tool(
                        value,
                        metadata={
                            "execution_boundary": "docker_sandbox",
                            "legacy_manifest": True,
                        },
                    ),
                    name=skill.name,
                    description=skill.description,
                    source="workspace_skill",
                    trust_level="sandboxed",
                )
            skills.append(skill)
        except Exception as exc:
            errors.append(
                WorkspaceSkillLoadError(
                    path=str(manifest_path),
                    message=str(exc),
                )
            )
    return WorkspaceSkillLoadResult(skills=skills, errors=errors)
