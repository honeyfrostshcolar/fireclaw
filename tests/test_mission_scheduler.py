from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission_scheduler import MissionScheduler, MissionSchedulerConfig
from fireclaw_core.robot_registry import RobotRegistry, RobotRegistryEntry


class FakeSubagentClient:
    def __init__(self):
        self.calls = []
        self.cancel_calls = []
        self.traces = {}
        self.presence_results = {}

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
        if key in self.traces:
            return self.traces[key]
        return {
            "task_id": task_id,
            "robot_id": entry.robot_id,
            "status": "succeeded",
            "result": {"status": "succeeded", "message": "done"},
            "events": [{"type": "task.completed"}],
        }

    def cancel_task(self, entry, task_id, *, operator=None):
        self.cancel_calls.append((entry, task_id, operator))
        return {
            "status": "cancel_requested",
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-06-08T00:00:00+00:00",
            "state": {},
        }


def test_scheduler_config_defaults():
    config = MissionSchedulerConfig()
    assert config.failure_policy == "stop"
    assert config.poll_interval_seconds == 0.1
    assert config.group_timeout_seconds == 300.0


def test_scheduler_submits_parallel_group(tmp_path):
    """Two robots in same execution_group both get submitted."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(poll_interval_seconds=0.01))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 1
    assert result["group_results"][0]["group_index"] == 0
    assert len(result["group_results"][0]["subtask_results"]) == 2
    assert len(client.calls) == 2


def test_scheduler_sequential_groups(tmp_path):
    """One robot reused across two execution_groups. Group 1 starts after group 0 completes."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(poll_interval_seconds=0.01))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 2
    assert result["group_results"][0]["group_index"] == 0
    assert result["group_results"][1]["group_index"] == 1
    assert len(client.calls) == 2


def test_scheduler_stops_on_group_failure(tmp_path):
    """When failure_policy='stop', group 1 is not submitted if group 0 has a failed subtask."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "robot malfunction"},
        "events": [{"type": "task.failed"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(
        poll_interval_seconds=0.01,
        failure_policy="stop",
    ))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "stopped"
    assert len(result["group_results"]) == 1
    assert len(client.calls) == 1


def test_scheduler_continues_on_failure_when_policy_is_continue(tmp_path):
    """When failure_policy='continue', group 1 is submitted even if group 0 failed."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "robot malfunction"},
        "events": [{"type": "task.failed"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(
        poll_interval_seconds=0.01,
        failure_policy="continue",
    ))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 2
    assert len(client.calls) == 2
