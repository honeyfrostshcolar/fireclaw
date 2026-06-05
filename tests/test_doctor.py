import json
import subprocess

from fireclaw_core.doctor import run_doctor


def _check(report, name):
    return next(check for check in report["checks"] if check["name"] == name)


def test_run_doctor_warns_for_mock_ros1_but_passes_control_boundaries(tmp_path):
    report = run_doctor(
        adapter="mock-ros1",
        robot_id="doctor-ros1",
        memory_path=str(tmp_path / "memory" / "runs.jsonl"),
        event_path=str(tmp_path / "events" / "events.jsonl"),
        skills_dir=str(tmp_path / "missing-skills"),
    )

    assert report["status"] == "warn"
    assert _check(report, "adapter")["status"] == "warn"
    assert _check(report, "adapter")["details"]["mode"] == "mock_ros1"
    assert _check(report, "memory_path")["status"] == "pass"
    assert _check(report, "event_path")["status"] == "pass"
    assert _check(report, "workspace_skills")["status"] == "pass"
    assert _check(report, "emergency_stop_hook")["status"] == "pass"
    assert _check(report, "action_feedback_boundary")["status"] == "pass"


def test_run_doctor_fails_invalid_workspace_skill_manifest(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "bad.skill.json").write_text(
        json.dumps(
            {
                "name": "bad",
                "description": "Invalid runtime.",
                "runtime": "external_conda",
                "command": ["python", "bad.py"],
            }
        ),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-dry",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=str(skills_dir),
    )

    assert report["status"] == "fail"
    skill_check = _check(report, "workspace_skills")
    assert skill_check["status"] == "fail"
    assert skill_check["details"]["error_count"] == 1
    assert skill_check["details"]["errors"][0]["path"].endswith("bad.skill.json")


def test_run_doctor_checks_ros1_config_readiness(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "doctor-real-ros1",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "action",
                        "name": "/fireclaw/doctor-real-ros1/navigation",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    }
                },
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/doctor-real-ros1/emergency_stop",
                    "type": "std_srvs/Trigger",
                },
            }
        ),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="ros1",
        robot_id="ignored",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=str(tmp_path / "missing-skills"),
        ros1_config_path=str(config_path),
    )

    assert report["status"] == "warn"
    assert _check(report, "adapter")["details"]["mode"] == "ros1"
    ros1_config = _check(report, "ros1_config")
    assert ros1_config["status"] == "warn"
    assert ros1_config["details"]["robot_id"] == "doctor-real-ros1"
    assert ros1_config["details"]["configured_actions"] == ["navigate_to_floor"]
    assert "search_for_victims" in ros1_config["details"]["missing_actions"]


def test_run_doctor_fails_ros1_without_config_path(tmp_path):
    report = run_doctor(
        adapter="ros1",
        robot_id="doctor-real-ros1",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=str(tmp_path / "missing-skills"),
    )

    assert report["status"] == "fail"
    assert _check(report, "adapter")["status"] == "fail"
    assert _check(report, "ros1_config")["status"] == "fail"


def test_run_doctor_reports_workspace_skills_missing_ros1_remap(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "spray_water.skill.json").write_text(
        json.dumps(
            {
                "name": "spray_water",
                "description": "Spray water at a target.",
                "runtime": "subprocess",
                "command": ["python", "-c", "print('{}')"],
                "input_schema": {
                    "type": "object",
                    "properties": {"target_id": {"type": "string"}},
                    "required": ["target_id"],
                },
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "ros1.yaml"
    config_path.write_text(
        """
robot_id: doctor-real-ros1
remap:
  navigate_to_floor:
    profile: move_base
    name: /move_base
  unknown_policy:
    profile: string_topic
    name: /fireclaw/unknown_policy
""".lstrip(),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="ros1",
        robot_id="ignored",
        memory_path=str(tmp_path / "memory.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=str(skills_dir),
        ros1_config_path=str(config_path),
    )

    ros1_config = _check(report, "ros1_config")
    assert ros1_config["status"] == "warn"
    assert ros1_config["details"]["custom_actions"] == ["unknown_policy"]
    assert ros1_config["details"]["workspace_skill_names"] == ["spray_water"]
    assert ros1_config["details"]["workspace_skills_missing_remap"] == ["spray_water"]
    assert ros1_config["details"]["unknown_remap_actions"] == ["unknown_policy"]


def test_doctor_module_cli_outputs_json_report(tmp_path):
    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.doctor",
            "--adapter",
            "mock-ros1",
            "--robot-id",
            "doctor-cli",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--event-path",
            str(tmp_path / "events.jsonl"),
            "--skills-dir",
            str(tmp_path / "missing-skills"),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    report = json.loads(completed.stdout)
    assert report["status"] == "warn"
    assert _check(report, "adapter")["details"]["robot_id"] == "doctor-cli"
