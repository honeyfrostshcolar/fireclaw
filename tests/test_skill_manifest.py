import json
import sys

import pytest

from fireclaw_core.skill_manifest import load_subprocess_skill_from_manifest


def test_load_subprocess_skill_from_manifest(tmp_path):
    manifest_path = tmp_path / "rl_navigation.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "rl_navigation",
                "description": "Runs an isolated RL navigation policy.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'action': 'move'}}))",
                ],
                "timeout_seconds": 2.0,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(manifest_path)

    assert skill.name == "rl_navigation"
    assert skill.runtime == "subprocess"
    assert skill.dry_run_only is True
    assert skill.run({}).data == {"action": "move"}


def test_load_subprocess_skill_manifest_with_execution_and_safety_metadata(tmp_path):
    manifest_path = tmp_path / "rl_navigation.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "rl_navigation",
                "description": "Runs an isolated RL navigation policy.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "timeout_seconds": 2.5,
                "dry_run_only": True,
                "max_attempts": 2,
                "idempotent": True,
                "required_sensors": ["rgb_camera", "thermal_camera"],
                "failure_categories": ["timeout", "perception_uncertain"],
                "allow_real_robot": False,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(manifest_path)

    assert skill.max_attempts == 2
    assert skill.idempotent is True
    assert skill.required_sensors == ["rgb_camera", "thermal_camera"]
    assert skill.failure_categories == ["timeout", "perception_uncertain"]
    assert skill.allow_real_robot is False
    assert skill.timeout_seconds == 2.5


def test_load_subprocess_skill_manifest_with_input_schema(tmp_path):
    manifest_path = tmp_path / "echo.skill.json"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    manifest_path.write_text(
        json.dumps(
            {
                "name": "echo",
                "description": "Echo text.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "input_schema": input_schema,
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(manifest_path)

    assert skill.input_schema == input_schema


def test_load_subprocess_skill_manifest_with_risk_level(tmp_path):
    manifest_path = tmp_path / "entry.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "smoke_entry",
                "description": "High-risk smoke entry.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {}}))",
                ],
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )

    skill = load_subprocess_skill_from_manifest(manifest_path)

    assert skill.risk_level == "high"


def test_manifest_rejects_non_subprocess_runtime(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad runtime.",
                "runtime": "external_conda",
                "command": [sys.executable],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_shell_string_command(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad command.",
                "runtime": "subprocess",
                "command": "python script.py",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="command"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_retry_without_idempotency(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Retry is unsafe without idempotency.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "max_attempts": 2,
                "idempotent": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="idempotent"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_malformed_required_sensors(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad sensors.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "required_sensors": ["rgb_camera", ""],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="required_sensors"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_real_robot_allowance_when_dry_run_only(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Contradictory robot execution flags.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "dry_run_only": True,
                "allow_real_robot": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="allow_real_robot"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_malformed_input_schema(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad input schema.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "input_schema": {"type": "array"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="input_schema"):
        load_subprocess_skill_from_manifest(manifest_path)


def test_manifest_rejects_invalid_risk_level(tmp_path):
    manifest_path = tmp_path / "bad.skill.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Bad risk level.",
                "runtime": "subprocess",
                "command": [sys.executable],
                "risk_level": "extreme",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="risk_level"):
        load_subprocess_skill_from_manifest(manifest_path)
