from fireclaw_core.mission_agent import MissionAgent
from fireclaw_core.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission_scheduler import MissionFailurePolicy, MissionScheduler, MissionSchedulerConfig
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


def test_failure_policy_defaults():
    policy = MissionFailurePolicy()
    assert policy.on_failed == "reassign"
    assert policy.on_denied == "abort"
    assert policy.on_lost == "abort"
    assert policy.on_block == "escalate"
    assert policy.max_retries == 1
    assert policy.max_reassigns == 1


def test_failure_policy_decision_for():
    policy = MissionFailurePolicy(on_failed="retry", on_denied="abort", on_lost="skip", on_block="escalate")
    assert policy.decision_for("failed") == "retry"
    assert policy.decision_for("denied") == "abort"
    assert policy.decision_for("lost") == "skip"
    assert policy.decision_for("block") == "escalate"
    assert policy.decision_for("succeeded") == "abort"  # unknown → abort


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


def test_scheduler_reassigns_failed_subtask(tmp_path):
    """When on_failed='reassign', a failed subtask is reassigned to another capable robot."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    # r1's first attempt fails
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "sensor malfunction"},
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
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_failed="reassign", max_reassigns=1),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    # r1 failed, then r2 was reassigned
    assert len(client.calls) == 2
    assert client.calls[0][0].robot_id == "r1"
    assert client.calls[1][0].robot_id == "r2"


def test_scheduler_aborts_on_denied(tmp_path):
    """When on_denied='abort', mission stops immediately on denied subtask."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "denied",
        "result": {"status": "denied", "message": "safety gate blocked"},
        "events": [{"type": "task.denied"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_denied="abort"),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "aborted"
    assert len(client.calls) == 1  # No retry/reassign for denied


def test_scheduler_escalates_on_block(tmp_path):
    """When on_block='escalate', subtask is marked escalated and mission continues."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "block",
        "result": {"status": "block", "message": "需要人工确认"},
        "events": [{"type": "task.blocked"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_block="escalate"),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "escalated"
    assert any(d.get("decision") == "escalated" for d in result.get("failure_decisions", []))


def test_scheduler_retries_failed_subtask(tmp_path):
    """When on_failed='retry', a failed subtask is retried on the same robot."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    # r1's first attempt fails
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "transient error"},
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
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_failed="retry", max_retries=1),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    # After retry fails again (max_retries=1), it gets skipped
    assert result["status"] == "succeeded"
    assert len(client.calls) == 2  # Original + 1 retry
    assert any(d["decision"] == "skipped" and d["reason"] == "max_retries_exceeded"
               for d in result.get("failure_decisions", []))
