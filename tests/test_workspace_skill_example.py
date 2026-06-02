from pathlib import Path

from fireclaw_core.workspace_skills import load_workspace_skills


def test_example_echo_policy_loads_and_runs_from_workspace_manifest():
    skills_dir = Path("skills")

    result = load_workspace_skills(skills_dir)
    skill_by_name = {skill.name: skill for skill in result.skills}

    assert "echo_policy" in skill_by_name
    skill = skill_by_name["echo_policy"]
    output = skill.run({"target": "二楼", "dry_run": True})

    assert output.ok is True
    assert output.data["skill"] == "echo_policy"
    assert output.data["received"]["target"] == "二楼"
    assert output.data["dry_run"] is True
