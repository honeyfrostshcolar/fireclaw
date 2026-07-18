# FireClaw Robot-Local Agent Design

## Goal

Build the first OpenClaw-style multi-agent FireClaw loop for Gazebo and ROS1: the mission-level agent receives an operator command, delegates a bounded task envelope to a robot-local agent, the robot-local agent calls an LLM for local analysis and planning, then executes approved skills through the existing ROS1/Gazebo adapter.

## Scope

This design targets the first embedded robot-local agent version. The robot-local agent runs inside each robot-local `FireClawGateway` process rather than as a separate service. The existing structured-task execution path remains available as a fallback when robot-local LLM planning is disabled, unavailable, invalid, or rejected by policy.

This design does not attempt to build a fully autonomous firefighting robot. It builds a constrained hierarchical embodied-agent architecture suitable for Gazebo debugging and research evaluation.

## Architecture

The intended runtime chain is:

```text
Operator
  -> MissionGateway / MissionAgent
     -> mission-level planner
     -> StructuredRobotTask envelope
     -> RobotSubagentClient
        -> robot-local FireClawGateway
           -> RobotAgentRuntime
              -> RobotAgentPlanner
              -> RobotAgentPolicy
              -> SafetyGate
              -> PlanExecutor
              -> ROS1/Gazebo adapter
```

The mission-level agent remains responsible for high-level task decomposition and robot assignment. The robot-local agent is responsible for local analysis and bounded local planning under a task envelope supplied by mission control.

The robot-local agent is inserted into `FireClawGateway._execute_agent_task()` as an optional path for structured tasks. If robot-local agent mode is disabled, the gateway keeps using the current `planning_result_from_structured_task()` behavior.

## OpenClaw Analogue

The design mirrors OpenClaw's proven split between a main session/agent and delegated subagent work:

- main agent owns high-level user intent and dispatch;
- subagent work is tracked as a run with lifecycle state;
- tool execution remains behind a control boundary;
- events and task-flow records make delegated execution auditable;
- fallback behavior preserves progress when model-driven execution fails.

FireClaw adapts this for robotics by adding a bounded task envelope, ROS adapter execution, physical-safety checks, and explicit policy validation before execution.

## Task Envelope

`StructuredRobotTask` remains the wire payload accepted by the robot-local gateway. The robot-local runtime converts it into a stricter internal envelope:

```python
@dataclass(frozen=True)
class RobotAgentTaskEnvelope:
    task_id: str
    mission_id: str | None
    robot_id: str
    command: str | None
    task_type: str
    target: dict[str, Any]
    allowed_skills: list[str]
    required_skills: list[str]
    constraints: dict[str, Any]
    risk_level: str
    operator_id: str | None
```

`required_skills` is the mission-level minimum execution chain. `allowed_skills` is the robot-local LLM's planning boundary. For the first version:

```text
allowed_skills = required_skills + ["report_status", "return_to_safe_zone"]
```

High-risk system skills such as `emergency_stop` are not planned by the LLM in v1. They remain available to system safety logic.

## Robot-Local Plan

The robot-local planner returns a local plan:

```python
@dataclass(frozen=True)
class RobotLocalPlan:
    intent: str
    steps: list[RobotLocalPlanStep]
    rationale: str | None
    confidence: float | None
```

```python
@dataclass(frozen=True)
class RobotLocalPlanStep:
    skill_name: str
    inputs: dict[str, Any]
    reason: str | None = None
```

The LLM must produce structured output. The implementation should use an OpenAI-compatible tool schema or JSON schema rather than free-form text parsing.

## Policy Boundary

The first version uses constrained autonomy. The robot-local LLM may reorder or supplement low-risk steps inside the envelope, but it may not expand mission authority.

`RobotAgentPolicy.validate()` enforces:

1. Every planned `skill_name` must be in `allowed_skills`.
2. Floor-bearing skills must use `envelope.target["floor"]`.
3. The plan must include required safety/reporting skills from `required_skills` when they are present.
4. `risk_level` values of `high` or `critical` may produce a proposed plan, but may not execute directly in v1.
5. Invalid plans must emit a policy event and fall back to the mission-level `required_skills` plan.

SafetyGate remains authoritative after policy validation. A SafetyGate block, confirmation requirement, or escalation must not be bypassed by fallback.

## Gateway Configuration

`GatewayConfig` should gain robot-local agent options:

```python
robot_agent_enabled: bool = False
robot_agent_planner: str = "deterministic"
robot_agent_provider_base_url: str | None = None
robot_agent_provider_api_key: str | None = None
robot_agent_model: str | None = None
robot_agent_llm_trace_path: str | None = None
```

CLI shape:

```bash
python -m fireclaw_core.gateway \
  --adapter ros1 \
  --real-run \
  --robot-id gazebo_turtlebot3 \
  --ros1-config examples/ros1_configs/gazebo_turtlebot3_move_base.yaml \
  --robot-agent \
  --robot-agent-planner llm \
  --robot-agent-provider-base-url "$OPENAI_BASE_URL" \
  --robot-agent-provider-api-key "$OPENAI_API_KEY" \
  --robot-agent-model "$MODEL"
```

## Execution Flow

```text
1. MissionAgent generates StructuredRobotTask.
2. RobotSubagentClient posts it to the robot-local FireClawGateway.
3. FireClawGateway receives the structured task.
4. If robot_agent_enabled=false, it uses the existing structured-task execution path.
5. If robot_agent_enabled=true:
   - convert StructuredRobotTask to RobotAgentTaskEnvelope;
   - collect robot state, environment state, skills, and recent local context;
   - call RobotAgentPlanner;
   - validate the resulting RobotLocalPlan with RobotAgentPolicy;
   - convert accepted plan to PlanningResult;
   - fall back to required_skills when the model fails or policy rejects the plan.
6. SafetyGate evaluates the resulting plan.
7. PlanExecutor executes approved skills.
8. ROS1 adapter calls Gazebo topics, services, and actions.
9. Gateway records terminal task events and trace data.
```

## Error Handling

LLM timeout or provider API error:

```text
emit robot_agent.plan_failed
fallback to required_skills
```

Missing tool call, malformed JSON, or invalid local plan:

```text
emit robot_agent.plan_invalid
fallback to required_skills
```

Policy rejection, including out-of-envelope skills or target mutation:

```text
emit robot_agent.policy_rejected
fallback to required_skills
```

SafetyGate block or confirmation requirement:

```text
return blocked / needs_confirmation
do not fallback around SafetyGate
```

ROS1 action server unavailable or action timeout:

```text
task.failed
include ROS action name and timeout in the error payload
```

Task cancellation:

```text
cancellation_requested propagates through LLM wait boundaries and PlanExecutor
existing gateway cancellation semantics remain authoritative
```

## Events and Auditability

Robot-local agent mode should emit explicit events:

```text
robot_agent.plan_requested
robot_agent.plan_accepted
robot_agent.plan_failed
robot_agent.plan_invalid
robot_agent.policy_rejected
robot_agent.fallback_used
```

Events should include `task_id`, `mission_id`, `robot_id`, planner type, selected model when available, and sanitized plan metadata. Secrets and raw API keys must never be logged.

LLM trace should go to the configured trace path and must be optional.

## Tests

Unit tests:

- `StructuredRobotTask -> RobotAgentTaskEnvelope`
- robot-local LLM response parsing
- policy rejection for out-of-envelope skills
- policy rejection for mutated floor targets
- policy rejection or proposal-only behavior for high/critical risk
- fallback behavior on model timeout, API error, malformed output, or policy rejection

Gateway integration tests:

- `FireClawGateway(adapter="simulator", robot_agent_enabled=True)` executes a valid robot-local LLM plan.
- Invalid robot-local LLM plan falls back to `required_skills`.
- Policy rejection emits `robot_agent.policy_rejected` and `robot_agent.fallback_used`.
- SafetyGate blocks are not bypassed by fallback.
- Cancellation still reaches active execution.

Gazebo smoke validation:

- not part of the default pytest suite;
- runs against ROS1 Noetic and Gazebo;
- verifies `operator command -> MissionGateway -> robot-local RobotAgent LLM -> ROS1 move_base -> terminal event -> proof bundle`.

## Research Framing

The research claim should be conservative:

```text
A safety-bounded hierarchical embodied-agent architecture for firefighting robots,
where mission-level planning delegates constrained local planning to robot-side agents
and verifies policy compliance before ROS/Gazebo execution.
```

Suggested ablations:

- mission-level planning only vs mission-level plus robot-local LLM planning;
- no policy gate vs constrained policy gate;
- no local state/context vs robot-local context;
- deterministic fallback vs LLM local planner.

The first version is suitable for Gazebo evidence and architecture evaluation. It is not sufficient by itself for real firefighting deployment claims.

## Non-Goals

- no real hardware claim;
- no unbounded robot-local autonomy;
- no automatic emergency or high-risk LLM action execution;
- no separate robot-agent service process in v1;
- no UI work beyond CLI flags and logs.

