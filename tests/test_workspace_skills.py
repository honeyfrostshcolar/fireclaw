import json
import sys
from dataclasses import replace

from fireclaw_core.infra.workspace_skills import load_workspace_skills


def _write_manifest(path, *, name="external_skill", command=None, runtime="subprocess"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": name,
                "description": f"{name} description.",
                "runtime": runtime,
                "command": command
                or [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'source': 'workspace'}}))",
                ],
                "timeout_seconds": 2,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )


def test_load_workspace_skills_discovers_valid_manifests_and_ignores_unrelated_files(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "root.skill.json", name="root_skill")
    _write_manifest(skills_dir / "nested" / "nested.skill.json", name="nested_skill")
    (skills_dir / "ignored.json").write_text("{}", encoding="utf-8")

    result = load_workspace_skills(
        skills_dir,
        deployment_profile=legacy_skill_profile,
        sandbox_executor=legacy_skill_executor,
    )

    assert [skill.name for skill in result.skills] == ["nested_skill", "root_skill"]
    assert result.errors == []


def test_load_workspace_skills_resolves_python_placeholder(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(
        skills_dir / "python.skill.json",
        command=[
            "{python}",
            "-c",
            "import json; print(json.dumps({'ok': True, 'data': {'python': 'current'}}))",
        ],
    )

    result = load_workspace_skills(
        skills_dir,
        deployment_profile=legacy_skill_profile,
        sandbox_executor=legacy_skill_executor,
    )

    assert result.errors == []
    action = result.skills[0].run({})
    assert action.ok is True
    assert action.data == {"python": "current"}


def test_load_workspace_skills_reports_invalid_manifests_without_crashing(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "valid.skill.json", name="valid_skill")
    _write_manifest(skills_dir / "invalid.skill.json", name="invalid_skill", runtime="external_conda")

    result = load_workspace_skills(
        skills_dir,
        deployment_profile=legacy_skill_profile,
        sandbox_executor=legacy_skill_executor,
    )

    assert [skill.name for skill in result.skills] == ["valid_skill"]
    assert len(result.errors) == 1
    assert result.errors[0].path.endswith("invalid.skill.json")
    assert "runtime" in result.errors[0].message


def test_load_workspace_skills_returns_empty_result_for_missing_directory(tmp_path):
    result = load_workspace_skills(tmp_path / "missing-skills")

    assert result.skills == []
    assert result.errors == []


def test_workspace_skill_fails_closed_without_explicit_policy_and_sandbox(
    tmp_path,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "unsafe.skill.json")

    result = load_workspace_skills(skills_dir)

    assert result.skills == []
    assert len(result.errors) == 1
    assert "explicit deployment profile" in result.errors[0].message


def test_real_profile_blocks_legacy_process_even_with_sandbox(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "unsafe.skill.json")

    result = load_workspace_skills(
        skills_dir,
        deployment_profile=replace(legacy_skill_profile, mode="real"),
        sandbox_executor=legacy_skill_executor,
    )

    assert result.skills == []
    assert len(result.errors) == 1
    assert "blocked by deployment policy" in result.errors[0].message


def test_denied_computer_tools_also_block_legacy_manifest_processes(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "unsafe.skill.json")

    result = load_workspace_skills(
        skills_dir,
        deployment_profile=replace(
            legacy_skill_profile,
            deny=("group:computer",),
        ),
        sandbox_executor=legacy_skill_executor,
    )

    assert result.skills == []
    assert len(result.errors) == 1
    assert "blocked by deployment policy" in result.errors[0].message


def test_inspection_mode_never_stages_or_executes_manifest(
    tmp_path,
    legacy_skill_executor,
):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "inspect.skill.json")

    result = load_workspace_skills(
        skills_dir,
        sandbox_executor=legacy_skill_executor,
        inspect_only=True,
    )

    assert [skill.name for skill in result.skills] == ["external_skill"]
    assert legacy_skill_executor.calls == []
    assert not (legacy_skill_executor.root / "external_skill").exists()
