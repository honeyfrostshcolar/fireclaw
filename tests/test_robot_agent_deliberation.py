from __future__ import annotations

import pytest

from fireclaw_core.agent.robot_deliberation import (
    LLMRobotAgentDecisionPolicy,
    RobotAgentDecision,
    RobotAgentDeliberationLimits,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.agent.robot_agent import (
    RobotAgentPlannerError,
    RobotAgentTaskEnvelope,
)
from fireclaw_core.agent.loop_checkpoint import (
    JsonlAgentLoopCheckpointStore,
)
from fireclaw_core.provider.provider import (
    ChatCompletion,
    TokenUsage,
    ToolCall,
)
from fireclaw_core.task.task_contract import StructuredRobotTask


class SequencePolicy:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return self.decisions.pop(0)


class FakeProviderRuntime:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def chat_completion(
        self,
        *,
        messages,
        tools,
        temperature=0.0,
        max_tokens=4096,
    ):
        self.calls.append({"messages": messages, "tools": tools})
        return self.response

    def status(self):
        return {"type": "fake", "model": "fake-model"}


def _task(*, risk_level="low"):
    return StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="search",
        command="去二楼搜索",
        target={"floor": 2},
        required_skills=["navigate_to_waypoint", "victim_search"],
        risk_level=risk_level,
    )


def _success(step):
    return {
        "status": "completed",
        "safety": {"status": "allow"},
        "execution": {
            "status": "succeeded",
            "steps": [
                {
                    "skill_name": step.skill_name,
                    "status": "succeeded",
                    "output": {"floor": 2},
                }
            ],
        },
    }


def _single_skill_task():
    return StructuredRobotTask(
        task_id="task-resume",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="navigate",
        command="去二楼",
        target={"floor": 2},
        required_skills=["navigate_to_waypoint"],
        risk_level="low",
    )


def test_robot_deliberation_returns_skill_results_to_policy():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="navigate",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(
                operation="execute_skill",
                message="search",
                tool_name="victim_search",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(
                operation="complete",
                message="victim search completed",
            ),
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {"robot_state": {"current_floor": 2}},
        execute_skill=_success,
    )

    assert result.status == "completed"
    assert policy.requests[1].observations[0].tool_name == "navigate_to_waypoint"
    assert policy.requests[1].observations[0].status == "succeeded"
    assert result.result["succeeded_skills"] == [
        "navigate_to_waypoint",
        "victim_search",
    ]


def test_robot_deliberation_can_recover_after_failed_skill():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="try navigation",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(
                operation="execute_skill",
                message="retry after refreshed state",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(
                operation="execute_skill",
                message="search",
                tool_name="victim_search",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(operation="complete", message="done"),
        ]
    )
    calls = 0

    def execute(step):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "status": "failed",
                "safety": {"status": "allow"},
                "execution": {
                    "status": "failed",
                    "steps": [
                        {
                            "skill_name": step.skill_name,
                            "status": "failed",
                            "failure_category": "target_unreachable",
                        }
                    ],
                },
            }
        return _success(step)

    result = RobotAgentDeliberationRuntime(
        policy=policy,
        limits=RobotAgentDeliberationLimits(
            max_iterations=5,
            timeout_seconds=30,
            max_skill_executions=4,
        ),
    ).run(
        _task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=execute,
    )

    assert result.status == "completed"
    assert policy.requests[1].observations[0].status == "failed"
    assert policy.requests[1].observations[0].reason_code == "target_unreachable"


def test_robot_deliberation_escalation_preserves_evidence_references():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="escalate",
                message="Navigation remains stalled after bounded diagnostics.",
                reason_code="persistent_navigation_stall",
                evidence_ids=("ros1:diagnostic:123", "action:timed-out:456"),
            )
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _single_skill_task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=_success,
    )

    assert result.status == "escalated"
    assert result.reason_code == "persistent_navigation_stall"
    assert result.result == {
        "status": "escalated",
        "reason_code": "persistent_navigation_stall",
        "evidence_ids": [
            "ros1:diagnostic:123",
            "action:timed-out:456",
        ],
    }


def test_robot_deliberation_rejects_premature_completion():
    policy = SequencePolicy(
        [
            RobotAgentDecision(operation="complete", message="done"),
            RobotAgentDecision(
                operation="blocked",
                message="required work is incomplete",
                reason_code="incomplete",
            ),
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=_success,
    )

    assert result.status == "blocked"
    assert policy.requests[1].observations[0].reason_code == (
        "required_skills_incomplete"
    )


def test_robot_deliberation_blocks_skill_outside_task_envelope():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="unsafe expansion",
                tool_name="deploy_hose",
                inputs={},
            )
        ]
    )
    task = _task()
    task = StructuredRobotTask(
        **{
            **task.to_dict(),
            "allowed_skills": ["navigate_to_waypoint", "victim_search"],
        }
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        task,
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=_success,
    )

    assert result.status == "blocked"
    assert result.reason_code == "action_policy_rejected"


def test_robot_deliberation_escalates_high_risk_action():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="navigate",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            )
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _task(risk_level="high"),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=_success,
    )

    assert result.status == "escalated"
    assert result.reason_code == "action_policy_rejected"


def test_robot_deliberation_cannot_reason_past_safety_gate_block():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="navigate",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            ),
            RobotAgentDecision(
                operation="execute_skill",
                message="try another action",
                tool_name="victim_search",
                inputs={"floor": 2},
            ),
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=lambda step: {
            "status": "block",
            "safety": {"status": "block"},
            "execution": None,
        },
    )

    assert result.status == "blocked"
    assert result.reason_code == "safety_gate_terminal"
    assert len(policy.requests) == 1


def test_robot_deliberation_preserves_physical_confirmation_boundary():
    policy = SequencePolicy(
        [
            RobotAgentDecision(
                operation="execute_skill",
                message="navigate",
                tool_name="navigate_to_waypoint",
                inputs={"floor": 2},
            )
        ]
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        _single_skill_task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=lambda step: {
            "status": "awaiting_confirmation",
            "safety": {"status": "require_confirmation"},
            "execution": {"status": "failed", "steps": []},
        },
    )

    assert result.status == "escalated"
    assert result.reason_code == "safety_gate_terminal"
    assert result.observations[-1].status == "approval_required"
    assert result.observations[-1].output["status"] == (
        "awaiting_confirmation"
    )
    assert len(policy.requests) == 1


def test_robot_deliberation_reconciles_finished_skill_without_replay(
    tmp_path,
):
    store = JsonlAgentLoopCheckpointStore(tmp_path / "robot-loop.jsonl")
    dispatched = {}

    def dispatch_then_lose_process(step):
        dispatched[step.operation_id] = _success(step)
        raise SystemExit("simulated robot process loss")

    with pytest.raises(SystemExit, match="simulated robot process loss"):
        RobotAgentDeliberationRuntime(
            policy=SequencePolicy([
                RobotAgentDecision(
                    operation="execute_skill",
                    message="navigate",
                    tool_name="navigate_to_waypoint",
                    inputs={"floor": 2},
                ),
            ]),
            checkpoint_store=store,
        ).run(
            _single_skill_task(),
            fallback_robot_id="robot-1",
            context_provider=lambda: {},
            execute_skill=dispatch_then_lose_process,
        )

    replayed = []
    result = RobotAgentDeliberationRuntime(
        policy=SequencePolicy([
            RobotAgentDecision(operation="complete", message="done"),
        ]),
        checkpoint_store=store,
    ).run(
        _single_skill_task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=lambda step: replayed.append(step),
        reconcile_skill=lambda operation_id, step: dispatched[operation_id],
    )

    assert result.status == "completed"
    assert replayed == []
    assert result.result["succeeded_skills"] == ["navigate_to_waypoint"]
    assert result.observations[0].status == "succeeded"
    assert store.latest("robot:robot-1:task:task-resume").status == (
        "completed"
    )


def test_robot_deliberation_escalates_unknown_physical_outcome(
    tmp_path,
):
    store = JsonlAgentLoopCheckpointStore(tmp_path / "robot-loop.jsonl")

    with pytest.raises(SystemExit):
        RobotAgentDeliberationRuntime(
            policy=SequencePolicy([
                RobotAgentDecision(
                    operation="execute_skill",
                    message="navigate",
                    tool_name="navigate_to_waypoint",
                    inputs={"floor": 2},
                ),
            ]),
            checkpoint_store=store,
        ).run(
            _single_skill_task(),
            fallback_robot_id="robot-1",
            context_provider=lambda: {},
            execute_skill=lambda step: (_ for _ in ()).throw(
                SystemExit("lost")
            ),
        )

    replayed = []
    policy = SequencePolicy([
        RobotAgentDecision(operation="complete", message="must not decide"),
    ])
    result = RobotAgentDeliberationRuntime(
        policy=policy,
        checkpoint_store=store,
    ).run(
        _single_skill_task(),
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=lambda step: replayed.append(step),
        reconcile_skill=lambda operation_id, step: {
            "reconciliation_status": "unknown",
        },
    )

    assert result.status == "escalated"
    assert result.reason_code == "physical_action_outcome_unknown"
    assert replayed == []
    assert policy.requests == []
    checkpoint = store.latest("robot:robot-1:task:task-resume")
    assert checkpoint.is_recoverable
    assert checkpoint.pending_operation is not None


def test_llm_robot_agent_policy_returns_one_skill_decision():
    runtime = FakeProviderRuntime(
        ChatCompletion(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="navigate_to_waypoint",
                    arguments={"floor": 2},
                )
            ],
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="tool_calls",
        )
    )
    request = _policy_request()
    request.context["skill_tools"] = [
        {
            "type": "function",
            "function": {
                "name": "navigate_to_waypoint",
                "description": "Navigate.",
                "parameters": {
                    "type": "object",
                    "properties": {"floor": {"type": "integer"}},
                    "required": ["floor"],
                },
            },
        }
    ]

    decision = LLMRobotAgentDecisionPolicy(runtime).decide(request)

    assert decision.operation == "execute_skill"
    assert decision.tool_name == "navigate_to_waypoint"
    assert decision.inputs == {"floor": 2}
    tool_names = [
        item["function"]["name"] for item in runtime.calls[0]["tools"]
    ]
    assert "complete_robot_task" in tool_names
    assert "report_robot_task_blocked" in tool_names


def test_llm_robot_agent_policy_rejects_multiple_operations_in_one_turn():
    runtime = FakeProviderRuntime(
        ChatCompletion(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="navigate_to_waypoint",
                    arguments={"floor": 2},
                ),
                ToolCall(
                    id="call-2",
                    name="complete_robot_task",
                    arguments={"message": "done"},
                ),
            ],
            usage=TokenUsage(1, 1, 2),
            model="fake-model",
            finish_reason="tool_calls",
        )
    )
    request = _policy_request()
    request.context["skill_tools"] = [
        {
            "type": "function",
            "function": {
                "name": "navigate_to_waypoint",
                "description": "Navigate.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    with pytest.raises(RobotAgentPlannerError, match="exactly one"):
        LLMRobotAgentDecisionPolicy(runtime).decide(request)


def _policy_request():
    from fireclaw_core.agent.robot_deliberation import (
        RobotAgentDeliberationRequest,
    )

    return RobotAgentDeliberationRequest(
        envelope=RobotAgentTaskEnvelope(
            task_id="task-1",
            mission_id="mission-1",
            robot_id="robot-1",
            command="去二楼搜索",
            task_type="search",
            target={"floor": 2},
            allowed_skills=[
                "navigate_to_waypoint",
                "victim_search",
            ],
            required_skills=[
                "navigate_to_waypoint",
                "victim_search",
            ],
            constraints={},
            risk_level="low",
            operator_id=None,
        ),
        iteration=1,
        remaining_iterations=5,
        context={
            "robot_state": {"current_floor": 1},
            "environment_state": {},
        },
        observations=(),
    )
