import json
import subprocess
import sys
import time
from pathlib import Path

from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.mission_memory import MissionMemoryRecord, MissionMemoryStore


def _write_slow_policy_skill(skills_dir: Path) -> None:
    skills_dir.mkdir()
    (skills_dir / "slow_policy.py").write_text(
        "import json, time\n"
        "time.sleep(10)\n"
        "print(json.dumps({'ok': True, 'data': {'policy': 'slow'}}))\n",
        encoding="utf-8",
    )
    (skills_dir / "slow_policy.skill.json").write_text(
        json.dumps(
            {
                "name": "slow_policy",
                "description": "Slow policy skill used to test mission cancellation.",
                "runtime": "subprocess",
                "command": [sys.executable, "slow_policy.py"],
                "timeout_seconds": 15,
                "dry_run_only": True,
                "risk_level": "low",
                "input_schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
            }
        ),
        encoding="utf-8",
    )


def _wait_for_cli_trace(mission_registry_path, robot_registry_path, mission_id):
    deadline = time.time() + 3
    while time.time() < deadline:
        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "trace",
                mission_id,
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=False,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
        if result.get("status") == "succeeded":
            return result
        time.sleep(0.01)
    raise AssertionError(f"Mission {mission_id} did not finish before timeout.")


def test_mission_cli_submit_subtask_records_mission(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps(
                {
                    "robots": [
                        {
                            "robot_id": "robot-1",
                            "base_url": gateway.base_url,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--session-id",
                "mission-cli-1",
                "--dedupe-key",
                "mission-cli-1-robot-1-floor-2",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "accepted"
    assert result["mission_id"] == "mission-cli-1"
    assert result["robot_id"] == "robot-1"
    assert result["task_id"].startswith("task-")
    assert mission_registry_path.exists()


def test_mission_cli_trace_aggregates_robot_subagent_trace(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--session-id",
                "mission-cli-2",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        trace = _wait_for_cli_trace(mission_registry_path, robot_registry_path, "mission-cli-2")
    finally:
        gateway.stop()

    assert trace["mission_id"] == "mission-cli-2"
    assert trace["status"] == "succeeded"
    assert trace["subtasks"][0]["robot_id"] == "robot-1"
    assert trace["subtasks"][0]["robot_trace"]["result"]["status"] == "succeeded"


def test_mission_cli_cancel_requests_robot_subagent_cancellation(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_slow_policy_skill(skills_dir)
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=str(skills_dir),
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人 使用 slow_policy",
                "--session-id",
                "mission-cli-cancel",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "cancel",
                "mission-cli-cancel",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "cancel_requested"
    assert result["cancelled_subtask_count"] == 1
    assert result["subtasks"][0]["robot_id"] == "robot-1"
    assert result["subtasks"][0]["status"] == "cancel_requested"


def test_mission_cli_plan_mission_submits_subtasks(tmp_path):
    gateway1 = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot1-memory.jsonl"),
            event_path=str(tmp_path / "robot1-events.jsonl"),
            task_queue_path=str(tmp_path / "robot1-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway2 = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-2",
            memory_path=str(tmp_path / "robot2-memory.jsonl"),
            event_path=str(tmp_path / "robot2-events.jsonl"),
            task_queue_path=str(tmp_path / "robot2-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway1.start()
    gateway2.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps(
                {
                    "robots": [
                        {"robot_id": "robot-1", "base_url": gateway1.base_url, "capabilities": ["search_for_victims"]},
                        {"robot_id": "robot-2", "base_url": gateway2.base_url, "capabilities": ["search_for_victims"]},
                    ]
                }
            ),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "plan-mission",
                "--command",
                "去二楼和三楼搜索受困人员",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
                "--no-use-scheduler",
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway1.stop()
        gateway2.stop()

    assert result["status"] == "planned"
    assert result["intent"] == "search"
    assert len(result["subtask_results"]) == 2
    robot_ids = {r["robot_id"] for r in result["subtask_results"]}
    assert robot_ids == {"robot-1", "robot-2"}


def test_mission_cli_rejects_submit_without_mission_scope(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼救人",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
                "--operator-id",
                "test-observer",
                "--role",
                "observer",
            ],
            check=False,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "denied"
    assert "mission.submit" in result["message"]


def _run_memory_cli(tmp_path, *extra_args):
    memory_path = tmp_path / "mission_memory.jsonl"
    argv = [
        ".venv/bin/python",
        "-m",
        "fireclaw_core.mission_cli",
        "memory",
        "--memory-path",
        str(memory_path),
        *extra_args,
    ]
    return subprocess.run(argv, check=False, cwd=".", text=True, capture_output=True)


def test_mission_cli_memory_list(tmp_path):
    memory_path = tmp_path / "mission_memory.jsonl"
    store = MissionMemoryStore(memory_path)
    store.append(MissionMemoryRecord(
        record_id="mem-1", mission_id="m-1", record_type="outcome",
        content={"status": "succeeded"}, created_at="2026-06-08T12:00:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-2", mission_id="m-1", record_type="observation",
        content={"note": "smoke detected"}, created_at="2026-06-08T12:01:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-3", mission_id="m-2", record_type="outcome",
        content={"status": "failed"}, created_at="2026-06-08T12:02:00Z",
    ))

    # List all records for m-1
    completed = _run_memory_cli(
        tmp_path, "list", "--mission-id", "m-1",
    )
    assert completed.returncode == 0
    records = json.loads(completed.stdout)
    assert len(records) == 2
    assert records[0]["record_id"] == "mem-1"
    assert records[1]["record_id"] == "mem-2"

    # List filtered by type
    completed = _run_memory_cli(
        tmp_path, "list", "--mission-id", "m-1", "--type", "outcome",
    )
    assert completed.returncode == 0
    records = json.loads(completed.stdout)
    assert len(records) == 1
    assert records[0]["record_type"] == "outcome"

    # List with limit
    completed = _run_memory_cli(
        tmp_path, "list", "--mission-id", "m-1", "--limit", "1",
    )
    assert completed.returncode == 0
    records = json.loads(completed.stdout)
    assert len(records) == 1


def test_mission_cli_memory_add(tmp_path):
    completed = _run_memory_cli(
        tmp_path,
        "add",
        "--mission-id", "m-1",
        "--type", "outcome",
        "--content", '{"status": "succeeded", "duration_seconds": 120}',
    )
    assert completed.returncode == 0
    record = json.loads(completed.stdout)
    assert record["mission_id"] == "m-1"
    assert record["record_type"] == "outcome"
    assert record["content"]["status"] == "succeeded"
    assert record["content"]["duration_seconds"] == 120
    assert record["record_id"].startswith("mem-")

    # Verify it was actually written to the store
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    records = store.list_records()
    assert len(records) == 1
    assert records[0].mission_id == "m-1"


def test_mission_cli_memory_add_with_robot_and_subtask(tmp_path):
    completed = _run_memory_cli(
        tmp_path,
        "add",
        "--mission-id", "m-1",
        "--type", "observation",
        "--content", '{"note": "victim found"}',
        "--robot-id", "robot-1",
        "--subtask-id", "subtask-1",
    )
    assert completed.returncode == 0
    record = json.loads(completed.stdout)
    assert record["robot_id"] == "robot-1"
    assert record["subtask_id"] == "subtask-1"

    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    records = store.list_records()
    assert len(records) == 1
    assert records[0].robot_id == "robot-1"
    assert records[0].subtask_id == "subtask-1"


def test_mission_cli_memory_add_rejects_invalid_type(tmp_path):
    completed = _run_memory_cli(
        tmp_path,
        "add",
        "--mission-id", "m-1",
        "--type", "invalid_type",
        "--content", '{"key": "value"}',
    )
    assert completed.returncode != 0
    assert "Invalid record type" in completed.stderr


def test_mission_cli_memory_summary(tmp_path):
    memory_path = tmp_path / "mission_memory.jsonl"
    store = MissionMemoryStore(memory_path)
    store.append(MissionMemoryRecord(
        record_id="mem-1", mission_id="m-1", record_type="outcome",
        content={"status": "succeeded"}, created_at="2026-06-08T12:00:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-2", mission_id="m-1", record_type="observation",
        content={"note": "smoke"}, created_at="2026-06-08T12:01:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-3", mission_id="m-1", record_type="lesson",
        content={"lesson": "always check exits"}, created_at="2026-06-08T12:02:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-4", mission_id="m-2", record_type="outcome",
        content={"status": "failed"}, created_at="2026-06-08T12:03:00Z",
    ))

    # Summary for specific mission
    completed = _run_memory_cli(tmp_path, "summary", "--mission-id", "m-1")
    assert completed.returncode == 0
    summary = json.loads(completed.stdout)
    assert summary["total"] == 3
    assert summary["by_type"]["outcome"] == 1
    assert summary["by_type"]["observation"] == 1
    assert summary["by_type"]["lesson"] == 1

    # Summary for all missions
    completed = _run_memory_cli(tmp_path, "summary")
    assert completed.returncode == 0
    summary = json.loads(completed.stdout)
    assert summary["total"] == 4
    assert summary["by_type"]["outcome"] == 2


def test_mission_cli_corrections(tmp_path):
    memory_path = tmp_path / "mission_memory.jsonl"
    store = MissionMemoryStore(memory_path)
    store.append(MissionMemoryRecord(
        record_id="mem-1", mission_id="m-1", record_type="correction",
        content={"correction": "应先搜索三楼", "operator_id": "op-1"},
        created_at="2026-06-08T12:00:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-2", mission_id="m-1", record_type="correction",
        content={"correction": "注意烟雾方向", "context": "风向变化", "operator_id": "op-1"},
        created_at="2026-06-08T12:01:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-3", mission_id="m-1", record_type="outcome",
        content={"status": "succeeded"}, created_at="2026-06-08T12:02:00Z",
    ))
    store.append(MissionMemoryRecord(
        record_id="mem-4", mission_id="m-2", record_type="correction",
        content={"correction": "其他任务的纠正"}, created_at="2026-06-08T12:03:00Z",
    ))

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.mission_cli",
            "corrections",
            "m-1",
            "--memory-path",
            str(memory_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["mission_id"] == "m-1"
    assert len(result["corrections"]) == 2
    # search() returns most recent first
    assert result["corrections"][0]["content"]["correction"] == "注意烟雾方向"
    assert result["corrections"][0]["content"]["context"] == "风向变化"
    assert result["corrections"][1]["content"]["correction"] == "应先搜索三楼"


def test_mission_cli_events(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        # Submit a mission first so the mission exists
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼搜索",
                "--session-id",
                "mission-events-test",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "events",
                "mission-events-test",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["mission_id"] == "mission-events-test"
    assert "event_count" in result
    assert "events" in result
    assert isinstance(result["events"], list)


def _run_approval_cli(tmp_path, *extra_args):
    approval_path = tmp_path / "mission_approvals.jsonl"
    argv = [
        ".venv/bin/python",
        "-m",
        "fireclaw_core.mission_cli",
        "approval",
        "--approval-path",
        str(approval_path),
        *extra_args,
    ]
    return subprocess.run(argv, check=False, cwd=".", text=True, capture_output=True)


def test_mission_cli_approval_list(tmp_path):
    approval_path = tmp_path / "mission_approvals.jsonl"
    store = JsonlApprovalStore(approval_path)
    store.create(
        mission_id="m-1", action="navigate", risk_level="high",
        command="去三楼", requested_by="op-1", created_at="2026-06-08T12:00:00Z",
    )
    store.create(
        mission_id="m-1", action="spray", risk_level="critical",
        command="喷水", requested_by="op-1", created_at="2026-06-08T12:01:00Z",
    )
    store.create(
        mission_id="m-2", action="navigate", risk_level="low",
        command="去一楼", requested_by="op-2", created_at="2026-06-08T12:02:00Z",
    )

    # List all
    completed = _run_approval_cli(tmp_path, "list")
    assert completed.returncode == 0
    requests = json.loads(completed.stdout)
    assert len(requests) == 3

    # List filtered by mission-id
    completed = _run_approval_cli(tmp_path, "list", "--mission-id", "m-1")
    assert completed.returncode == 0
    requests = json.loads(completed.stdout)
    assert len(requests) == 2
    assert all(r["mission_id"] == "m-1" for r in requests)

    # List filtered by status
    completed = _run_approval_cli(tmp_path, "list", "--status", "pending")
    assert completed.returncode == 0
    requests = json.loads(completed.stdout)
    assert len(requests) == 3
    assert all(r["status"] == "pending" for r in requests)


def test_mission_cli_approval_request(tmp_path):
    completed = _run_approval_cli(
        tmp_path,
        "request",
        "--mission-id", "m-1",
        "--action", "navigate",
        "--risk-level", "high",
        "--command", "去三楼搜索",
    )
    assert completed.returncode == 0
    request = json.loads(completed.stdout)
    assert request["mission_id"] == "m-1"
    assert request["action"] == "navigate"
    assert request["risk_level"] == "high"
    assert request["command"] == "去三楼搜索"
    assert request["status"] == "pending"
    assert request["request_id"]

    # Verify it was actually written to the store
    store = JsonlApprovalStore(tmp_path / "mission_approvals.jsonl")
    records = store.list_requests()
    assert len(records) == 1
    assert records[0].mission_id == "m-1"


def test_mission_cli_approval_decide_approve(tmp_path):
    approval_path = tmp_path / "mission_approvals.jsonl"
    store = JsonlApprovalStore(approval_path)
    req = store.create(
        mission_id="m-1", action="navigate", risk_level="high",
        command="去三楼", requested_by="op-1", created_at="2026-06-08T12:00:00Z",
    )

    completed = _run_approval_cli(
        tmp_path,
        "decide",
        req.request_id,
        "--decision", "approve",
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == "approved"
    assert result["request_id"] == req.request_id

    # Verify in store
    updated = store.get(req.request_id)
    assert updated is not None
    assert updated.status == "approved"


def test_mission_cli_approval_decide_deny(tmp_path):
    approval_path = tmp_path / "mission_approvals.jsonl"
    store = JsonlApprovalStore(approval_path)
    req = store.create(
        mission_id="m-1", action="spray", risk_level="critical",
        command="喷水", requested_by="op-1", created_at="2026-06-08T12:00:00Z",
    )

    completed = _run_approval_cli(
        tmp_path,
        "decide",
        req.request_id,
        "--decision", "deny",
        "--reason", "区域未确认安全",
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert result["status"] == "denied"
    assert result["request_id"] == req.request_id
    assert result["reason"] == "区域未确认安全"

    # Verify in store
    updated = store.get(req.request_id)
    assert updated is not None
    assert updated.status == "denied"
    assert updated.reason == "区域未确认安全"


def test_mission_cli_replay(tmp_path):
    gateway = FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="simulator",
            robot_id="robot-1",
            memory_path=str(tmp_path / "robot-memory.jsonl"),
            event_path=str(tmp_path / "robot-events.jsonl"),
            task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
            workspace_skills_dir=None,
        )
    )
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        memory_path = tmp_path / "mission_memory.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )

        # Submit a mission first
        subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                "去二楼搜索",
                "--session-id",
                "mission-replay-test",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )

        completed = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "fireclaw_core.mission_cli",
                "replay",
                "mission-replay-test",
                "--robot-registry",
                str(robot_registry_path),
                "--mission-registry",
                str(mission_registry_path),
                "--memory-path",
                str(memory_path),
            ],
            check=True,
            cwd=".",
            text=True,
            capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["mission_id"] == "mission-replay-test"
    assert "timeline" in result
    assert "summary" in result
    assert isinstance(result["timeline"], list)
    assert result["summary"]["subtask_count"] == 1


def test_mission_cli_replay_not_found(tmp_path):
    robot_registry_path = tmp_path / "robots.json"
    mission_registry_path = tmp_path / "missions.jsonl"
    memory_path = tmp_path / "mission_memory.jsonl"
    robot_registry_path.write_text(
        json.dumps({"robots": []}),
        encoding="utf-8",
    )
    # Create empty mission registry
    mission_registry_path.touch()

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.mission_cli",
            "replay",
            "nonexistent-mission",
            "--robot-registry",
            str(robot_registry_path),
            "--mission-registry",
            str(mission_registry_path),
            "--memory-path",
            str(memory_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    result = json.loads(completed.stdout)

    assert result["mission_id"] == "nonexistent-mission"
    assert result["status"] == "not_found"
    assert result["timeline"] == []


def test_lifecycle_check_cli_outputs_report(tmp_path: Path):
    from fireclaw_core.task_registry import JsonlTaskRegistryStore
    from fireclaw_core.subagent_registry import JsonlSubagentRegistry

    task_path = tmp_path / "tasks.jsonl"
    subagent_path = tmp_path / "subagents.jsonl"
    # Create empty stores so the files exist
    JsonlTaskRegistryStore(task_path)
    JsonlSubagentRegistry(subagent_path)

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.mission_cli",
            "lifecycle-check",
            "--task-registry",
            str(task_path),
            "--subagent-registry",
            str(subagent_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    body = json.loads(completed.stdout)
    assert body["status"] in {"ok", "warn"}
    assert "stale_tasks" in body
    assert "orphaned_subagents" in body
    assert "checked_at" in body


def test_mission_cli_plan_mission_with_llm_flag():
    """Verify --planner llm creates an LLMMissionPlanner via _build_planner."""
    import argparse
    from fireclaw_core.llm_planner import LLMMissionPlanner
    from fireclaw_core.mission_planner import MissionPlanner
    from fireclaw_core.mission_cli import _build_planner

    # Test LLM planner creation
    args = argparse.Namespace(
        planner="llm",
        provider_base_url="http://localhost:8080/v1",
        provider_api_key="test-key-123",
        model="gpt-4o",
        llm_trace_path=None,
        catalog=None,
    )
    planner = _build_planner(args)
    assert isinstance(planner, LLMMissionPlanner)

    # Test deterministic planner (default)
    args_det = argparse.Namespace(
        planner="deterministic",
        provider_base_url=None,
        provider_api_key=None,
        model=None,
        llm_trace_path=None,
        catalog=None,
    )
    planner_det = _build_planner(args_det)
    assert isinstance(planner_det, MissionPlanner)
    assert not isinstance(planner_det, LLMMissionPlanner)

    # Test LLM planner with trace store
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        trace_path = f.name
    args_trace = argparse.Namespace(
        planner="llm",
        provider_base_url="http://localhost:8080/v1",
        provider_api_key="test-key-123",
        model="gpt-4o",
        llm_trace_path=trace_path,
        catalog=None,
    )
    planner_trace = _build_planner(args_trace)
    assert isinstance(planner_trace, LLMMissionPlanner)
    assert planner_trace._trace_store is not None


def test_mission_cli_plan_mission_llm_missing_required_flags(tmp_path):
    """Verify --planner llm without required provider flags exits with error."""
    robot_registry_path = tmp_path / "robots.json"
    mission_registry_path = tmp_path / "missions.jsonl"
    robot_registry_path.write_text(
        json.dumps({"robots": [{"robot_id": "robot-1", "base_url": "http://fake:9999"}]}),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.mission_cli",
            "plan-mission",
            "--command",
            "去二楼搜索",
            "--planner",
            "llm",
            "--robot-registry",
            str(robot_registry_path),
            "--mission-registry",
            str(mission_registry_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode != 0
    assert "required when --planner=llm" in completed.stderr


def test_mission_cli_plan_mission_deterministic_default(tmp_path):
    """Verify --planner defaults to deterministic (existing behavior)."""
    robot_registry_path = tmp_path / "robots.json"
    mission_registry_path = tmp_path / "missions.jsonl"
    robot_registry_path.write_text(
        json.dumps({"robots": [{"robot_id": "robot-1", "base_url": "http://fake:9999"}]}),
        encoding="utf-8",
    )

    # No --planner flag, should default to deterministic
    completed = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "fireclaw_core.mission_cli",
            "plan-mission",
            "--command",
            "去二楼搜索",
            "--robot-registry",
            str(robot_registry_path),
            "--mission-registry",
            str(mission_registry_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    # Will fail at runtime since robot-1 isn't reachable, but should not fail at CLI parsing
    # The key is it doesn't error about --planner flags
    assert "required when --planner=llm" not in completed.stderr
