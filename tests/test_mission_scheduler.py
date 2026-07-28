from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_planner import MissionPlan, MissionSubtask
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.mission.mission_scheduler import MissionFailurePolicy, MissionScheduler, MissionSchedulerConfig
from fireclaw_core.mission.revision_dispatcher import (
    JsonlMissionDispatchStore,
    MissionDispatchCheckpoint,
    MissionNodeExecution,
    checkpoint_for_graph,
    execution_spec_hash,
)
from fireclaw_core.mission.task_graph import task_graph_from_mission_plan
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry


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


def test_scheduler_config_has_bounded_revision_cancellation_wait():
    config = MissionSchedulerConfig()
    assert config.revision_cancel_timeout_seconds == 5.0


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


def test_scheduler_blocks_before_dispatch_when_checkpoint_fails(tmp_path):
    class FailingStore:
        def append(self, checkpoint):
            raise OSError("disk unavailable")

    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=JsonlMissionRegistry(tmp_path / "missions.jsonl"),
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(
                robot_id="r1",
                command="去2楼搜索受困人员",
                floor=2,
                capability_required="search_for_victims",
            ),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        dispatch_store=FailingStore(),
    )

    result = scheduler.schedule(plan, mission_id="m1")

    assert result["status"] == "blocked"
    assert "checkpoint could not be persisted" in result["message"]
    assert client.calls == []


def test_restart_recovery_reconciles_active_task_and_dispatches_dependency(
    tmp_path,
):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
        RobotRegistryEntry(
            robot_id="r2",
            base_url="http://r2:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "existing-task")] = {
        "task_id": "existing-task",
        "robot_id": "r1",
        "status": "succeeded",
        "result": {"status": "succeeded"},
        "events": [{"type": "task.completed"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id="m1",
        command="search floors",
        created_at="2026-07-28T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="m1",
        robot_id="r1",
        task_id="existing-task",
        command="search floor 2",
        status="running",
        created_at="2026-07-28T00:00:01+00:00",
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="search floors",
        subtasks=[
            MissionSubtask(
                robot_id="r1",
                command="search floor 2",
                floor=2,
                capability_required="search_for_victims",
                execution_group=0,
            ),
            MissionSubtask(
                robot_id="r2",
                command="search floor 3",
                floor=3,
                capability_required="search_for_victims",
                execution_group=1,
            ),
        ],
    )
    graph = task_graph_from_mission_plan(
        plan,
        mission_id="m1",
        plan_id="m1:plan:1",
        state_snapshot_id="m1:state:1",
    )
    store = JsonlMissionDispatchStore(tmp_path / "missions.dispatch.jsonl")
    store.append(checkpoint_for_graph(
        graph,
        (
            MissionNodeExecution(
                plan_id=graph.plan_id,
                node_id="task-1",
                robot_id="r1",
                task_id="existing-task",
                status="running",
                node_spec_hash=execution_spec_hash(graph.nodes[0]),
            ),
        ),
        memory_command_event_id="command-event",
        memory_plan_event_id="plan-event",
    ))
    scheduler = MissionScheduler(
        mission_agent=mission,
        dispatch_store=store,
        config=MissionSchedulerConfig(poll_interval_seconds=0.01),
    )

    results = scheduler.resume_pending_dispatches()

    assert results[0]["status"] == "succeeded"
    assert results[0]["recovered"] is True
    assert results[0]["resumed"] is True
    assert [call[0].robot_id for call in client.calls] == ["r2"]
    assert client.calls[0][1]["dedupe_key"] == "m1-m1:plan:1-task-2"
    latest = store.latest("m1")
    assert latest is not None
    assert latest.memory_command_event_id == "command-event"
    assert latest.memory_plan_event_id == "plan-event"
    assert mission.active_task_graph("m1") == graph


def test_restart_recovery_dispatches_pending_node_with_stable_dedupe_key(
    tmp_path,
):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id="m1",
        command="search floor 2",
        created_at="2026-07-28T00:00:00+00:00",
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="search floor 2",
        subtasks=[
            MissionSubtask(
                robot_id="r1",
                command="search floor 2",
                floor=2,
                capability_required="search_for_victims",
            ),
        ],
    )
    graph = task_graph_from_mission_plan(
        plan,
        mission_id="m1",
        plan_id="m1:plan:1",
        state_snapshot_id="m1:state:1",
    )
    store = JsonlMissionDispatchStore(tmp_path / "missions.dispatch.jsonl")
    store.append(checkpoint_for_graph(graph, ()))

    result = MissionScheduler(
        mission_agent=mission,
        dispatch_store=store,
        config=MissionSchedulerConfig(poll_interval_seconds=0.01),
    ).resume_pending_dispatches()[0]

    assert result["status"] == "succeeded"
    assert len(client.calls) == 1
    assert client.calls[0][1]["dedupe_key"] == "m1-m1:plan:1-task-1"


def test_restart_recovery_blocks_unknown_active_robot_task_state(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "existing-task")] = {
        "task_id": "existing-task",
        "robot_id": "r1",
        "status": "unknown",
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id="m1",
        command="search floor 2",
        created_at="2026-07-28T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="m1",
        robot_id="r1",
        task_id="existing-task",
        command="search floor 2",
        status="running",
        created_at="2026-07-28T00:00:01+00:00",
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="search floor 2",
        subtasks=[
            MissionSubtask(
                robot_id="r1",
                command="search floor 2",
                floor=2,
                capability_required="search_for_victims",
            ),
        ],
    )
    graph = task_graph_from_mission_plan(
        plan,
        mission_id="m1",
        plan_id="m1:plan:1",
    )
    store = JsonlMissionDispatchStore(tmp_path / "missions.dispatch.jsonl")
    store.append(checkpoint_for_graph(
        graph,
        (
            MissionNodeExecution(
                plan_id=graph.plan_id,
                node_id="task-1",
                robot_id="r1",
                task_id="existing-task",
                status="running",
                node_spec_hash=execution_spec_hash(graph.nodes[0]),
            ),
        ),
    ))

    result = MissionScheduler(
        mission_agent=mission,
        dispatch_store=store,
    ).resume_pending_dispatches()[0]

    assert result["status"] == "blocked"
    assert "no authoritative runtime status" in result["message"]
    assert result["recovered"] is False
    assert client.calls == []


def test_restart_recovery_blocks_legacy_checkpoint_without_task_graph(
    tmp_path,
):
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id="m1",
        command="search floor 2",
        created_at="2026-07-28T00:00:00+00:00",
    )
    store = JsonlMissionDispatchStore(tmp_path / "missions.dispatch.jsonl")
    store.append(MissionDispatchCheckpoint(
        mission_id="m1",
        active_plan_id="m1:plan:1",
        revision=1,
        node_executions=(),
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )

    result = MissionScheduler(
        mission_agent=mission,
        dispatch_store=store,
    ).resume_pending_dispatches()[0]

    assert result["status"] == "blocked"
    assert "predates restart-safe task graph" in result["message"]
    assert client.calls == []


def test_restart_recovery_finishes_superseded_cancellation_before_dispatch(
    tmp_path,
):
    class CancellationRecoveryClient(FakeSubagentClient):
        def __init__(self):
            super().__init__()
            self.call_order = []

        def submit_task(self, entry, **kwargs):
            self.call_order.append(("submit", entry.robot_id))
            return super().submit_task(entry, **kwargs)

        def cancel_task(self, entry, task_id, *, operator=None):
            self.call_order.append(("cancel", entry.robot_id))
            self.traces[(entry.robot_id, task_id)] = {
                "task_id": task_id,
                "robot_id": entry.robot_id,
                "status": "cancelled",
                "result": {"status": "cancelled"},
                "events": [{"type": "task.cancelled"}],
            }
            return super().cancel_task(
                entry,
                task_id,
                operator=operator,
            )

    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="r1",
            base_url="http://r1:8765",
            capabilities=("search_for_victims",),
        ),
        RobotRegistryEntry(
            robot_id="r2",
            base_url="http://r2:8765",
            capabilities=("search_for_victims",),
        ),
    ])
    client = CancellationRecoveryClient()
    client.traces[("r1", "old-task")] = {
        "task_id": "old-task",
        "robot_id": "r1",
        "status": "running",
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="m1",
        session_id="m1",
        command="search floor 2",
        created_at="2026-07-28T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="m1",
        robot_id="r1",
        task_id="old-task",
        command="use blocked route",
        status="running",
        created_at="2026-07-28T00:00:01+00:00",
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="search floor 2",
        subtasks=[
            MissionSubtask(
                robot_id="r2",
                command="use safe route",
                floor=2,
                capability_required="search_for_victims",
            ),
        ],
    )
    graph = task_graph_from_mission_plan(
        plan,
        mission_id="m1",
        plan_id="m1:plan:2",
        state_snapshot_id="m1:state:2",
        revision=2,
        supersedes_plan_id="m1:plan:1",
        invalidation_evidence_ids=("route-blocked-evidence",),
    )
    superseded = MissionNodeExecution(
        plan_id="m1:plan:1",
        node_id="task-1",
        robot_id="r1",
        task_id="old-task",
        status="running",
        node_spec_hash="old-spec",
    )
    store = JsonlMissionDispatchStore(tmp_path / "missions.dispatch.jsonl")
    store.append(checkpoint_for_graph(
        graph,
        (),
        fenced_task_ids=("old-task",),
        superseded_executions=(superseded,),
        invalidation_event_id="route-blocked-event",
    ))

    result = MissionScheduler(
        mission_agent=mission,
        dispatch_store=store,
        config=MissionSchedulerConfig(poll_interval_seconds=0.01),
    ).resume_pending_dispatches()[0]

    assert result["status"] == "succeeded"
    assert client.call_order == [("cancel", "r1"), ("submit", "r2")]
    assert result["recovery_cancellation_results"][0]["status"] == (
        "cancel_requested"
    )
    latest = store.latest("m1")
    assert latest is not None
    assert latest.superseded_executions == ()


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
