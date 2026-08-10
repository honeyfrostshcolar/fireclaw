from __future__ import annotations

import time
from threading import Event

from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_run import MissionRunManager


class _Planner:
    def __init__(self) -> None:
        self.report_calls: list[dict] = []

    def generate_final_report(self, **kwargs):
        self.report_calls.append(kwargs)
        return {
            "summary": "机器人已完成搜索。",
            "completed": ["robot-1"],
            "needs_attention": [],
            "next_operator_action": "无",
        }


class _Agent:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.planner = _Planner()
        self.delay = delay
        self.started = Event()
        self.release = Event()
        self.reports: dict[str, dict] = {}
        self.corrections: list[dict] = []
        self.cancel_calls: list[str] = []
        self.mission_registry = None

    def plan_and_submit(self, command: str, **kwargs):
        self.started.set()
        control = kwargs.get("run_control")
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if control is not None and control.is_cancel_requested():
                return {"status": "cancelled", "mission_id": kwargs["session_id"]}
            time.sleep(0.005)
        self.release.wait(timeout=1.0)
        if control is not None and control.is_cancel_requested():
            return {"status": "cancelled", "mission_id": kwargs["session_id"]}
        return {
            "status": "succeeded",
            "mission_id": kwargs["session_id"],
            "subtask_results": [{"robot_id": "robot-1", "status": "completed"}],
        }

    def mission_trace(self, mission_id: str):
        return {
            "mission_id": mission_id,
            "status": "succeeded",
            "subtasks": [{
                "robot_id": "robot-1",
                "task_id": "task-1",
                "status": "completed",
            }],
        }

    def record_final_report(self, mission_id: str, report: dict):
        self.reports[mission_id] = dict(report)

    def record_correction(self, mission_id: str, **kwargs):
        self.corrections.append({"mission_id": mission_id, **kwargs})
        return {"status": "recorded", "mission_id": mission_id}

    def cancel_mission(self, mission_id: str, **kwargs):
        self.cancel_calls.append(mission_id)
        return {"status": "cancel_requested", "mission_id": mission_id}


class _TimedOutAgent(_Agent):
    def plan_and_submit(self, command: str, **kwargs):
        self.started.set()
        return {
            "status": "timed_out",
            "mission_id": kwargs["session_id"],
            "subtask_results": [
                {"robot_id": "robot-1", "status": "timed_out"}
            ],
        }

    def mission_trace(self, mission_id: str):
        return {
            "mission_id": mission_id,
            "status": "failed",
            "subtasks": [{
                "robot_id": "robot-1",
                "task_id": "task-1",
                "status": "timed_out",
                "result": {
                    "status": "timed_out",
                    "cancellation_reason": "deadline_exceeded",
                    "cancellation_acknowledged": True,
                    "runtime_stopped": True,
                },
            }],
        }


def _wait_for(manager: MissionRunManager, mission_id: str, status: str) -> dict:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        result = manager.get(mission_id)
        if result.get("status") == status:
            return result
        time.sleep(0.01)
    raise AssertionError(f"Run did not reach {status}: {manager.get(mission_id)}")


def test_background_run_returns_before_execution_and_generates_report():
    agent = _Agent(delay=0.1)
    manager = MissionRunManager(agent)
    started = time.monotonic()
    accepted = manager.submit("搜索一层", mission_id="m-1")
    elapsed = time.monotonic() - started

    assert elapsed < 0.08
    assert accepted["status"] == "accepted"
    assert accepted["run_id"] == "m-1"
    assert agent.started.wait(timeout=1)

    agent.release.set()
    result = _wait_for(manager, "m-1", "completed")
    assert result["final_report"]["generated_by"] == "llm"
    assert agent.planner.report_calls[0]["mission_status"] == "completed"
    assert agent.reports["m-1"]["summary"] == "机器人已完成搜索。"


def test_pause_resume_and_correction_are_auditable():
    agent = _Agent(delay=0.05)
    manager = MissionRunManager(agent)
    accepted = manager.submit("搜索一层", mission_id="m-2")
    assert accepted["status"] == "accepted"
    assert agent.started.wait(timeout=1)

    paused = manager.pause("m-2")
    assert paused["status"] == "paused"
    corrected = manager.correct("m-2", correction="先检查东侧入口")
    assert corrected["status"] == "recorded"
    assert manager.get("m-2")["correction_count"] == 1

    resumed = manager.resume("m-2")
    assert resumed["status"] == "running"
    agent.release.set()
    assert _wait_for(manager, "m-2", "completed")["correction_count"] == 1
    assert agent.corrections[0]["correction"] == "先检查东侧入口"


def test_cancel_sets_control_and_requests_robot_cancellation():
    agent = _Agent(delay=1.0)
    events: list[str] = []
    manager = MissionRunManager(
        agent,
        event_sink=lambda event_type, _mission_id, _payload: events.append(
            event_type
        ),
    )
    manager.submit("导航到入口", mission_id="m-3")
    assert agent.started.wait(timeout=1)

    cancelled = manager.cancel("m-3")
    assert cancelled["status"] == "cancel_requested"
    assert agent.cancel_calls == ["m-3"]
    assert _wait_for(manager, "m-3", "cancelled")["final_report"]["status"] == "cancelled"
    assert "mission.cancel_requested" in events
    assert "mission.cancelled" in events
    assert "mission.completed" not in events


def test_timeout_emits_canonical_mission_terminal_event():
    agent = _TimedOutAgent()
    events: list[str] = []
    manager = MissionRunManager(
        agent,
        event_sink=lambda event_type, _mission_id, _payload: events.append(
            event_type
        ),
    )

    manager.submit("导航到入口", mission_id="m-timeout")
    result = _wait_for(manager, "m-timeout", "timed_out")

    assert result["result"]["status"] == "timed_out"
    assert result["final_report"]["status"] == "timed_out"
    assert "mission.report_ready" in events
    assert "mission.timed_out" in events
    assert "mission.completed" not in events
    assert "mission.cancelled" not in events


def test_mission_registry_persists_final_report(tmp_path):
    path = tmp_path / "missions.jsonl"
    registry = JsonlMissionRegistry(path)
    registry.create_mission(
        mission_id="m-4",
        session_id="m-4",
        command="搜索一层",
        created_at="2026-07-31T00:00:00+00:00",
    )
    registry.record_final_report(
        mission_id="m-4",
        report={"status": "completed", "summary": "完成"},
        updated_at="2026-07-31T00:01:00+00:00",
    )

    reloaded = JsonlMissionRegistry(path).mission_trace("m-4")
    assert reloaded["final_report"]["summary"] == "完成"
