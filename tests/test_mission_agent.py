import json
from unittest.mock import MagicMock

from fireclaw_core.approval.approval_store import JsonlApprovalStore
from fireclaw_core.memory.memory_retrieval import RetrievedMemory
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.agent.robot_agent import envelope_from_structured_task
from fireclaw_core.task.task_contract import StructuredRobotTask, validate_structured_robot_task
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_planning_audit import GuardDecision, MissionPlanningAuditRecord
from fireclaw_core.mission.mission_planner import MissionPlan, MissionPlannerContext, MissionPlanningResult, MissionSubtask
from fireclaw_core.mission.mission_registry import JsonlMissionRegistry
from fireclaw_core.plugin.plugin_runtime import PluginRuntime
from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
from fireclaw_core.subagent.subagent_client import RobotSubagentClient
from fireclaw_core.task.task_registry import JsonlTaskRegistryStore
from fireclaw_core.task.task_flow_registry import JsonlTaskFlowRegistryStore


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


class FakeMemoryRetriever:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, query, *, scope=None, limit=10):
        self.calls.append((query, scope, limit))
        return self.results[:limit]


class FakeAuditSink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


class FailingAuditSink:
    def record(self, record):
        raise RuntimeError("audit unavailable")


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
        "message": "Robot Agent is not registered.",
        "subtasks": [],
    }
    assert client.calls == []


def test_mission_agent_records_validator_allow_decision_before_dispatch():
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
    audit_sink = FakeAuditSink()
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索受困人员",
        available_robots=[
            {
                "robot_id": "robot-1",
                "capabilities": ["search_for_victims"],
                "enabled": True,
                "zone": None,
            }
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[
            GuardDecision(
                layer="parser",
                status="allow",
                reason="mission_plan_parsed",
                message="LLM mission plan parsed.",
            )
        ],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-04T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    assert record.mission_id == "mission-1"
    assert record.final_status == "planned"
    assert record.decisions[-1].layer == "validator"
    assert record.decisions[-1].status == "allow"
    assert record.decisions[-1].reason == "mission_plan_valid"
    assert client.calls


def test_mission_agent_records_validator_block_decision_without_dispatching():
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
    audit_sink = FakeAuditSink()
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索受困人员",
        available_robots=[
            {
                "robot_id": "robot-1",
                "capabilities": ["search_for_victims"],
                "enabled": True,
                "zone": None,
            }
        ],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-04T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=0,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "blocked"
    assert "Subtask floor must be positive" in result["errors"][0]
    assert client.calls == []
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    assert record.final_status == "blocked"
    assert record.decisions[-1].layer == "validator"
    assert record.decisions[-1].status == "block"
    assert record.decisions[-1].reason == "mission_plan_invalid"
    assert "errors" in record.decisions[-1].details


def test_mission_agent_blocks_allowed_plan_when_audit_sink_fails():
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
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索受困人员",
            subtasks=[
                MissionSubtask(
                    robot_id="robot-1",
                    command="去2楼搜索受困人员",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=MissionPlanningAuditRecord(
            command="去二楼搜索受困人员",
            available_robots=[],
            tool_schema={"type": "function"},
            llm_tool_call=None,
            decisions=[],
            final_status="planned",
            final_message="planned",
            created_at="2026-07-04T00:00:00+00:00",
        ),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=FailingAuditSink(),
    )

    result = mission.plan_and_submit("去二楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "blocked"
    assert result["message"] == "Mission planning audit could not be recorded."
    assert result["subtask_results"] == []
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
    assert trace["subtasks"][0]["status"] == "completed"
    assert trace["subtasks"][0]["robot_trace"]["events"] == [{"type": "task.completed"}]
    assert mission_registry.get_mission("mission-1").subtasks[0].status == "completed"


def test_mission_trace_normalizes_robot_completed_retrieval_to_completed(tmp_path):
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot.local",
                capabilities=("monitor_environment",),
            )
        ]
    )
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="mission-1",
        session_id="session-1",
        command="检查一楼烟雾",
        created_at="2026-06-11T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="检查一楼烟雾",
        status="accepted",
        created_at="2026-06-11T00:00:00+00:00",
    )

    class TraceClient:
        def get_task_trace(self, entry, task_id):
            return {
                "task_id": task_id,
                "status": "retrieved",
                "result": {"status": "retrieved", "message": "没有找到匹配的记忆记录。"},
                "queue_record": {"status": "completed"},
                "events": [{"type": "task.completed", "payload": {"status": "retrieved"}}],
            }

    agent = MissionAgent(
        registry=registry,
        subagent_client=TraceClient(),
        mission_registry=mission_registry,
    )

    trace = agent.mission_trace("mission-1")

    assert trace["status"] == "succeeded"
    assert trace["completed_subtask_count"] == 1
    assert trace["subtasks"][0]["status"] == "completed"


def test_mission_trace_prefers_escalated_result_over_stale_completed_wrapper(
    tmp_path,
):
    registry = RobotRegistry(
        [RobotRegistryEntry(robot_id="robot-1", base_url="http://robot.local")]
    )
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission_registry.create_mission(
        mission_id="mission-1",
        session_id="session-1",
        command="搜索受困人员",
        created_at="2026-06-11T00:00:00+00:00",
    )
    mission_registry.record_subtask(
        mission_id="mission-1",
        robot_id="robot-1",
        task_id="task-1",
        command="搜索受困人员",
        status="accepted",
        created_at="2026-06-11T00:00:00+00:00",
    )

    class TraceClient:
        def get_task_trace(self, entry, task_id):
            return {
                "task_id": task_id,
                "status": "completed",
                "result": {
                    "status": "escalated",
                    "message": "本地恢复失败，需要中央处理",
                },
                "queue_record": {"status": "completed"},
                "events": [
                    {
                        "type": "task.completed",
                        "payload": {"status": "escalated"},
                    }
                ],
            }

    agent = MissionAgent(
        registry=registry,
        subagent_client=TraceClient(),
        mission_registry=mission_registry,
    )

    trace = agent.mission_trace("mission-1")

    assert trace["status"] == "escalated"
    assert trace["completed_subtask_count"] == 1
    assert trace["subtasks"][0]["status"] == "escalated"
    assert (
        mission_registry.get_mission("mission-1").subtasks[0].status
        == "escalated"
    )


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
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
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

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

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


def test_plan_and_submit_dispatches_primitive_fallback_on_clarify():
    """When planner returns 'clarify' but a robot has primitive skills, dispatch a primitive_composition task."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="clarify",
        message="unknown intent",
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        primitive_skills_by_robot={"r1": ("navigate_to_floor", "search_area")},
    )

    result = mission.plan_and_submit("导航到 x=2 y=0", session_id="m1")

    assert result["status"] == "accepted"
    assert "r1" in result["message"]
    assert len(client.calls) == 1
    submitted_entry, submitted_kwargs = client.calls[0]
    assert submitted_entry.robot_id == "r1"
    structured_task = submitted_kwargs.get("structured_task", {})
    assert structured_task.get("task_type") == "primitive_composition"
    assert "navigate_to_floor" in structured_task.get("allowed_skills", [])
    assert "search_area" in structured_task.get("allowed_skills", [])

    # Round-trip: the structured_task should survive from_dict → envelope construction
    task = StructuredRobotTask.from_dict(structured_task)
    envelope = envelope_from_structured_task(task, fallback_robot_id="r1")
    assert "navigate_to_floor" in envelope.allowed_skills
    assert "search_area" in envelope.allowed_skills


def test_plan_and_submit_primitive_fallback_blocked_for_high_risk_command():
    """High-risk commands should be blocked even when primitive skills exist."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="clarify",
        message="unknown intent",
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        primitive_skills_by_robot={"r1": ("navigate_to_floor",)},
    )

    result = mission.plan_and_submit("去三楼灭火", session_id="m1")

    assert result["status"] == "clarify"
    assert "复合技能" in result["message"] or "人工确认" in result["message"]
    assert client.calls == []


def test_plan_and_submit_primitive_fallback_skips_when_no_primitive_skills():
    """When no robot has primitive skills, fallback should not trigger and original clarify is returned."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="clarify",
        message="unknown intent",
    ))
    # No primitive_skills_by_robot configured
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("导航到 x=2 y=0", session_id="m1")

    assert result["status"] == "clarify"
    assert client.calls == []


# --- Authorization tests ---

def test_mission_agent_denies_submit_without_mission_scope():
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

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

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

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

    # Set up fake events from the Robot Agent.
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
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


def test_mission_events_updates_subagent_registry_on_terminal_robot_event(tmp_path):
    """Terminal robot lifecycle events should propagate into SubagentRegistry via mission_events()."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    subagent_registry = JsonlSubagentRegistry(tmp_path / "subagent.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        subagent_registry=subagent_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")
    mission_id = submitted["mission_id"]
    task_id = submitted["task_id"]

    # Create a subagent run record so mark_terminal can find it
    subagent_registry.create(
        parent_mission_id=mission_id,
        robot_id="robot-1",
        child_task_id=task_id,
        created_at="2026-06-08T12:00:00Z",
    )

    # Set up terminal event from robot
    client.events_by_entry[("robot-1", task_id)] = [
        {"type": "task.completed", "task_id": task_id, "timestamp": "2026-06-08T12:00:02Z"},
    ]

    mission.mission_events(mission_id)

    # SubagentRegistry should have been updated to terminal
    run_record = subagent_registry.get_by_child_task_id(task_id)
    assert run_record is not None
    assert run_record.status == "completed"


def test_mission_events_updates_task_registry_on_terminal_robot_event(tmp_path):
    """Terminal robot lifecycle events should propagate into TaskRegistry via mission_events()."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    task_registry = JsonlTaskRegistryStore(tmp_path / "tasks.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        task_registry=task_registry,
    )
    submitted = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")
    mission_id = submitted["mission_id"]
    task_id = submitted["task_id"]

    # submit_subtask should have created a task record with format mission_id:task_id
    composite_id = f"{mission_id}:{task_id}"
    record = task_registry.get(composite_id)
    assert record is not None
    assert record.status == "accepted"

    # Set up terminal event from robot
    client.events_by_entry[("robot-1", task_id)] = [
        {"type": "task.completed", "task_id": task_id, "timestamp": "2026-06-08T12:00:02Z"},
    ]

    mission.mission_events(mission_id)

    # TaskRegistry should have been updated to terminal status
    updated = task_registry.get(composite_id)
    assert updated is not None
    assert updated.status == "completed"


# --- Operator correction tests ---

def test_mission_agent_records_correction(tmp_path):
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
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
    from fireclaw_core.gateway.control import OperatorContext
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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
    from fireclaw_core.gateway.control import ControlPolicy, OperatorContext, scopes_for_role
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


# --- IncidentReplay integration tests ---

def test_mission_agent_replay_incident(tmp_path):
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
    mission_id = submitted["mission_id"]

    result = mission.replay_incident(mission_id)

    assert result["mission_id"] == "mission-1"
    assert result["status"] in ("running", "created")
    assert isinstance(result["timeline"], list)
    assert len(result["timeline"]) >= 1
    assert result["timeline"][0]["event_type"] == "subtask.submitted"
    assert result["timeline"][0]["robot_id"] == "robot-1"
    assert result["summary"]["subtask_count"] == 1


def test_mission_agent_replay_incident_not_found(tmp_path):
    registry = RobotRegistry([])
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(registry=registry, subagent_client=FakeSubagentClient(), mission_registry=mission_registry)

    result = mission.replay_incident("nonexistent-mission")

    assert result["mission_id"] == "nonexistent-mission"
    assert result["status"] == "not_found"
    assert result["timeline"] == []


def test_mission_agent_replay_incident_not_configured():
    registry = RobotRegistry([])
    # mission_registry is None
    mission = MissionAgent(registry=registry, subagent_client=FakeSubagentClient())

    result = mission.replay_incident("some-mission")

    assert result["mission_id"] == "some-mission"
    assert result["status"] == "not_configured"
    assert result["timeline"] == []


# --- Stale robot exclusion tests ---

def test_mission_agent_check_fleet_presence_marks_stale_robots():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    # r2 was seen long ago -> stale
    registry.update_presence("r2", "2000-01-01T00:00:00+00:00")
    client = FakeSubagentClient()
    # r2 doesn't respond to presence check
    client.presence_results["r2"] = {
        "robot_id": "r2",
        "online": False,
        "error": "Connection refused",
    }
    mission = MissionAgent(registry=registry, subagent_client=client)

    results = mission.check_fleet_presence()

    assert results["r1"]["online"] is True
    assert results["r2"]["online"] is False
    assert results["r2"]["stale"] is True


def test_mission_agent_check_fleet_presence_marks_non_stale_offline():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765"),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    from datetime import datetime, timezone
    # r2 was seen recently -> not stale, just offline this time
    registry.update_presence("r2", datetime.now(timezone.utc).isoformat())
    client = FakeSubagentClient()
    client.presence_results["r2"] = {
        "robot_id": "r2",
        "online": False,
        "error": "Connection refused",
    }
    mission = MissionAgent(registry=registry, subagent_client=client)

    results = mission.check_fleet_presence()

    assert results["r2"]["online"] is False
    assert results["r2"]["stale"] is False


def test_mission_agent_plan_and_submit_excludes_stale_robots():
    registry = RobotRegistry(
        [
            RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
            RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
        ],
        heartbeat_timeout_seconds=30.0,
    )
    # r2 is stale (seen long ago)
    registry.update_presence("r2", "2000-01-01T00:00:00+00:00")
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
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    # r2 is stale -> excluded from enabled_entries -> not in available_robots
    # Only r1 should be submitted
    assert result["status"] == "planned"
    assert len(result["subtask_results"]) == 1
    assert result["subtask_results"][0]["robot_id"] == "r1"


# --- MissionScheduler integration tests ---

def test_plan_and_submit_uses_scheduler_by_default(tmp_path):
    """Test that plan_and_submit(use_scheduler=True) delegates to MissionScheduler."""
    from unittest.mock import patch, MagicMock

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_registry=mission_registry,
        mission_memory=memory_store,
    )

    # Mock the MissionScheduler class to avoid actual polling
    mock_scheduler = MagicMock()
    mock_scheduler.schedule.return_value = {
        "status": "succeeded",
        "mission_id": "mission-1",
        "group_results": [{"group_index": 0, "subtask_results": []}],
        "failure_decisions": [],
    }

    with patch('fireclaw_core.mission.mission_scheduler.MissionScheduler', return_value=mock_scheduler):
        result = mission.plan_and_submit("去二楼搜索", session_id="mission-1")

    assert result["status"] == "succeeded"
    assert result["plan"]["intent"] == "search"
    assert "group_results" in result
    assert "failure_decisions" in result
    mock_scheduler.schedule.assert_called_once()
    records = memory_store.list_records(mission_id="mission-1", record_type="outcome")
    assert any(record.content.get("status") == "succeeded" for record in records)


def test_plan_and_submit_preserves_direct_iteration_when_scheduler_disabled():
    """Test that plan_and_submit(use_scheduler=False) preserves direct iteration."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    assert "subtask_results" in result
    assert len(result["subtask_results"]) == 2
    assert result["subtask_results"][0]["robot_id"] == "r1"
    assert result["subtask_results"][1]["robot_id"] == "r2"
    assert len(client.calls) == 2


def test_plan_and_submit_scheduler_result_includes_failure_decisions():
    """Test that scheduler result includes failure_decisions and group_results."""
    from unittest.mock import patch, MagicMock

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    # Mock scheduler to return failure decisions
    mock_scheduler = MagicMock()
    mock_scheduler.schedule.return_value = {
        "status": "succeeded",
        "mission_id": "mission-1",
        "group_results": [
            {
                "group_index": 0,
                "subtask_results": [{"status": "failed"}],
                "terminal_states": [{"status": "failed", "robot_id": "r1"}],
            }
        ],
        "failure_decisions": [
            {"robot_id": "r1", "status": "failed", "decision": "retry"}
        ],
    }

    with patch('fireclaw_core.mission.mission_scheduler.MissionScheduler', return_value=mock_scheduler):
        result = mission.plan_and_submit("去二楼搜索", session_id="mission-1")

    assert result["status"] == "succeeded"
    assert "group_results" in result
    assert isinstance(result["group_results"], list)
    assert len(result["group_results"]) == 1
    assert "failure_decisions" in result
    assert isinstance(result["failure_decisions"], list)
    assert len(result["failure_decisions"]) == 1
    assert result["failure_decisions"][0]["decision"] == "retry"


# --- Planner context with memories and corrections ---

def test_plan_and_submit_populates_context_with_memories_and_corrections(tmp_path):
    """plan_and_submit should retrieve memories and corrections and pass them in context."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")

    # Pre-populate memory with an outcome and a correction using the Builder's
    # admission requirements: matching mission_id and _embodied metadata.
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-1",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded", "subtask_count": 1},
    )
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-1",
        mission_id="mission-1",
        record_type="correction",
        content={"correction": "应先搜索三楼再搜索二楼", "context": "三楼有浓烟"},
    )
    # Use a FakeMemoryRetriever so the Builder can find indexed memories
    retriever = FakeMemoryRetriever([
        RetrievedMemory(
            record_id="mem-1",
            mission_id="mission-1",
            record_type="outcome",
            content={
                "_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                "command": "去二楼搜索",
                "status": "succeeded",
                "subtask_count": 1,
            },
            score=0.9,
            source="fused",
            created_at="2026-07-24T00:00:00+00:00",
        ),
    ])

    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_memory=memory_store,
        memory_retriever=retriever,
        embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    # Verify the planner received context with memories and corrections
    assert len(planner.calls) == 1
    ctx = planner.calls[0][1]
    assert ctx is not None
    assert len(ctx.retrieved_memories) == 1
    assert ctx.retrieved_memories[0]["content"]["command"] == "去二楼搜索"
    assert len(ctx.operator_corrections) == 1
    assert ctx.operator_corrections[0]["content"]["correction"] == "应先搜索三楼再搜索二楼"


def test_plan_and_submit_uses_ranked_memory_retriever_when_configured(tmp_path):
    """MissionAgent should feed ranked retriever results into planner context."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-1",
        mission_id="mission-1",
        record_type="correction",
        content={"correction": "先确认楼梯间温度"},
        created_at="2026-06-10T00:00:00+00:00",
    )
    # Seed a record that the retriever will find, with matching mission_id
    _seed_record_with_metadata(
        memory_store,
        record_id="ranked-1",
        mission_id="mission-1",
        record_type="lesson",
        content={"lesson": "二楼搜索优先走东侧楼梯"},
        created_at="2026-06-10T00:00:00+00:00",
    )
    retriever = FakeMemoryRetriever([
        RetrievedMemory(
            record_id="ranked-1",
            mission_id="mission-1",
            record_type="lesson",
            content={
                "_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                "lesson": "二楼搜索优先走东侧楼梯",
            },
            score=0.91,
            source="fused",
            created_at="2026-06-10T00:00:00+00:00",
        )
    ])
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_memory=memory_store,
        memory_retriever=retriever,
        embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    assert len(retriever.calls) == 1
    assert retriever.calls[0][0] == "去二楼搜索"
    assert retriever.calls[0][-1] == 5
    ctx = planner.calls[0][1]
    # The Builder canonicalizes records with additional fields
    assert len(ctx.retrieved_memories) >= 1
    ranked_memory = [m for m in ctx.retrieved_memories if m.get("record_id") == "ranked-1"]
    assert len(ranked_memory) == 1
    assert ranked_memory[0]["content"]["lesson"] == "二楼搜索优先走东侧楼梯"
    assert ranked_memory[0]["memory_scope"] == "current_mission"
    assert ranked_memory[0]["advisory_only"] is True
    assert len(ctx.operator_corrections) == 1
    assert ctx.operator_corrections[0]["content"]["correction"] == "先确认楼梯间温度"


def test_plan_and_submit_empty_context_when_no_memory_configured():
    """When mission_memory is None, context should have empty memories and corrections."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        # mission_memory is None
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    assert ctx is not None
    assert ctx.retrieved_memories == []
    assert ctx.operator_corrections == []


def test_plan_and_submit_redacts_secrets_in_context(tmp_path):
    """Secrets in memory content should be redacted before passing to planner."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")

    # Record with a secret in the correction, using matching mission_id and metadata
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-1",
        mission_id="mission-1",
        record_type="correction",
        content={"correction": "使用 api_key=sk-abc1234567890 进行认证", "context": "需要更新token=secretvalue1234"},
    )

    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search", plan=plan,
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_memory=memory_store,
        embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    assert ctx is not None
    assert len(ctx.operator_corrections) == 1
    # Secrets should be redacted
    correction_text = ctx.operator_corrections[0]["content"]["correction"]
    assert "sk-abc1234567890" not in correction_text
    assert "***" in correction_text


# --- Plugin hook integration tests ---

def test_plan_and_submit_applies_provider_context_hook(tmp_path):
    """Plugin enrich_context items that reference verifiable records are admitted."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    # Seed the record that the plugin will reference
    _seed_record_with_metadata(
        memory_store,
        record_id="plugin-memory",
        mission_id="mission-1",
        record_type="lesson",
        content={"lesson": "优先检查东侧楼梯"},
    )
    # Also seed an initial memory so the Builder has something in the canonical map
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-1",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded"},
    )
    retriever = FakeMemoryRetriever([
        RetrievedMemory(
            record_id="mem-1",
            mission_id="mission-1",
            record_type="outcome",
            content={
                "_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                "command": "去二楼搜索",
                "status": "succeeded",
            },
            score=0.9,
            source="fused",
            created_at="2026-07-24T00:00:00+00:00",
        ),
    ])
    runtime = PluginRuntime()
    # Plugin enriches context by referencing a verifiable record from the authority
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="fire.context",
        callback=lambda payload: {"retrieved_memories": [
            {
                "record_id": "plugin-memory",
                "mission_id": "mission-1",
                "record_type": "lesson",
                "content": {"lesson": "优先检查东侧楼梯"},
                "source": "plugin",
            }
        ]},
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims"),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        plugin_runtime=runtime,
        mission_memory=memory_store,
        memory_retriever=retriever,
        embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    enriched = [m for m in ctx.retrieved_memories if m.get("record_id") == "plugin-memory"]
    assert len(enriched) == 1


def test_plan_and_submit_drops_non_dict_plugin_memories(tmp_path):
    """Non-dict plugin enrich_context items are dropped by the Builder."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="fire.context",
        callback=lambda payload: {"retrieved_memories": [
            "not-a-dict",
            42,
            {"record_id": "unverifiable", "mission_id": "plugin", "record_type": "lesson", "content": {}, "source": "plugin"},
        ]},
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims"),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        plugin_runtime=runtime,
        embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    # All plugin items are unverifiable and should be dropped
    for mem in ctx.retrieved_memories:
        assert mem.get("record_id") != "unverifiable"


# --- TaskRegistry and SubagentRegistry lifecycle projection tests ---

def test_plan_and_submit_projects_subtask_lifecycle_records(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    task_registry = JsonlTaskRegistryStore(tmp_path / "task_registry.jsonl")
    subagent_registry = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims"),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        task_registry=task_registry,
        subagent_registry=subagent_registry,
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    projected = task_registry.list_records()
    assert len(projected) == 2
    # Find the subtask record (not the mission itself)
    subtask_records = [r for r in projected if r.parent_task_id is not None]
    assert len(subtask_records) == 1
    assert subtask_records[0].requester_session_id == "mission-1"
    assert subtask_records[0].owner_id == "r1"
    assert subtask_records[0].status in {"accepted", "queued"}
    # Find the mission record
    mission_records = [r for r in projected if r.parent_task_id is None]
    assert len(mission_records) == 1
    assert mission_records[0].task_id == "mission-1"
    assert mission_records[0].status == "planned"


# --- Memory hook wiring tests ---

def test_plan_and_submit_applies_memory_filter_hook(tmp_path):
    """Memory filter hook should be able to reduce the memory set."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-1",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded"},
    )
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-2",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去三楼搜索", "status": "failed"},
    )
    retriever = FakeMemoryRetriever([
        RetrievedMemory(
            record_id="mem-1", mission_id="mission-1", record_type="outcome",
            content={"_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                     "command": "去二楼搜索", "status": "succeeded"},
            score=0.9, source="fused", created_at="2026-07-24T00:00:00+00:00",
        ),
        RetrievedMemory(
            record_id="mem-2", mission_id="mission-1", record_type="outcome",
            content={"_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                     "command": "去三楼搜索", "status": "failed"},
            score=0.8, source="fused", created_at="2026-07-24T00:00:00+00:00",
        ),
    ])

    runtime = PluginRuntime()
    # Filter hook: keep only the first memory
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="fire.memfilter",
        callback=lambda payload: {"memories": [payload["memories"][0]]},
    )
    plan = MissionPlan(
        intent="search", command="去二楼搜索",
        subtasks=[MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims")],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry, subagent_client=client, planner=planner,
        mission_memory=memory_store, memory_retriever=retriever,
        plugin_runtime=runtime, embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    assert len(ctx.retrieved_memories) == 1
    assert ctx.retrieved_memories[0]["record_id"] == "mem-1"


def test_plan_and_submit_applies_memory_rerank_hook(tmp_path):
    """Memory rerank hook should be able to reorder the memory set."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-a",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded"},
    )
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-b",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去三楼搜索", "status": "failed"},
    )
    retriever = FakeMemoryRetriever([
        RetrievedMemory(
            record_id="mem-a", mission_id="mission-1", record_type="outcome",
            content={"_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                     "command": "去二楼搜索", "status": "succeeded"},
            score=0.9, source="fused", created_at="2026-07-24T00:00:00+00:00",
        ),
        RetrievedMemory(
            record_id="mem-b", mission_id="mission-1", record_type="outcome",
            content={"_embodied": {"runtime_mode": "simulation", "sensitivity": "standard", "source_type": "test"},
                     "command": "去三楼搜索", "status": "failed"},
            score=0.8, source="fused", created_at="2026-07-24T00:00:00+00:00",
        ),
    ])

    runtime = PluginRuntime()
    # Rerank hook: reverse the order
    runtime.register_callable(
        hook_type="memory",
        hook_name="rerank",
        plugin_id="fire.rerank",
        callback=lambda payload: {"memories": list(reversed(payload["memories"]))},
    )
    plan = MissionPlan(
        intent="search", command="去二楼搜索",
        subtasks=[MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims")],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry, subagent_client=client, planner=planner,
        mission_memory=memory_store, memory_retriever=retriever,
        plugin_runtime=runtime, embodied_runtime_mode="simulation",
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    assert len(ctx.retrieved_memories) == 2
    assert ctx.retrieved_memories[0]["record_id"] == "mem-b"
    assert ctx.retrieved_memories[1]["record_id"] == "mem-a"


def test_memory_hooks_not_called_when_no_memories(tmp_path):
    """Memory hooks should not be invoked when the memory list is empty."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    called = []
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="fire.memfilter",
        callback=lambda payload: (called.append("filter"), None)[-1],
    )
    plan = MissionPlan(
        intent="search", command="去二楼搜索",
        subtasks=[MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims")],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry, subagent_client=client, planner=planner,
        plugin_runtime=runtime,
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="m1", use_scheduler=False)

    assert result["status"] == "planned"
    assert called == []  # hook was not called


# --- SubagentRegistry auto-wiring tests ---

def test_mission_agent_auto_wires_subagent_registry_to_client(tmp_path):
    """When subagent_registry is provided but subagent_client is not,
    MissionAgent should wire registry into the default RobotSubagentClient."""
    from fireclaw_core.subagent.subagent_registry import JsonlSubagentRegistry
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    subagent_registry = JsonlSubagentRegistry(tmp_path / "subagents.jsonl")
    mission = MissionAgent(registry=registry, subagent_registry=subagent_registry)

    assert isinstance(mission.subagent_client, RobotSubagentClient)
    assert mission.subagent_client.registry is subagent_registry


def test_mission_agent_explicit_client_ignores_subagent_registry():
    """When an explicit subagent_client is provided, subagent_registry is not used for it."""
    registry = RobotRegistry([])
    explicit_client = FakeSubagentClient()
    mission = MissionAgent(registry=registry, subagent_client=explicit_client, subagent_registry="ignored")

    assert mission.subagent_client is explicit_client


def test_submit_subtask_projects_into_task_registry(tmp_path):
    """submit_subtask() should project lifecycle into task_registry."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765"),
    ])
    client = FakeSubagentClient()
    task_registry = JsonlTaskRegistryStore(tmp_path / "task_registry.jsonl")
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        task_registry=task_registry,
        mission_registry=mission_registry,
    )

    result = mission.submit_subtask("r1", "去2楼搜索", session_id="mission-x")

    assert result["status"] == "accepted"
    records = task_registry.list_records()
    assert len(records) == 1
    rec = records[0]
    assert rec.task_id == "mission-x:task-robot-1"
    assert rec.parent_task_id == "mission-x"
    assert rec.child_session_id == "task-robot-1"
    assert rec.owner_id == "r1"
    assert rec.scope_kind == "subtask"


# --- TaskFlowRegistry integration tests ---

def test_plan_and_submit_projects_task_flow_record(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    task_flow_store = JsonlTaskFlowRegistryStore(tmp_path / "flows.jsonl")
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索受困人员",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        task_flow_store=task_flow_store,
    )

    result = mission.plan_and_submit("去二楼和三楼搜索受困人员", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    flow = task_flow_store.get("mission-1")
    assert flow is not None
    assert flow.mission_id == "mission-1"
    assert flow.command == "去二楼和三楼搜索受困人员"
    assert flow.status == "running"
    assert len(flow.task_ids) == 2
    assert len(flow.robot_ids) == 2
    assert "r1" in flow.robot_ids
    assert "r2" in flow.robot_ids


def test_plan_and_submit_scheduler_path_writes_task_flow_record(tmp_path):
    from unittest.mock import patch, MagicMock

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    task_flow_store = JsonlTaskFlowRegistryStore(tmp_path / "flows.jsonl")
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        task_flow_store=task_flow_store,
    )

    mock_scheduler = MagicMock()
    mock_scheduler.schedule.return_value = {
        "status": "succeeded",
        "mission_id": "mission-1",
        "group_results": [
            {
                "group_index": 0,
                "subtask_results": [
                    {"robot_id": "r1", "task_id": "task-r1", "status": "accepted"},
                ],
            }
        ],
        "failure_decisions": [],
    }

    with patch('fireclaw_core.mission.mission_scheduler.MissionScheduler', return_value=mock_scheduler):
        result = mission.plan_and_submit("去二楼搜索", session_id="mission-1")

    assert result["status"] == "succeeded"
    flow = task_flow_store.get("mission-1")
    assert flow is not None
    assert flow.mission_id == "mission-1"
    assert flow.command == "去二楼搜索"
    assert flow.status == "running"
    assert "task-r1" in flow.task_ids
    assert "r1" in flow.robot_ids


def test_plan_and_submit_task_flow_not_written_when_store_none(tmp_path):
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    # No task_flow_store
    mission = MissionAgent(registry=registry, subagent_client=client, planner=planner)

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    # No crash, no store to check


def test_primitive_fallback_structured_task_validates_after_round_trip():
    structured_task = {
        "task_id": "m1:r1:primitive-composition",
        "mission_id": "m1",
        "robot_id": "r1",
        "task_type": "primitive_composition",
        "command": "导航到 x=2 y=0",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor", "report_status"],
        "risk_level": "low",
        "constraints": {"source": "mission_primitive_fallback"},
    }

    task = StructuredRobotTask.from_dict(structured_task)

    assert validate_structured_robot_task(task) == []


# --- Task 7: PlannerMemoryContextBuilder integration tests ---


def _seed_record_with_metadata(
    memory_store,
    *,
    record_id: str,
    mission_id: str,
    record_type: str,
    content: dict,
    runtime_mode: str = "simulation",
    sensitivity: str = "standard",
    robot_id=None,
    subtask_id=None,
    created_at=None,
):
    """Seed a mission memory record with embodied metadata for builder admission."""
    from datetime import datetime, timezone
    now = created_at or datetime.now(timezone.utc).isoformat()
    enriched_content = {
        "_embodied": {
            "runtime_mode": runtime_mode,
            "sensitivity": sensitivity,
            "source_type": "test",
        },
        **content,
    }
    memory_store.append(MissionMemoryRecord(
        record_id=record_id,
        mission_id=mission_id,
        record_type=record_type,
        content=enriched_content,
        robot_id=robot_id,
        subtask_id=subtask_id,
        created_at=now,
    ))


def test_retrieve_planner_context_returns_tuple(tmp_path):
    """_retrieve_planner_context() still returns (memories, corrections) tuple."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-1",
        mission_id="mission-1",
        record_type="correction",
        content={"correction": "先确认楼梯安全"},
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_memory=memory_store,
        embodied_runtime_mode="simulation",
    )

    result = agent._retrieve_planner_context("去二楼搜索", mission_id="mission-1")

    assert isinstance(result, tuple)
    assert len(result) == 2
    memories, corrections = result
    assert isinstance(memories, list)
    assert isinstance(corrections, list)


def test_retrieve_planner_context_returns_current_mission_corrections(tmp_path):
    """Current mission/runtime corrections are returned by _retrieve_planner_context."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-1",
        mission_id="current-mission",
        record_type="correction",
        content={"correction": "先确认楼梯安全再上楼"},
    )
    # Seed a record from another mission (should NOT be returned)
    _seed_record_with_metadata(
        memory_store,
        record_id="corr-other",
        mission_id="other-mission",
        record_type="correction",
        content={"correction": "其他任务的纠正"},
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_memory=memory_store,
        embodied_runtime_mode="simulation",
    )

    memories, corrections = agent._retrieve_planner_context(
        "去二楼搜索", mission_id="current-mission",
    )

    assert len(corrections) == 1
    assert corrections[0]["content"]["correction"] == "先确认楼梯安全再上楼"
    # Other mission's correction should be excluded
    assert all(c.get("source_mission_id") == "current-mission" for c in corrections)


def test_builder_source_exception_does_not_block_planning(tmp_path):
    """A Builder source exception does not block a valid mission plan."""
    from fireclaw_core.memory.planner_memory_context import PlannerMemoryContextBuilder

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()

    class FailingMemoryStore:
        def list_records(self, **kwargs):
            raise RuntimeError("store unavailable")

        def search(self, **kwargs):
            raise RuntimeError("store unavailable")

    builder = PlannerMemoryContextBuilder(
        mission_memory=FailingMemoryStore(),
    )
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-24T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
        planner_memory_context_builder=builder,
    )

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    # Planning should succeed despite memory source failure
    assert result["status"] == "planned"
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    # The memory_context decision should have been appended
    assert any(d.layer == "memory_context" for d in record.decisions)
    # Warnings should be present (store failure)
    mem_decision = [d for d in record.decisions if d.layer == "memory_context"][0]
    assert mem_decision.status == "allow"


def test_raw_plugin_enrichment_not_appended_after_builder(tmp_path):
    """Raw plugin enrich_context memory is not appended after Builder validation."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-1",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded"},
    )
    runtime = PluginRuntime()
    # Plugin that injects extra memories via enrich_context
    runtime.register_callable(
        hook_type="provider",
        hook_name="enrich_context",
        plugin_id="fire.inject",
        callback=lambda payload: {"retrieved_memories": [
            {
                "record_id": "injected-memory",
                "mission_id": "fake",
                "record_type": "lesson",
                "content": {"lesson": "inject"},
                "source": "plugin",
            }
        ]},
    )
    plan = MissionPlan(
        intent="search", command="去二楼搜索",
        subtasks=[MissionSubtask(robot_id="r1", command="去2楼搜索", floor=2, capability_required="search_for_victims")],
    )
    planner = FakeMissionPlanner(MissionPlanningResult(status="planned", message="ok", intent="search", plan=plan))
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_memory=memory_store,
        plugin_runtime=runtime,
        embodied_runtime_mode="simulation",
    )

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    ctx = planner.calls[0][1]
    # The injected plugin memory should NOT appear in context
    for mem in ctx.retrieved_memories:
        assert mem.get("record_id") != "injected-memory"


def test_audit_record_receives_memory_context_decision_before_validator(tmp_path):
    """Planner audit record receives exactly one memory_context decision before the validator decision."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-24T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
    )

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    # There should be exactly one memory_context decision
    mem_decisions = [d for d in record.decisions if d.layer == "memory_context"]
    assert len(mem_decisions) == 1
    assert mem_decisions[0].status == "allow"
    # Validator decision should come after memory_context
    assert record.decisions[-1].layer == "validator"
    assert record.decisions[0].layer == "memory_context"
    assert record.decisions[1].layer == "validator"


def test_memory_context_decision_contains_counts_not_content(tmp_path):
    """The memory_context decision contains counts and warning codes, not memory content or exception messages."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()
    memory_store = MissionMemoryStore(tmp_path / "memory.jsonl")
    _seed_record_with_metadata(
        memory_store,
        record_id="mem-1",
        mission_id="mission-1",
        record_type="outcome",
        content={"command": "去二楼搜索", "status": "succeeded"},
    )
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-24T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
        mission_memory=memory_store,
        embodied_runtime_mode="simulation",
    )

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    assert result["status"] == "planned"
    record = audit_sink.records[0]
    mem_decision = [d for d in record.decisions if d.layer == "memory_context"][0]
    details = mem_decision.details
    # Must contain counts
    assert "accepted_memories" in details
    assert "accepted_corrections" in details
    assert "omitted_counts" in details
    assert "warning_codes" in details
    # Must NOT contain memory content or exception messages
    details_str = json.dumps(details, ensure_ascii=False)
    assert "去二楼搜索" not in details_str
    assert "exception" not in details_str.lower() or "exception_class" in details_str


def test_memory_degradation_does_not_block_planning(tmp_path):
    """Memory degradation must NOT block planning."""
    from fireclaw_core.memory.planner_memory_context import PlannerMemoryContextBuilder

    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    audit_sink = FakeAuditSink()

    class FailingRetriever:
        def retrieve(self, query, *, scope=None, limit=10):
            raise RuntimeError("index corrupted")

    builder = PlannerMemoryContextBuilder(
        memory_retriever=FailingRetriever(),
    )
    base_audit = MissionPlanningAuditRecord(
        command="去二楼搜索",
        available_robots=[],
        tool_schema={"type": "function"},
        llm_tool_call={"name": "create_mission_plan", "arguments": {}},
        decisions=[],
        final_status="planned",
        final_message="planned",
        created_at="2026-07-24T00:00:00+00:00",
    )
    planner = MagicMock()
    planner.plan.return_value = MissionPlanningResult(
        status="planned",
        message="planned",
        intent="search",
        plan=MissionPlan(
            intent="search",
            command="去二楼搜索",
            subtasks=[
                MissionSubtask(
                    robot_id="r1",
                    command="去2楼搜索",
                    floor=2,
                    capability_required="search_for_victims",
                )
            ],
        ),
        audit_record=base_audit,
    )
    agent = MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
        mission_planning_audit_sink=audit_sink,
        planner_memory_context_builder=builder,
        embodied_runtime_mode="simulation",
    )

    result = agent.plan_and_submit("去二楼搜索", session_id="mission-1", use_scheduler=False)

    # Planning should succeed despite memory failure
    assert result["status"] == "planned"
    assert len(audit_sink.records) == 1
    record = audit_sink.records[0]
    mem_decision = [d for d in record.decisions if d.layer == "memory_context"][0]
    # Decision is "allow" even though memory is degraded
    assert mem_decision.status == "allow"
    assert mem_decision.reason == "memory_context_degraded"
