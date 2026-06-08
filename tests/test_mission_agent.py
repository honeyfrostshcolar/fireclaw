from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_planner import MissionPlan, MissionPlannerContext, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


class FakeSubagentClient:
    def __init__(self):
        self.calls = []
        self.cancel_calls = []
        self.traces = {}

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
