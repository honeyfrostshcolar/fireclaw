from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fireclaw_core.skill_manifest import load_subprocess_skill_from_manifest
from fireclaw_core.skills import Skill


@dataclass(frozen=True)
class WorkspaceSkillLoadError:
    path: str
    message: str


@dataclass(frozen=True)
class WorkspaceSkillLoadResult:
    skills: list[Skill]
    errors: list[WorkspaceSkillLoadError]


def load_workspace_skills(root: str | Path) -> WorkspaceSkillLoadResult:
    root_path = Path(root)
    if not root_path.exists():
        return WorkspaceSkillLoadResult(skills=[], errors=[])

    skills: list[Skill] = []
    errors: list[WorkspaceSkillLoadError] = []
    for manifest_path in sorted(root_path.glob("**/*.skill.json")):
        try:
            skills.append(load_subprocess_skill_from_manifest(manifest_path))
        except Exception as exc:
            errors.append(
                WorkspaceSkillLoadError(
                    path=str(manifest_path),
                    message=str(exc),
                )
            )
    return WorkspaceSkillLoadResult(skills=skills, errors=errors)
