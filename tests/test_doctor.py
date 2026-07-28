import json
import subprocess
import sys
from pathlib import Path

from fireclaw_core.devtools.doctor import run_doctor


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


def test_run_doctor_ros1_adapter_reports_readiness(tmp_path):
    """ROS1 adapter check should report transport readiness, not 'not implemented'."""
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "doctor-ros1-ready",
                "endpoints": {
                    "navigate_to_floor": {
                        "interface": "action",
                        "name": "/fireclaw/nav",
                        "type": "fireclaw_msgs/NavigateFloorAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    }
                },
                "emergency_stop": {
                    "interface": "service",
                    "name": "/fireclaw/estop",
                    "type": "std_srvs/Trigger",
                },
            }
        ),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="ros1",
        robot_id="doctor-ros1-ready",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=str(tmp_path / "missing-skills"),
        ros1_config_path=str(config_path),
    )

    adapter_check = _check(report, "adapter")
    assert adapter_check["status"] == "pass"
    assert "not implemented" not in adapter_check["message"].lower()
    assert "readiness" in adapter_check["message"].lower() or "ready" in adapter_check["message"].lower()
    details = adapter_check["details"]
    assert details["ros1_config_loaded"] is True
    assert details["emergency_stop_configured"] is True
    assert details["action_feedback_supported"] is True
    assert details["action_cancel_supported"] is True


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
            sys.executable,
            "-m",
            "fireclaw_core.devtools.doctor",
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


# ---------------------------------------------------------------------------
# Doctor --fix mode tests
# ---------------------------------------------------------------------------


def _write_stale_queue(path: Path, task_id: str, status: str, session_id: str = "s1") -> None:
    """Append a task queue record to a JSONL file."""
    record = {
        "task_id": task_id,
        "session_id": session_id,
        "command": f"do {task_id}",
        "status": status,
        "created_at": "2026-06-09T10:00:00Z",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def test_doctor_reports_stale_queue_records(tmp_path):
    """Doctor detects non-terminal task queue records as stale."""
    queue_path = tmp_path / "queue.jsonl"
    _write_stale_queue(queue_path, "task-1", "accepted")
    _write_stale_queue(queue_path, "task-2", "running")
    # Terminal record should NOT be flagged.
    _write_stale_queue(queue_path, "task-3", "completed")

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        task_queue_path=str(queue_path),
    )

    stale_check = _check(report, "stale_task_queue")
    assert stale_check["status"] == "warn"
    assert stale_check["details"]["stale_count"] == 2
    assert stale_check["details"]["stale_task_ids"] == ["task-1", "task-2"]


def test_doctor_fix_marks_stale_lost(tmp_path):
    """fix=True marks non-terminal queue records as lost."""
    queue_path = tmp_path / "queue.jsonl"
    _write_stale_queue(queue_path, "task-10", "accepted")
    _write_stale_queue(queue_path, "task-20", "running")

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        task_queue_path=str(queue_path),
        fix=True,
    )

    stale_check = _check(report, "stale_task_queue")
    assert stale_check["status"] == "pass"
    assert stale_check["details"]["stale_count"] == 0
    assert report["repairs"] != []
    assert report["fixed"] >= 1


def test_doctor_dry_run_does_not_mutate(tmp_path):
    """fix=False (default) does not modify the task queue file."""
    queue_path = tmp_path / "queue.jsonl"
    _write_stale_queue(queue_path, "task-99", "accepted")

    original_content = queue_path.read_text(encoding="utf-8")

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        task_queue_path=str(queue_path),
        fix=False,
    )

    # File must not have changed.
    assert queue_path.read_text(encoding="utf-8") == original_content
    # No repairs reported.
    assert report["repairs"] == []
    assert report["fixed"] == 0


def test_doctor_reports_missing_memory_index(tmp_path):
    """Doctor reports when memory index path does not exist."""
    index_path = tmp_path / "missing" / "index.sqlite"

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_index_path=str(index_path),
    )

    index_check = _check(report, "memory_index")
    assert index_check["status"] == "warn"
    assert "not found" in index_check["message"].lower() or "missing" in index_check["message"].lower()


def test_doctor_reports_invalid_plugin_descriptor(tmp_path):
    """Doctor detects corrupt plugin descriptor JSON."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.plugin.json").write_text("NOT VALID JSON {{", encoding="utf-8")

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        plugin_dir=str(plugin_dir),
    )

    plugin_check = _check(report, "plugin_descriptors")
    assert plugin_check["status"] == "warn"
    assert plugin_check["details"]["invalid_count"] >= 1
    assert "broken.plugin.json" in str(plugin_check["details"]["invalid_files"])


def test_doctor_fix_returns_repair_count(tmp_path):
    """Report includes repair count and list when fix=True."""
    queue_path = tmp_path / "queue.jsonl"
    _write_stale_queue(queue_path, "stale-a", "accepted")
    _write_stale_queue(queue_path, "stale-b", "running")
    _write_stale_queue(queue_path, "done-c", "completed")

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-fix",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        task_queue_path=str(queue_path),
        fix=True,
    )

    assert "repairs" in report
    assert "fixed" in report
    assert isinstance(report["repairs"], list)
    assert isinstance(report["fixed"], int)
    assert report["fixed"] >= 1


# ---------------------------------------------------------------------------
# Doctor memory eval integration tests
# ---------------------------------------------------------------------------


def _create_test_memory_index(path: Path) -> None:
    """Create a minimal SQLite memory index with one record for testing.

    Uses the actual SqliteMemoryIndex schema so the real retriever can read it.
    """
    from fireclaw_core.memory.memory_index import SqliteMemoryIndex

    index = SqliteMemoryIndex(str(path))
    index.upsert({
        "record_id": "r1",
        "mission_id": "m1",
        "record_type": "outcome",
        "robot_id": "bot1",
        "content": {
            "command": "search floor 2",
            "status": "succeeded",
            "_embodied": {
                "runtime_mode": "real",
                "sensitivity": "standard",
            },
        },
        "created_at": "",
    })


def test_doctor_memory_eval_skipped_without_fixture(tmp_path):
    """Memory eval check passes when no fixture path is provided."""
    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "pass"
    assert "skipped" in eval_check["message"].lower()


def test_doctor_memory_eval_skipped_without_index(tmp_path):
    """Memory eval check passes when no index path is provided."""
    fixture_path = tmp_path / "cases.json"
    fixture_path.write_text(
        json.dumps([{"query": "test", "must_match": ["test"]}]),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_eval_fixture=str(fixture_path),
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "pass"
    assert "skipped" in eval_check["message"].lower()


def test_doctor_memory_eval_warns_missing_index(tmp_path):
    """Memory eval warns when index file does not exist."""
    fixture_path = tmp_path / "cases.json"
    fixture_path.write_text(
        json.dumps([{"query": "test", "must_match": ["test"]}]),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_index_path=str(tmp_path / "missing" / "index.sqlite"),
        memory_eval_fixture=str(fixture_path),
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "warn"


def test_doctor_memory_eval_warns_missing_fixture(tmp_path):
    """Memory eval warns when fixture file does not exist."""
    index_path = tmp_path / "index.sqlite"
    _create_test_memory_index(index_path)

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_index_path=str(index_path),
        memory_eval_fixture=str(tmp_path / "missing_cases.json"),
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "warn"


def test_doctor_memory_eval_passes_when_threshold_met(tmp_path):
    """Memory eval passes when hit_rate meets threshold."""
    index_path = tmp_path / "index.sqlite"
    _create_test_memory_index(index_path)
    fixture_path = tmp_path / "cases.json"
    fixture_path.write_text(
        json.dumps([{"query": "search floor 2", "must_match": ["search"]}]),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_index_path=str(index_path),
        memory_eval_fixture=str(fixture_path),
        memory_eval_threshold=0.5,
        memory_eval_mission_id="m1",
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "pass"
    assert eval_check["details"]["meets_threshold"] is True
    assert eval_check["details"]["hit_rate"] == 1.0


def test_doctor_memory_eval_warns_when_threshold_not_met(tmp_path):
    """Memory eval warns when hit_rate is below threshold."""
    index_path = tmp_path / "index.sqlite"
    _create_test_memory_index(index_path)
    fixture_path = tmp_path / "cases.json"
    # Query for something not in the index
    fixture_path.write_text(
        json.dumps([{"query": "nonexistent topic", "must_match": ["nonexistent"]}]),
        encoding="utf-8",
    )

    report = run_doctor(
        adapter="dry-run",
        robot_id="doctor-eval",
        memory_path=str(tmp_path / "mem.jsonl"),
        event_path=str(tmp_path / "events.jsonl"),
        skills_dir=None,
        memory_index_path=str(index_path),
        memory_eval_fixture=str(fixture_path),
        memory_eval_threshold=0.5,
    )

    eval_check = _check(report, "memory_eval")
    assert eval_check["status"] == "warn"
    assert eval_check["details"]["meets_threshold"] is False
    assert eval_check["details"]["hit_rate"] == 0.0
