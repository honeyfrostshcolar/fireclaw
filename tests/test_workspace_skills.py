import json
import sys

from fireclaw_core.workspace_skills import load_workspace_skills


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


def test_load_workspace_skills_discovers_valid_manifests_and_ignores_unrelated_files(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "root.skill.json", name="root_skill")
    _write_manifest(skills_dir / "nested" / "nested.skill.json", name="nested_skill")
    (skills_dir / "ignored.json").write_text("{}", encoding="utf-8")

    result = load_workspace_skills(skills_dir)

    assert [skill.name for skill in result.skills] == ["nested_skill", "root_skill"]
    assert result.errors == []


def test_load_workspace_skills_reports_invalid_manifests_without_crashing(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_manifest(skills_dir / "valid.skill.json", name="valid_skill")
    _write_manifest(skills_dir / "invalid.skill.json", name="invalid_skill", runtime="external_conda")

    result = load_workspace_skills(skills_dir)

    assert [skill.name for skill in result.skills] == ["valid_skill"]
    assert len(result.errors) == 1
    assert result.errors[0].path.endswith("invalid.skill.json")
    assert "runtime" in result.errors[0].message


def test_load_workspace_skills_returns_empty_result_for_missing_directory(tmp_path):
    result = load_workspace_skills(tmp_path / "missing-skills")

    assert result.skills == []
    assert result.errors == []
