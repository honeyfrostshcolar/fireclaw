from pathlib import Path
from unittest.mock import MagicMock

from fireclaw_core.agent.computer_tools import (
    ComputerSandbox,
    register_computer_tool_plugin,
)
from fireclaw_core.agent.robot_deliberation import (
    LLMRobotAgentDecisionPolicy,
    RobotAgentDecision,
    RobotAgentDeliberationRuntime,
)
from fireclaw_core.agent.tool_runtime import AgentToolRuntime
from fireclaw_core.agent.robot_registry import (
    RobotRegistry,
    RobotRegistryEntry,
)
from fireclaw_core.mission.mission_deliberation import (
    MissionDeliberationRuntime,
)
from fireclaw_core.mission.mission_planner import MissionPlannerContext
from fireclaw_core.mission.mission_state import MissionStateSnapshotBuilder
from fireclaw_core.planner.llm_planner import LLMMissionPlanner
from fireclaw_core.plugin.plugin_host import FireClawPluginHost
from fireclaw_core.policy.deployment import (
    DeploymentProfile,
    SandboxProfile,
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

    def chat_completion(self, **kwargs):
        return self.response

    def status(self):
        return {"type": "fake", "model": "fake-model"}


def _completion(name: str, arguments: dict) -> ChatCompletion:
    return ChatCompletion(
        content=None,
        tool_calls=[
            ToolCall(id=f"call-{name}", name=name, arguments=arguments)
        ],
        usage=TokenUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        ),
        model="test-model",
        finish_reason="tool_calls",
    )


def _profile(tmp_path: Path, *, role: str) -> DeploymentProfile:
    return DeploymentProfile(
        mode="simulation",
        role=role,
        sandbox=SandboxProfile(
            enabled=True,
            workspace_root=tmp_path / role,
            image="fireclaw-sim:test",
        ),
    )


def test_robot_llm_policy_maps_projected_agent_tool_to_separate_operation(
    tmp_path: Path,
) -> None:
    policy = LLMRobotAgentDecisionPolicy(
        FakeProviderRuntime(
            _completion("computer_read_file", {"path": "notes.txt"})
        )
    )
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="inspect",
        command="查看诊断记录",
        target={},
        required_skills=[],
        allowed_skills=[],
        risk_level="low",
    )
    from fireclaw_core.agent.robot_agent import (
        envelope_from_structured_task,
    )
    from fireclaw_core.agent.robot_deliberation import (
        RobotAgentDeliberationRequest,
    )

    decision = policy.decide(
        RobotAgentDeliberationRequest(
            envelope=envelope_from_structured_task(
                task,
                fallback_robot_id="robot-1",
            ),
            iteration=1,
            remaining_iterations=2,
            context={
                "agent_tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "computer_read_file",
                            "description": "Read a sandbox file.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"}
                                },
                                "required": ["path"],
                            },
                        },
                    }
                ],
                "agent_tool_policy": {
                    "tools": [
                        {
                            "name": "computer_read_file",
                            "effect": "read",
                        }
                    ]
                },
            },
            observations=(),
        )
    )

    assert decision.operation == "execute_agent_tool"
    assert decision.tool_name == "computer_read_file"
    assert decision.tool_effect == "read"


def test_robot_runtime_returns_agent_tool_output_as_advisory() -> None:
    policy = SequencePolicy([
        RobotAgentDecision(
            operation="execute_agent_tool",
            message="inspect diagnostics",
            tool_name="computer_read_file",
            inputs={"path": "notes.txt"},
            tool_effect="read",
        ),
        RobotAgentDecision(
            operation="blocked",
            message="diagnostic-only test finished",
            reason_code="test_finished",
        ),
    ])
    task = StructuredRobotTask(
        task_id="task-1",
        mission_id="mission-1",
        robot_id="robot-1",
        task_type="inspect",
        command="查看诊断记录",
        target={},
        required_skills=[],
        allowed_skills=[],
        risk_level="low",
    )

    result = RobotAgentDeliberationRuntime(policy=policy).run(
        task,
        fallback_robot_id="robot-1",
        context_provider=lambda: {},
        execute_skill=lambda step: {},
        execute_agent_tool=lambda name, arguments: {
            "status": "executed",
            "output": {"content": "diagnostic"},
        },
    )

    assert result.status == "blocked"
    observation = policy.requests[1].observations[0]
    assert observation.operation == "execute_agent_tool"
    assert observation.authoritative is False
    assert observation.output["output"]["content"] == "diagnostic"


def test_mission_react_loop_receives_computer_output_as_advisory(
    tmp_path: Path,
) -> None:
    registry = RobotRegistry([
        RobotRegistryEntry(
            robot_id="robot-a",
            base_url="http://robot-a.test",
            capabilities=("search_for_victims",),
        )
    ])
    snapshot = MissionStateSnapshotBuilder(registry=registry).build(
        mission_id="mission-1",
        presence={
            "robot-a": {
                "online": True,
                "last_seen_at": "2026-07-30T01:00:00+00:00",
                "state": {},
            }
        },
        captured_at="2026-07-30T01:00:01+00:00",
    )
    profile = _profile(tmp_path, role="mission_agent")
    sandbox = ComputerSandbox(profile.sandbox)
    (sandbox.root / "notes.txt").write_text(
        "west corridor diagnostic",
        encoding="utf-8",
    )
    host = FireClawPluginHost()
    register_computer_tool_plugin(host, sandbox)
    tool_runtime = AgentToolRuntime(
        plugin_host=host,
        profile=profile,
    )
    provider = MagicMock()
    provider.chat_completion.side_effect = [
        _completion("computer_read_file", {"path": "notes.txt"}),
        _completion(
            "propose_plan",
            {
                "intent": "search",
                "subtasks": [
                        {
                            "robot_id": "robot-a",
                            "command": "搜索目标区域",
                            "floor": 1,
                            "target": {
                                "frame_id": "map",
                                "pose": {"x": 1.0, "y": 2.0, "yaw": 0.0},
                        },
                        "capability_required": "search_for_victims",
                        "execution_group": 0,
                    }
                ],
                "knowledge_refs": [],
            },
        ),
    ]
    planner = LLMMissionPlanner(
        provider=provider,
        model_id="test-model",
        plugin_host=host,
        agent_tool_runtime=tool_runtime,
    )
    runtime = MissionDeliberationRuntime(
        registry=registry,
        policy=planner,
        agent_tool_runtime=tool_runtime,
    )

    result = runtime.deliberate(
        mission_id="mission-1",
        command="搜索坐标 (1.0, 2.0)",
        state_snapshot=snapshot,
        planner_context=MissionPlannerContext(
            available_robots=registry.enabled_entries(),
            state_snapshot=snapshot.to_dict(),
        ),
    )

    assert result.status == "proposed", result.to_dict()
    assert result.observations[0].authoritative is False
    assert result.observations[0].kind == "agent_tool:computer_read_file"
    second_call = provider.chat_completion.call_args_list[1]
    import json

    payload = json.loads(
        second_call.kwargs["messages"][1]["content"]
    )["planning_context"]
    assert payload["authoritative"]["observations"] == []
    advisory = payload["advisory"]["agent_tool_observations"]
    assert advisory[0]["data"]["output"]["content"] == (
        "west corridor diagnostic"
    )
