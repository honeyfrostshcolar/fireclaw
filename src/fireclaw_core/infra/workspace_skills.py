from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from fireclaw_core.infra.skill_manifest import load_subprocess_skill_from_manifest
from fireclaw_core.execution.skills import Skill
from fireclaw_core.plugin.plugin_host import FireClawPluginHost


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
) -> WorkspaceSkillLoadResult:
    root_path = Path(root)
    if not root_path.exists():
        return WorkspaceSkillLoadResult(skills=[], errors=[])

    skills: list[Skill] = []
    errors: list[WorkspaceSkillLoadError] = []
    for manifest_path in sorted(root_path.glob("**/*.skill.json")):
        try:
            loaded = load_subprocess_skill_from_manifest(manifest_path)
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
                },
            )
            if plugin_host is not None:
                plugin_host.activate(
                    plugin_id,
                    lambda api, value=skill: api.register_tool(value),
                    name=skill.name,
                    description=skill.description,
                    source="workspace_skill",
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
