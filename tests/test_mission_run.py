from __future__ import annotations

import time
from threading import Event

from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_run import MissionRunManager


class _Planner:
    def __init__(self, *, report_delay: float = 0.0) -> None:
        self.report_calls: list[dict] = []
        self.report_delay = report_delay
        self.report_started = Event()

    def generate_final_report(self, **kwargs):
        self.report_started.set()
        if self.report_delay > 0:
            time.sleep(self.report_delay)
        self.report_calls.append(kwargs)
        return {
            "summary": "机器人已完成搜索。",
            "completed": ["robot-1"],
            "needs_attention": [],
            "next_operator_action": "无",
        }


class _Agent:
    def __init__(self, *, delay: float = 0.0, report_delay: float = 0.0) -> None:
        self.planner = _Planner(report_delay=report_delay)
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


def test_terminal_status_is_published_before_slow_final_report():
    agent = _Agent(report_delay=0.25)
    events: list[str] = []
    manager = MissionRunManager(
        agent,
        event_sink=lambda event_type, _mission_id, _payload: events.append(
            event_type
        ),
    )
    manager.submit("搜索一层", mission_id="m-report-async")
    assert agent.started.wait(timeout=1)
    agent.release.set()

    terminal = _wait_for(manager, "m-report-async", "completed")
    assert terminal["report_pending"] is True
    assert terminal["report_status"] == "pending"
    assert "final_report" not in terminal
    assert agent.planner.report_started.wait(timeout=1)
    assert events.index("mission.report_generation_started") < events.index(
        "mission.completed"
    )
    assert "mission.report_ready" not in events
    assert manager.report("m-report-async")["status"] == "pending"

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        report = manager.report("m-report-async")
        if report.get("generated_by") == "llm":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("asynchronous Mission report did not complete")

    ready = manager.get("m-report-async")
    assert ready["report_pending"] is False
    assert ready["final_report"]["generated_by"] == "llm"
    assert "mission.report_ready" in events


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


def test_mission_run_relays_progress_events():
    class _ProgressAgent:
        def __init__(self) -> None:
            self.planner = _Planner()
            self.mission_registry = None

        def plan_and_submit(self, command: str, **kwargs):
            control = kwargs.get("run_control")
            if control is not None:
                control.emit_event("action.feedback", {"robot_id": "robot-1", "message": "迭代 2: 清理代价地图"})
            return {
                "status": "succeeded",
                "mission_id": kwargs["session_id"],
                "subtask_results": [{"robot_id": "robot-1", "status": "completed"}],
            }

        def mission_trace(self, mission_id: str):
            return {
                "mission_id": mission_id,
                "status": "succeeded",
                "subtasks": [{"robot_id": "robot-1", "task_id": "t-1", "status": "completed"}],
            }

    received: list[tuple[str, str, dict]] = []
    agent = _ProgressAgent()
    manager = MissionRunManager(
        agent,
        event_sink=lambda event_type, mission_id, payload: received.append(
            (event_type, mission_id, payload)
        ),
    )

    manager.submit("导航任务", mission_id="m-progress")
    _wait_for(manager, "m-progress", "completed")

    feedback_events = [p for et, mid, p in received if et == "action.feedback"]
    assert len(feedback_events) == 1
    assert feedback_events[0]["message"] == "迭代 2: 清理代价地图"
    assert feedback_events[0]["robot_id"] == "robot-1"


def test_preplanned_run_emits_plan_loaded_before_execution_without_posthoc_dispatch():
    class _SealedAgent(_Agent):
        def execute_sealed_plan(self, plan, **kwargs):
            return {
                "status": "succeeded",
                "mission_id": kwargs["session_id"],
                "plan": plan.to_dict(),
                "subtask_results": [{
                    "robot_id": "robot-1",
                    "task_id": "task-1",
                    "status": "accepted",
                    "command": "前往巡检点",
                }],
            }

    plan = MissionPlan(
        intent="patrol",
        command="前往巡检点",
        subtasks=[MissionSubtask(
            robot_id="robot-1",
            command="前往巡检点",
            floor=None,
            capability_required="patrol",
            execution_group=0,
        )],
    )
    received: list[tuple[str, dict]] = []
    manager = MissionRunManager(
        _SealedAgent(),
        event_sink=lambda event_type, _mission_id, payload: received.append(
            (event_type, dict(payload))
        ),
    )

    manager.submit_preplanned(
        plan,
        mission_id="m-sealed-order",
        operator={"operator_id": "operator-1"},
        artifact_id="artifact-1",
        plan_digest="sha256:test",
    )
    _wait_for(manager, "m-sealed-order", "completed")

    event_types = [event_type for event_type, _ in received]
    assert event_types.index("mission.sealed_plan_loaded") < event_types.index(
        "mission.sealed_plan_execution_started"
    )
    assert "mission.subtask_dispatched" not in event_types
    loaded_payload = next(
        payload
        for event_type, payload in received
        if event_type == "mission.sealed_plan_loaded"
    )
    assert loaded_payload["plan"]["command"] == "前往巡检点"
    assert loaded_payload["plan_source"] == "sealed_plan_artifact"
