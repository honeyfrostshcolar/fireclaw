import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore


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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                    "去坐标 (2.0, 1.5) 救人",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                    "去坐标 (2.0, 1.5) 救人",
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


def test_mission_cli_cancel_requests_robot_subagent_cancellation(
    tmp_path,
    legacy_skill_profile,
    legacy_skill_executor,
):
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
            deployment_profile=legacy_skill_profile,
        ),
        workspace_skill_executor=legacy_skill_executor,
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
                "submit-subtask",
                "--robot",
                "robot-1",
                "--command",
                    "去坐标 (2.0, 1.5) 救人 使用 slow_policy",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
        sys.executable,
        "-m",
        "fireclaw_core.mission.mission_cli",
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
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
        sys.executable,
        "-m",
        "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
                sys.executable,
                "-m",
                "fireclaw_core.mission.mission_cli",
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
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
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
    from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
    from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry

    task_path = tmp_path / "tasks.jsonl"
    subagent_path = tmp_path / "subagents.jsonl"
    # Create empty stores so the files exist
    JsonlTaskRegistryStore(task_path)
    JsonlSubagentRegistry(subagent_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
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
    from fireclaw_core.planner.llm_planner import LLMMissionPlanner
    from fireclaw_core.mission.mission_planner import MissionPlanner
    from fireclaw_core.mission.mission_cli import _build_planner

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
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
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
    assert "provider_base_url is required" in completed.stderr


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
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
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


def test_serve_subcommand_help():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
            "serve",
            "--help",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    assert "--data-dir" in completed.stdout
    assert "--planner" in completed.stdout
    assert "--host" in completed.stdout
    assert "--port" in completed.stdout
    assert "--adapter" in completed.stdout


def test_mission_subcommand_help():
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core.mission.mission_cli",
            "mission",
            "--help",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    assert "--server" in completed.stdout
    assert "--timeout" in completed.stdout


def test_main_module_routes_to_mission_cli():
    """Verify `python -m fireclaw_core` dispatches to mission_cli.main()."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "--help",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, f"stderr: {completed.stderr}"
    # The mission_cli parser description should appear
    assert "mission-control" in completed.stdout.lower() or "mission" in completed.stdout.lower()


def test_mission_cli_robot_profile_export_writes_robot_registry(tmp_path):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "robots.json"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "robot-profile",
            "export",
            "--profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    body = json.loads(completed.stdout)
    registry = json.loads(output_path.read_text(encoding="utf-8"))
    assert body["status"] == "written"
    assert registry["robots"][0]["robot_id"] == "debug-robot-1"
    assert registry["robots"][0]["capabilities"] == ["search_for_victims"]


def test_build_mission_runtime_paths_accepts_robot_profiles(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import _build_mission_runtime_paths

    args = Namespace(
        robot_registry=None,
        robot_profile=[str(tmp_path / "robot.toml")],
        mission_registry=str(tmp_path / "missions.jsonl"),
        memory_path=None,
        memory_index=None,
        task_registry=None,
        subagent_registry=None,
        session_lineage=None,
        task_flow=None,
        approval_path=None,
    )

    paths = _build_mission_runtime_paths(args)

    assert paths.robot_profiles == (tmp_path / "robot.toml",)
    assert paths.robot_registry == Path("robots.json")


def test_build_mission_runtime_paths_requires_registry_without_profiles(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import _build_mission_runtime_paths

    args = Namespace(
        robot_registry=None,
        robot_profile=None,
        mission_registry=str(tmp_path / "missions.jsonl"),
        memory_path=None,
        memory_index=None,
        task_registry=None,
        subagent_registry=None,
        session_lineage=None,
        task_flow=None,
        approval_path=None,
    )

    with pytest.raises(SystemExit, match="--robot-registry is required"):
        _build_mission_runtime_paths(args)


def test_build_mission_runtime_paths_includes_mission_planning_audit_path(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import _build_mission_runtime_paths

    robot_registry = tmp_path / "robots.json"
    mission_registry = tmp_path / "missions.jsonl"
    audit_path = tmp_path / "mission-planning-audit.jsonl"
    args = Namespace(
        robot_profile=None,
        robot_registry=str(robot_registry),
        mission_registry=str(mission_registry),
        memory_path=None,
        memory_index=None,
        task_registry=None,
        subagent_registry=None,
        session_lineage=None,
        task_flow=None,
        approval_path=None,
        mission_planning_audit_path=str(audit_path),
    )

    paths = _build_mission_runtime_paths(args)

    assert paths.mission_planning_audit == audit_path


def test_plan_mission_help_exposes_robot_profile_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "plan-mission", "--help"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "--robot-profile" in completed.stdout
    assert "--robot-registry" in completed.stdout


def test_serve_help_exposes_robot_profile_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "--robot-profile" in completed.stdout


def test_build_external_knowledge_rag_config_uses_separate_namespace(tmp_path):
    from argparse import Namespace
    from fireclaw_core.mission.mission_cli import (
        _build_external_knowledge_rag_config,
    )

    config = _build_external_knowledge_rag_config(Namespace(
        knowledge_rag_backend="hybrid",
        knowledge_rag_bm25_index_dir=str(tmp_path / "bm25"),
        knowledge_rag_dense_index_dir=str(tmp_path / "dense"),
        knowledge_rag_generation_root=None,
        knowledge_rag_embedding_provider="fake",
        knowledge_rag_embedding_model_path=None,
        knowledge_rag_reranker_provider=None,
        knowledge_rag_reranker_model_path=None,
        knowledge_rag_device="cpu",
        knowledge_rag_candidate_multiplier=4,
        knowledge_rag_rrf_k=40,
    ))

    assert config is not None
    assert config.source_kind == "external_knowledge"
    assert config.backend == "hybrid"
    assert config.bm25_index_dir == tmp_path / "bm25"
    assert config.dense_index_dir == tmp_path / "dense"
    assert config.candidate_multiplier == 4
    assert config.rrf_k == 40


def test_serve_help_exposes_external_knowledge_rag_flags():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "--knowledge-rag-backend" in completed.stdout
    assert "--knowledge-rag-bm25-index-dir" in completed.stdout
    assert "--knowledge-rag-embedding-model-path" in completed.stdout


def test_robot_profile_discover_writes_suggested_rules(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "discovered.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
    )

    exit_code = main()

    assert exit_code == 0
    text = output_path.read_text(encoding="utf-8")
    assert "[[robot.sensor_discovery.rules]]" in text
    assert 'sensor = "lidar"' in text
    assert 'topic_pattern = "/scan"' in text
    assert "confirmed = false" in text
    assert "confirmed_by" not in text
    assert "confirmed_at" not in text


def test_robot_profile_discover_write_profile_appends_rules(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--output",
            str(tmp_path / "unused.toml"),
            "--write-profile",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    text = profile_path.read_text(encoding="utf-8")
    assert "[[robot.sensor_discovery.rules]]" in text
    assert 'sensor = "lidar"' in text
    assert "confirmed = false" in text
    assert "confirmed_by" not in text


def test_robot_profile_discover_write_profile_keeps_existing_discovery_table_parseable(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--write-profile",
        ],
    )

    from fireclaw_core.mission.mission_cli import main

    assert main() == 0

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile
    profile = load_robot_capability_profile(profile_path)
    assert profile.sensor_discovery.rules[0].sensor == "lidar"


def test_robot_profile_discover_outputs_runtime_fingerprint(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    output_path = tmp_path / "discovered.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 0

    text = output_path.read_text(encoding="utf-8")
    assert "[robot.discovery_fingerprint]" in text
    assert 'source = "ros1"' in text
    assert 'topics_hash = "sha256:' in text
    assert 'confirmed_by = "robot-profile discover"' not in text
    assert "confirmed_at" not in text


def test_robot_profile_discover_write_profile_replaces_existing_fingerprint_table(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:old"
confirmed_by = "operator"

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--write-profile",
        ],
    )

    from fireclaw_core.mission.mission_cli import main

    assert main() == 0

    text = profile_path.read_text(encoding="utf-8")
    assert text.count("[robot.discovery_fingerprint]") == 1
    assert "sha256:old" not in text

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(profile_path)
    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.topics_hash.startswith("sha256:")
    assert profile.sensor_discovery.rules[0].sensor == "lidar"


def test_robot_profile_diff_discovery_reports_added_candidate(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "diff-discovery",
            "--profile",
            str(profile_path),
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "changed"
    assert payload["added"] == ["/scan"]


def test_robot_profile_diff_discovery_reports_stale_fingerprint(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:old"
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"

[[robot.sensor_discovery.rules]]
topic_pattern = "/scan"
message_type = "sensor_msgs/LaserScan"
sensor = "lidar"
confirmed = true
confirmed_by = "operator-1"
confirmed_at = "2026-06-15T12:00:00+08:00"
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "diff-discovery",
            "--profile",
            str(profile_path),
        ],
    )

    assert main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale_confirmation"] is True
    assert payload["fingerprint_status"] == "stale"


def test_robot_profile_confirm_discovery_writes_confirmed_rules_and_audit_metadata(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery
    from fireclaw_core.sensors.health import SensorObservation

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "observe",
        lambda self, topic, sensor, timeout_seconds: SensorObservation(
            observed=True, age_seconds=0.0,
            payload_size=64, frame_id="laser_frame",
            finite_range_count=360,
        ),
    )

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "confirm-discovery",
            "--profile",
            str(profile_path),
            "--confirmed-by",
            "operator-1",
            "--confirmed-at",
            "2026-06-15T12:00:00+08:00",
        ],
    )

    assert main() == 0

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(profile_path)
    assert profile.discovery_fingerprint is not None
    assert profile.discovery_fingerprint.confirmed_by == "operator-1"
    assert profile.discovery_fingerprint.confirmed_at == "2026-06-15T12:00:00+08:00"
    assert profile.sensor_discovery.rules[0].sensor == "lidar"
    assert profile.sensor_discovery.rules[0].confirmed is True
    assert profile.sensor_discovery.rules[0].confirmed_by == "operator-1"
    assert profile.sensor_discovery.rules[0].confirmed_at == "2026-06-15T12:00:00+08:00"


def test_robot_profile_confirm_discovery_refuses_when_runtime_fingerprint_unavailable(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    def fail_topic_types(self):
        raise RuntimeError("roscore not reachable")

    monkeypatch.setattr(ros1_sensor_discovery.Ros1CliGraphProvider, "topic_types", fail_topic_types)

    from fireclaw_core.mission.mission_cli import main

    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "confirm-discovery",
            "--profile",
            str(profile_path),
            "--confirmed-by",
            "operator-1",
        ],
    )

    assert main() == 1
    assert "runtime fingerprint unavailable" in capsys.readouterr().err
