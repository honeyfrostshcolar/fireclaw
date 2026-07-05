from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_trace_stream import MissionEvent, MissionTraceStream
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


class FakeSubagentClient:
    def __init__(self):
        self.calls = []
        self.traces = {}
        self.trace_call_count: dict[tuple[str, str], int] = {}
        self.trace_sequence: dict[tuple[str, str], list[dict]] = {}

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": f"task-{entry.robot_id}",
            "session_id": kwargs.get("session_id"),
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        key = (entry.robot_id, task_id)
        # Sequence-based traces: return different results on each call
        if key in self.trace_sequence:
            count = self.trace_call_count.get(key, 0)
            seq = self.trace_sequence[key]
            result = seq[min(count, len(seq) - 1)]
            self.trace_call_count[key] = count + 1
            return result
        if key in self.traces:
            return self.traces[key]
        return {
            "task_id": task_id,
            "robot_id": entry.robot_id,
            "status": "running",
            "result": None,
            "events": [],
        }

    def cancel_task(self, entry, task_id, *, operator=None):
        return {"status": "cancel_requested", "task_id": task_id, "robot_id": entry.robot_id}

    def check_presence(self, entry):
        return {"robot_id": entry.robot_id, "online": True, "last_seen_at": "2026-06-08T00:00:00+00:00", "state": {}}


def test_stream_emits_subtask_submitted():
    """When a subtask is submitted, stream emits subtask.submitted."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry("/dev/null")
    # Use an in-memory path for testing
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    tmp.close()
    try:
        mission_registry = JsonlMissionRegistry(tmp.name)
        mission = MissionAgent(registry=registry, subagent_client=client, mission_registry=mission_registry)
        # Submit a subtask to create the mission record
        mission.submit_subtask("r1", "去二楼搜索", session_id="m1")

        stream = MissionTraceStream(mission_agent=mission, poll_interval_seconds=0.01)
        events = list(stream.stream("m1", timeout_seconds=1.0))

        submitted = [e for e in events if e.type == "subtask.submitted"]
        assert len(submitted) >= 1
        assert submitted[0].robot_id == "r1"
        assert submitted[0].mission_id == "m1"
    finally:
        os.unlink(tmp.name)


def test_stream_emits_status_change():
    """When a subtask status changes, stream emits subtask.status_changed."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    # First trace call returns running, second returns succeeded
    client.trace_sequence[("r1", "task-r1")] = [
        {"task_id": "task-r1", "robot_id": "r1", "status": "running", "result": None, "events": []},
        {"task_id": "task-r1", "robot_id": "r1", "status": "succeeded", "result": {"status": "succeeded", "message": "done"}, "events": [{"type": "task.completed"}]},
    ]
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    tmp.close()
    try:
        mission_registry = JsonlMissionRegistry(tmp.name)
        mission = MissionAgent(registry=registry, subagent_client=client, mission_registry=mission_registry)
        mission.submit_subtask("r1", "去二楼搜索", session_id="m1")

        stream = MissionTraceStream(mission_agent=mission, poll_interval_seconds=0.01)
        events = list(stream.stream("m1", timeout_seconds=1.0))

        status_changes = [e for e in events if e.type == "subtask.status_changed"]
        assert len(status_changes) >= 1
        assert status_changes[0].status == "completed"
        assert status_changes[0].previous_status == "accepted"
    finally:
        os.unlink(tmp.name)


def test_stream_emits_mission_terminal_event():
    """When mission reaches terminal status, stream emits mission event and stops."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    # First call running, second call succeeded
    client.trace_sequence[("r1", "task-r1")] = [
        {"task_id": "task-r1", "robot_id": "r1", "status": "running", "result": None, "events": []},
        {"task_id": "task-r1", "robot_id": "r1", "status": "succeeded", "result": {"status": "succeeded", "message": "done"}, "events": [{"type": "task.completed"}]},
    ]
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    tmp.close()
    try:
        mission_registry = JsonlMissionRegistry(tmp.name)
        mission = MissionAgent(registry=registry, subagent_client=client, mission_registry=mission_registry)
        mission.submit_subtask("r1", "去二楼搜索", session_id="m1")

        stream = MissionTraceStream(mission_agent=mission, poll_interval_seconds=0.01)
        events = list(stream.stream("m1", timeout_seconds=1.0))

        mission_events = [e for e in events if e.type.startswith("mission.")]
        assert len(mission_events) >= 1
        assert any(e.type == "mission.succeeded" for e in mission_events)
    finally:
        os.unlink(tmp.name)


def test_stream_ends_on_timeout():
    """When timeout is reached, stream emits timeout event and stops."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    # Keep trace in "running" so mission never reaches terminal
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "running",
        "result": None,
        "events": [],
    }
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    tmp.close()
    try:
        mission_registry = JsonlMissionRegistry(tmp.name)
        mission = MissionAgent(registry=registry, subagent_client=client, mission_registry=mission_registry)
        mission.submit_subtask("r1", "去二楼搜索", session_id="m1")

        stream = MissionTraceStream(mission_agent=mission, poll_interval_seconds=0.01)
        events = list(stream.stream("m1", timeout_seconds=0.1))

        timeout_events = [e for e in events if e.type == "mission.timeout"]
        assert len(timeout_events) == 1
    finally:
        os.unlink(tmp.name)


def test_event_to_dict():
    """MissionEvent.to_dict() returns all fields."""
    event = MissionEvent(
        type="subtask.status_changed",
        mission_id="m1",
        robot_id="r1",
        task_id="t1",
        status="succeeded",
        previous_status="running",
        timestamp="2026-06-08T00:00:00+00:00",
        details={"key": "value"},
    )
    d = event.to_dict()
    assert d["type"] == "subtask.status_changed"
    assert d["mission_id"] == "m1"
    assert d["robot_id"] == "r1"
    assert d["task_id"] == "t1"
    assert d["status"] == "succeeded"
    assert d["previous_status"] == "running"
    assert d["details"] == {"key": "value"}
