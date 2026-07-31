from fireclaw_core.task.task_state import project_task_state


def _event(event_type, payload=None, *, task_id="task-1", session_id="session-1", timestamp="2026-06-03T00:00:00+00:00"):
    return {
        "event_id": f"evt-{event_type}",
        "task_id": task_id,
        "session_id": session_id,
        "type": event_type,
        "timestamp": timestamp,
        "payload": payload or {},
    }


def test_project_task_state_returns_unknown_for_empty_events():
    state = project_task_state([])

    assert state["task"]["status"] == "unknown"
    assert state["task"]["task_id"] is None
    assert state["skills"] == []
    assert state["actions"] == []


def test_project_task_state_summarizes_successful_skill_and_action():
    events = [
        _event("task.received", {"command": "去二楼救人"}, timestamp="2026-06-03T00:00:00+00:00"),
        _event("task.planned", {"intent": "rescue_victim"}, timestamp="2026-06-03T00:00:01+00:00"),
        _event(
            "skill.started",
            {"skill_name": "navigate_to_floor", "inputs": {"floor": 2}},
            timestamp="2026-06-03T00:00:02+00:00",
        ),
        _event(
            "action.requested",
            {
                "action_id": "action-1",
                "task_id": "task-1",
                "skill_name": "navigate_to_floor",
                "action_type": "navigate_to_floor",
                "inputs": {"floor": 2},
                "risk_level": "low",
                "dry_run": True,
                "timeout_seconds": None,
            },
            timestamp="2026-06-03T00:00:03+00:00",
        ),
        _event("action.started", {"action_id": "action-1"}, timestamp="2026-06-03T00:00:04+00:00"),
        _event(
            "action.succeeded",
            {
                "action_id": "action-1",
                "status": "succeeded",
                "output": {"mode": "simulator", "floor": 2},
            },
            timestamp="2026-06-03T00:00:05+00:00",
        ),
        _event(
            "skill.attempted",
            {"skill_name": "navigate_to_floor", "attempt_number": 1, "status": "succeeded"},
            timestamp="2026-06-03T00:00:06+00:00",
        ),
        _event(
            "skill.succeeded",
            {"skill_name": "navigate_to_floor", "attempt_count": 1},
            timestamp="2026-06-03T00:00:07+00:00",
        ),
        _event(
            "task.completed",
            {"status": "succeeded", "result": {"status": "succeeded"}},
            timestamp="2026-06-03T00:00:08+00:00",
        ),
    ]

    state = project_task_state(events)

    assert state["task"]["status"] == "completed"
    assert state["task"]["task_id"] == "task-1"
    assert state["task"]["session_id"] == "session-1"
    assert state["task"]["command"] == "去二楼救人"
    assert state["task"]["skill_count"] == 1
    assert state["task"]["action_count"] == 1
    assert state["task"]["active_skill_name"] is None
    assert state["task"]["active_action_id"] is None
    assert state["skills"][0]["skill_run_id"] == "skill-1"
    assert state["skills"][0]["status"] == "succeeded"
    assert state["skills"][0]["attempt_count"] == 1
    assert state["skills"][0]["action_ids"] == ["action-1"]
    assert state["actions"][0]["action_id"] == "action-1"
    assert state["actions"][0]["skill_run_id"] == "skill-1"
    assert state["actions"][0]["status"] == "succeeded"
    assert state["actions"][0]["output"]["floor"] == 2


def test_project_task_state_tracks_cancel_requested_before_terminal_result():
    events = [
        _event("task.received", {"command": "去二楼救人"}),
        _event("skill.started", {"skill_name": "slow_policy", "inputs": {}}),
        _event("action.requested", {"action_id": "action-1", "skill_name": "slow_policy", "action_type": "slow_policy"}),
        _event("action.started", {"action_id": "action-1"}),
        _event("task.cancel_requested", {"status": "cancel_requested"}),
    ]

    state = project_task_state(events)

    assert state["task"]["status"] == "cancel_requested"
    assert state["task"]["active_skill_name"] == "slow_policy"
    assert state["task"]["active_action_id"] == "action-1"
    assert state["actions"][0]["status"] == "running"


def test_project_task_state_tracks_final_cancelled_result():
    events = [
        _event("task.received", {"command": "去二楼救人"}),
        _event("skill.started", {"skill_name": "slow_policy", "inputs": {}}),
        _event("action.requested", {"action_id": "action-1", "skill_name": "slow_policy", "action_type": "slow_policy"}),
        _event("action.cancel_requested", {"action_id": "action-1"}),
        _event("action.cancelled", {"action_id": "action-1"}),
        _event("task.cancelled", {"status": "cancelled", "result": {"status": "cancelled"}}),
    ]

    state = project_task_state(events)

    assert state["task"]["status"] == "cancelled"
    assert state["skills"][0]["status"] == "cancelled"
    assert state["actions"][0]["status"] == "cancelled"


def test_project_task_state_records_action_feedback():
    events = [
        _event("task.received", {"command": "去二楼救人"}),
        _event("skill.started", {"skill_name": "navigate_to_floor", "inputs": {"floor": 2}}),
        _event("action.requested", {"action_id": "action-1", "skill_name": "navigate_to_floor", "action_type": "navigate_to_floor"}),
        _event("action.started", {"action_id": "action-1"}),
        _event("action.feedback", {"action_id": "action-1", "progress": 0.4, "message": "approaching stairwell"}),
        _event("action.feedback", {"action_id": "action-1", "progress": 0.8, "message": "near target floor"}),
    ]

    state = project_task_state(events)

    assert state["actions"][0]["status"] == "feedback"
    assert state["actions"][0]["feedback_count"] == 2
    assert state["actions"][0]["last_feedback"]["progress"] == 0.8


def test_project_task_state_tolerates_malformed_events():
    state = project_task_state(
        [
            {"type": "task.received", "payload": "bad"},
            {"type": "skill.started"},
            {"type": "action.requested", "payload": {"action_id": 123}},
        ]
    )

    assert state["task"]["status"] in {"received", "running"}
    assert isinstance(state["skills"], list)
    assert isinstance(state["actions"], list)
