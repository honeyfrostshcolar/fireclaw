from fireclaw_core.approval_store import JsonlApprovalStore
from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_memory import MissionMemoryStore
from fireclaw_core.mission_planner import MissionPlan, MissionPlannerContext, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


class FakeSubagentClient:
    def __init__(self):
        self.calls = []
        self.cancel_calls = []
        self.traces = {}
        self.presence_results = {}
        self.events_by_entry = {}

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": "task-robot-1",
            "session_id": kwargs.get("session_id"),
            "robot_id": entry.robot_id,
        }

    def get_task_trace(self, entry, task_id):
        return self.traces[(entry.robot_id, task_id)]

    def cancel_task(self, entry, task_id, *, operator=None):
        self.cancel_calls.append((entry, task_id, operator))
        return {
            "status": "cancel_requested",
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def get_events(self, entry, task_id=None, limit=100):
        key = (entry.robot_id, task_id)
        return self.events_by_entry.get(key, [])

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-06-08T00:00:00+00:00",
            "state": {},
        }


def test_mission_agent_submits_explicit_subtask_to_registered_robot():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("search_for_victims",),
            )
        ]
    )
    client = FakeSubagentClient()
    mission = MissionAgent(registry=registry, subagent_client=client)

    result = mission.submit_subtask(
        "robot-1",
        "去二楼搜索受困人员",
        session_id="mission-1",
        dedupe_key="mission-1-robot-1-floor-2",
    )

    assert result["status"] == "accepted"
    assert result["mission_id"].startswith("mission-")
    assert result["robot_id"] == "robot-1"
    assert result["task_id"] == "task-robot-1"
    assert result["subtasks"] == [
        {
            "robot_id": "robot-1",
            "task_id": "task-robot-1",
            "status": "accepted",
            "command": "去二楼搜索受困人员",
        }
    ]
    assert client.calls[0][0].robot_id == "robot-1"
    assert client.calls[0][1]["command"] == "去二楼搜索受困人员"
    assert client.calls[0][1]["dedupe_key"] == "mission-1-robot-1-floor-2"


def test_mission_agent_rejects_unknown_robot_without_submitting():
    registry = RobotRegistry([])
    client = FakeSubagentClient()
    mission = MissionAgent(registry=registry, subagent_client=client)

    result = mission.submit_subtask("robot-missing", "去二楼搜索", session_id="mission-1")

    assert result == {
        "status": "not_found",
        "robot_id": "robot-missing",
        "message": "Robot subagent is not registered.",
        "subtasks": [],
    }
    assert client.calls == []


def test_mission_agent_persists_mission_and_subtask_records(tmp_path):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
            )
        ]
    )
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=FakeSubagentClient(),
        mission_registry=mission_registry,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    trace = mission_registry.mission_trace(result["mission_id"])
    assert trace["mission_id"] == "mission-1"
    assert trace["command"] == "去二楼搜索"
    assert trace["status"] == "running"
    assert trace["subtasks"] == [
        {
            "robot_id": "robot-1",
            "task_id": "task-robot-1",
            "command": "去二楼搜索",
            "status": "accepted",
            "created_at": trace["subtasks"][0]["created_at"],
            "updated_at": trace["subtasks"][0]["updated_at"],
            "result": None,
            "error": None,
        }
    ]


def test_mission_agent_aggregates_robot_subagent_traces(tmp_path):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
            )
        ]
    )
    client = FakeSubagentClient()
    client.traces[("robot-1", "task-robot-1")] = {
        "task_id": "task-robot-1",
        "robot_id": "robot-1",
        "status": "succeeded",
        "result": {"status": "succeeded", "message": "任务完成"},
        "events": [{"type": "task.completed"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    trace = mission.mission_trace(submitted["mission_id"])

    assert trace["mission_id"] == "mission-1"
    assert trace["status"] == "succeeded"
    assert trace["completed_subtask_count"] == 1
    assert trace["subtasks"][0]["status"] == "succeeded"
    assert trace["subtasks"][0]["robot_trace"]["events"] == [{"type": "task.completed"}]
    assert mission_registry.get_mission("mission-1").subtasks[0].status == "succeeded"


def test_mission_agent_cancels_recorded_non_terminal_subtasks(tmp_path):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
            )
        ]
    )
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    result = mission.cancel_mission(
        submitted["mission_id"],
        operator={"operator_id": "mission-agent", "control_scopes": ["task.cancel"]},
    )

    assert result["status"] == "cancel_requested"
    assert result["mission_id"] == "mission-1"
    assert result["cancelled_subtask_count"] == 1
    assert result["subtasks"] == [
        {
            "robot_id": "robot-1",
            "task_id": "task-robot-1",
            "status": "cancel_requested",
            "cancel_result": {
                "status": "cancel_requested",
                "task_id": "task-robot-1",
                "robot_id": "robot-1",
            },
        }
    ]
    assert client.cancel_calls[0][0].robot_id == "robot-1"
    assert client.cancel_calls[0][1] == "task-robot-1"
    assert mission_registry.get_mission("mission-1").subtasks[0].status == "cancel_requested"


def test_mission_agent_cancel_skips_terminal_subtasks(tmp_path):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
            )
        ]
    )
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")
    mission_registry.update_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id=submitted["task_id"],
        status="succeeded",
        updated_at="2026-06-08T01:00:00+00:00",
    )

    result = mission.cancel_mission("mission-1")

    assert result["status"] == "already_terminal"
    assert result["cancelled_subtask_count"] == 0
    assert client.cancel_calls == []


class FakeMissionPlanner:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def plan(self, command, context=None):
        self.calls.append((command, context))
        return self._result


def test_mission_agent_plan_and_submit_creates_subtasks_from_plan():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned",
        message="ok",
        intent="search",
        plan=plan,
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "planned"
    assert result["plan"]["intent"] == "search"
    assert len(result["subtask_results"]) == 2
    assert result["subtask_results"][0]["robot_id"] == "r1"
    assert result["subtask_results"][1]["robot_id"] == "r2"
    assert len(client.calls) == 2


def test_mission_agent_plan_and_submit_returns_error_when_no_planner():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    mission = MissionAgent(registry=registry, subagent_client=client, planner=None)

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "no_planner"
    assert "planner" in result["message"]
    assert client.calls == []


def test_mission_agent_plan_and_submit_returns_clarify_from_planner():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="clarify",
        message="请指定目标楼层。",
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("搜索整栋楼", session_id="mission-1")

    assert result["status"] == "clarify"
    assert "楼层" in result["message"]
    assert client.calls == []


# --- Authorization tests ---

def test_mission_agent_denies_submit_without_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-observer",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "denied"
    assert "mission.submit" in result["message"]
    assert client.calls == []


def test_mission_agent_allows_submit_with_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="operator",
        control_scopes=scopes_for_role("operator"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(client.calls) == 1


def test_mission_agent_denies_cancel_without_mission_scope(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    policy = ControlPolicy()
    # First submit with admin to create mission
    admin_operator = OperatorContext(
        operator_id="admin",
        role="admin",
        control_scopes=scopes_for_role("admin"),
    )
    mission_admin = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        control_policy=policy,
        operator=admin_operator,
    )
    submitted = mission_admin.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    # Now try to cancel with observer
    observer_operator = OperatorContext(
        operator_id="test-observer",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission_observer = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        control_policy=policy,
        operator=observer_operator,
    )
    result = mission_observer.cancel_mission(submitted["mission_id"])

    assert result["status"] == "denied"
    assert "mission.cancel" in result["message"]


def test_mission_agent_denies_plan_without_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-observer",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search",
        plan=MissionPlan(intent="search", command="test", subtasks=[]),
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
        planner=planner,
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1")

    assert result["status"] == "denied"
    assert "mission.plan" in result["message"]
    assert client.calls == []


def test_mission_agent_admin_bypasses_mission_scopes():
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="admin",
        role="admin",
        control_scopes=scopes_for_role("admin"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(client.calls) == 1


def test_mission_agent_no_authorization_when_policy_not_configured():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    # No control_policy or operator configured
    mission = MissionAgent(registry=registry, subagent_client=client)

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(client.calls) == 1


def test_mission_agent_check_fleet_presence_updates_registry():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
    ])
    client = FakeSubagentClient()
    client.presence_results["r2"] = {
        "robot_id": "r2",
        "online": False,
        "error": "Connection refused",
    }
    mission = MissionAgent(registry=registry, subagent_client=client)

    results = mission.check_fleet_presence()

    assert results["r1"]["online"] is True
    assert results["r2"]["online"] is False
    assert registry.is_online("r1") is True
    assert registry.is_online("r2") is False


def test_mission_agent_plan_and_submit_skips_offline_robots():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.presence_results["r2"] = {
        "robot_id": "r2",
        "online": False,
        "error": "Connection refused",
    }
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned",
        message="ok",
        intent="search",
        plan=plan,
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1")

    # Should still submit since r1 is online, but r2 should be skipped
    assert result["status"] == "planned"
    assert len(result["subtask_results"]) == 1
    assert result["subtask_results"][0]["robot_id"] == "r1"


# --- MissionMemoryStore integration tests ---

def test_mission_agent_records_outcome_on_plan_and_submit(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned",
        message="ok",
        intent="search",
        plan=plan,
    ))
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_memory=memory_store,
    )

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1")

    assert result["status"] == "planned"
    records = memory_store.list_records(mission_id=result["mission_id"], record_type="outcome")
    # 2 records from submit_subtask (one per robot) + 1 plan-level record
    assert len(records) == 3
    plan_record = [r for r in records if "subtask_count" in r.content][0]
    assert plan_record.mission_id == result["mission_id"]
    assert plan_record.record_type == "outcome"
    assert plan_record.content["command"] == "去二楼和三楼搜索受困人员"
    assert plan_record.content["subtask_count"] == 2
    assert plan_record.content["status"] == "planned"
    assert plan_record.record_id  # non-empty
    assert plan_record.created_at  # non-empty


def test_mission_agent_records_outcome_on_cancel(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        mission_memory=memory_store,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    result = mission.cancel_mission(submitted["mission_id"])

    assert result["status"] == "cancel_requested"
    records = memory_store.list_records(mission_id="mission-1", record_type="outcome")
    # One record from submit_subtask, one from cancel_mission
    cancel_records = [r for r in records if "cancel" in r.content.get("status", "")]
    assert len(cancel_records) == 1
    cancel_record = cancel_records[-1]
    assert cancel_record.content["cancelled_subtask_count"] == 1
    assert cancel_record.content["skipped_subtask_count"] == 0


def test_mission_agent_records_outcome_on_submit_subtask(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_memory=memory_store,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    records = memory_store.list_records(mission_id=result["mission_id"], record_type="outcome")
    assert len(records) == 1
    record = records[0]
    assert record.content["robot_id"] == "robot-1"
    assert record.content["task_id"] == "task-robot-1"
    assert record.content["command"] == "去二楼搜索"
    assert record.robot_id == "robot-1"


def test_mission_agent_no_memory_error_when_memory_not_configured():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    # mission_memory is None (default)
    mission = MissionAgent(registry=registry, subagent_client=client)

    # Should not crash
    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")
    assert result["status"] == "accepted"


# --- MissionEventAggregator integration tests ---

def test_mission_agent_mission_events_returns_aggregated_events(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")
    mission_id = submitted["mission_id"]

    # Set up fake events from the robot subagent
    client.events_by_entry[("robot-1", "task-robot-1")] = [
        {"type": "sensor_update", "timestamp": "2026-06-08T12:00:01Z", "data": {"temp": 42}},
        {"type": "task.completed", "timestamp": "2026-06-08T12:00:02Z", "data": {"status": "succeeded"}},
    ]

    result = mission.mission_events(mission_id)

    assert result["mission_id"] == mission_id
    assert result["event_count"] == 2
    assert result["events"][0]["type"] == "sensor_update"
    assert result["events"][0]["robot_id"] == "robot-1"
    assert result["events"][1]["type"] == "task.completed"

    # Test filtering by event_type
    result_filtered = mission.mission_events(mission_id, event_type="task.completed")
    assert result_filtered["event_count"] == 1
    assert result_filtered["events"][0]["type"] == "task.completed"

    # Test filtering by robot_id
    result_robot = mission.mission_events(mission_id, robot_id="robot-1")
    assert result_robot["event_count"] == 2

    result_other = mission.mission_events(mission_id, robot_id="other-robot")
    assert result_other["event_count"] == 0


def test_mission_agent_mission_events_denied_without_scope(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-observer",
        role="observer",
        control_scopes=set(),  # no scopes at all
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        control_policy=policy,
        operator=operator,
    )

    result = mission.mission_events("some-mission-id")

    assert result["status"] == "denied"
    assert "mission.read" in result["message"]
    assert result["mission_id"] == "some-mission-id"
    assert result["event_count"] == 0
    assert result["events"] == []


def test_mission_agent_mission_events_returns_not_configured_without_registry():
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission = MissionAgent(registry=registry, subagent_client=client)

    result = mission.mission_events("some-mission-id")

    assert result["mission_id"] == "some-mission-id"
    assert result["status"] == "not_configured"
    assert result["event_count"] == 0
    assert result["events"] == []


# --- Operator correction tests ---

def test_mission_agent_records_correction(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="operator",
        control_scopes=scopes_for_role("operator"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
        mission_memory=memory_store,
    )

    result = mission.record_correction(
        "mission-1",
        correction="应先搜索三楼再搜索二楼",
    )

    assert result["status"] == "recorded"
    assert result["mission_id"] == "mission-1"
    assert result["correction"] == "应先搜索三楼再搜索二楼"

    records = memory_store.list_records(mission_id="mission-1", record_type="correction")
    assert len(records) == 1
    assert records[0].content["correction"] == "应先搜索三楼再搜索二楼"
    assert records[0].content["operator_id"] == "test-operator"


def test_mission_agent_records_correction_with_context(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="operator",
        control_scopes=scopes_for_role("operator"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
        mission_memory=memory_store,
    )

    result = mission.record_correction(
        "mission-1",
        correction="应先搜索三楼再搜索二楼",
        context="三楼有浓烟，优先级更高",
        robot_id="robot-1",
        subtask_id="task-1",
    )

    assert result["status"] == "recorded"

    records = memory_store.list_records(mission_id="mission-1", record_type="correction")
    assert len(records) == 1
    assert records[0].content["context"] == "三楼有浓烟，优先级更高"
    assert records[0].content["operator_id"] == "test-operator"
    assert records[0].robot_id == "robot-1"
    assert records[0].subtask_id == "task-1"


def test_mission_agent_correction_denied_without_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore("/tmp/test_memory.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-observer",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
        mission_memory=memory_store,
    )

    result = mission.record_correction(
        "mission-1",
        correction="应先搜索三楼再搜索二楼",
    )

    assert result["status"] == "denied"
    assert "mission.correct" in result["message"]


# --- Approval workflow tests ---

def test_mission_agent_request_approval(tmp_path):
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    mission = MissionAgent(registry=registry, subagent_client=FakeSubagentClient(), approval_store=store)

    result = mission.request_approval(
        "mission-1", action="enter_building", risk_level="high", command="进入燃烧建筑搜索",
    )

    assert result["status"] == "pending"
    request = result["request"]
    assert request["mission_id"] == "mission-1"
    assert request["action"] == "enter_building"
    assert request["risk_level"] == "high"
    assert request["command"] == "进入燃烧建筑搜索"
    assert request["status"] == "pending"
    assert request["request_id"]
    assert request["requested_by"] == "unknown"  # no operator configured


def test_mission_agent_request_approval_with_operator(tmp_path):
    from fireclaw_core.control import OperatorContext
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    operator = OperatorContext(operator_id="op-1", role="operator")
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, operator=operator,
    )

    result = mission.request_approval(
        "mission-1", action="enter_building", risk_level="high", command="进入燃烧建筑搜索",
    )

    assert result["status"] == "pending"
    assert result["request"]["requested_by"] == "op-1"
    # Verify it was persisted
    stored = store.get(result["request"]["request_id"])
    assert stored is not None
    assert stored.mission_id == "mission-1"


def test_mission_agent_decide_approval_approve(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="supervisor-1", role="supervisor",
        control_scopes=scopes_for_role("supervisor") | {"mission.approve"},
    )
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, control_policy=policy, operator=operator,
    )
    created = store.create(
        mission_id="mission-1", action="enter_building", risk_level="high",
        command="进入燃烧建筑搜索", requested_by="op-1", created_at="2026-06-08T00:00:00+00:00",
    )

    result = mission.decide_approval(created.request_id, decision="approve")

    assert result["status"] == "decided"
    assert result["request"]["status"] == "approved"
    assert result["request"]["decided_by"] == "supervisor-1"


def test_mission_agent_decide_approval_deny(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="supervisor-1", role="supervisor",
        control_scopes=scopes_for_role("supervisor") | {"mission.approve"},
    )
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, control_policy=policy, operator=operator,
    )
    created = store.create(
        mission_id="mission-1", action="enter_building", risk_level="high",
        command="进入燃烧建筑搜索", requested_by="op-1", created_at="2026-06-08T00:00:00+00:00",
    )

    result = mission.decide_approval(created.request_id, decision="deny", reason="条件不满足")

    assert result["status"] == "decided"
    assert result["request"]["status"] == "denied"
    assert result["request"]["reason"] == "条件不满足"
    assert result["request"]["decided_by"] == "supervisor-1"


def test_mission_agent_decide_approval_denied_without_scope(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-observer", role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, control_policy=policy, operator=operator,
    )
    created = store.create(
        mission_id="mission-1", action="enter_building", risk_level="high",
        command="进入燃烧建筑搜索", requested_by="op-1", created_at="2026-06-08T00:00:00+00:00",
    )

    result = mission.decide_approval(created.request_id, decision="approve")

    assert result["status"] == "denied"
    assert "mission.approve" in result["message"]


def test_mission_agent_decide_approval_invalid_decision(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="supervisor-1", role="supervisor",
        control_scopes=scopes_for_role("supervisor") | {"mission.approve"},
    )
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, control_policy=policy, operator=operator,
    )
    created = store.create(
        mission_id="mission-1", action="enter_building", risk_level="high",
        command="进入燃烧建筑搜索", requested_by="op-1", created_at="2026-06-08T00:00:00+00:00",
    )

    result = mission.decide_approval(created.request_id, decision="maybe")

    assert result["status"] == "error"
    assert "Invalid decision" in result["message"]


def test_mission_agent_decide_approval_not_found(tmp_path):
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([])
    store = JsonlApprovalStore(tmp_path / "approvals.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="supervisor-1", role="supervisor",
        control_scopes=scopes_for_role("supervisor") | {"mission.approve"},
    )
    mission = MissionAgent(
        registry=registry, subagent_client=FakeSubagentClient(),
        approval_store=store, control_policy=policy, operator=operator,
    )

    result = mission.decide_approval("nonexistent-id", decision="approve")

    assert result["status"] == "not_found"
    assert result["request_id"] == "nonexistent-id"


def test_mission_agent_request_approval_not_configured():
    registry = RobotRegistry([])
    mission = MissionAgent(registry=registry, subagent_client=FakeSubagentClient())

    result = mission.request_approval(
        "mission-1", action="enter_building", risk_level="high", command="进入燃烧建筑搜索",
    )

    assert result["status"] == "not_configured"


def test_mission_agent_decide_approval_not_configured():
    registry = RobotRegistry([])
    mission = MissionAgent(registry=registry, subagent_client=FakeSubagentClient())

    result = mission.decide_approval("some-id", decision="approve")

    assert result["status"] == "not_configured"
