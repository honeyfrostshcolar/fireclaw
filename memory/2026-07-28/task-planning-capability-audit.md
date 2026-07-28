# Task Planning Capability Audit

## 2026-07-28T15:49:04+08:00

### Task goal

Assess FireClaw's current task-planning capability as an embodied-agent
framework, compare the relevant execution model with the local OpenClaw
reference, and identify the highest-value architectural work. No production
code change was requested in this investigation.

### Repository state

- Branch: `master`, synchronized with `origin/master` before this audit.
- Existing unrelated untracked record:
  `memory/2026-07-28/codex-usage-reset-skill-check.md`.
- This audit record is also untracked until a later intentional commit.
- CodeGraph status: current FireClaw index was up to date, but it indexed the
  Python FireClaw tree only. The local OpenClaw reference is at `openclaw/`
  and was not included in that FireClaw CodeGraph index. OpenClaw source was
  therefore inspected through its own index after reading the scoped
  `AGENTS.md` files.

### Evidence inspected

FireClaw, via CodeGraph:

- `src/fireclaw_core/mission/mission_planner.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `src/fireclaw_core/mission/mission_agent.py:784` (`plan_and_submit`)
- `src/fireclaw_core/mission/mission_plan_validator.py`
- `src/fireclaw_core/mission/mission_scheduler.py:66` (`schedule`)
- `src/fireclaw_core/mission/mission_scheduler.py:220`
  (`_evaluate_group_failures`)
- `src/fireclaw_core/agent/robot_agent.py:252`
  (`LLMRobotAgentPlanner.plan`)
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/task/task_contract.py`
- `docs/superpowers/specs/2026-06-02-llm-tool-calling-planner-interface-design.md`

OpenClaw reference sources inspected:

- `openclaw/src/agents/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run-loop.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt-dispatch-preparation.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt-prompt-assembly.ts`
- `openclaw/src/agents/agent-tool-definition-adapter.ts`
- `openclaw/src/agents/agent-tools.before-tool-call.ts`

### What FireClaw can actually do now

1. Mission-level command intake and constrained task allocation.
   - `MissionPlanner` supports five keyword intents: search, patrol,
     firefight, recon, and transport.
   - It extracts floors from Chinese commands, matches a single required
     capability, then assigns enabled capable robots round-robin.
   - `execution_group` provides only a linear batch barrier: same group may
     run in parallel; later integer groups run after earlier ones.

2. One-shot LLM mission-plan generation.
   - `LLMMissionPlanner` exposes exactly one output function:
     `create_mission_plan`.
   - The output schema has only `intent`, plus a flat list of
     `robot_id`, `command`, `floor`, `capability_required`, and
     `execution_group`.
   - It can receive scoped mission memory, operator corrections, fleet
     availability, and advisory external knowledge. These are injected into a
     prompt, not exposed as iterative planning tools.

3. Deterministic dispatch controls.
   - `MissionAgent.plan_and_submit` checks authorization and live robot
     presence, builds the memory context, generates a plan, validates it,
     audits it, records lineage, and submits each subtask.
   - `MissionPlanValidator` checks robot registration/enabled status,
     declared capability, positive floor and group identifiers, and the basic
     structured-task contract.

4. Batch scheduling with fixed failure policies.
   - `MissionScheduler` polls group terminal states and can retry the same
     task, reassign the identical subtask to another robot, skip, escalate, or
     abort.
   - Failure handling preserves the original command, floor, capability, and
     execution group. It does not obtain an observation, change the task
     decomposition, insert sensing, alter routes, or call the planner again.

5. Bounded robot-local plan synthesis.
   - `LLMRobotAgentPlanner` can produce a list of allowed robot skills or
     allowed direct skill calls under a `RobotAgentTaskEnvelope`.
   - It allows at most an optional read-only entity-memory tool round followed
     by one plan/action round. The resulting local plan is policy-checked and
     then executed by the existing safety/execution path.
   - Robot profiles can declare enabled skills, LLM-exposed skills, primitive
     skills, and fixed capability-to-skill chains.

6. Strengths worth preserving.
   - Least-privilege task envelopes, deterministic validation, SafetyGate
     separation, operator approval, audit records, task lineage, and memory
     authority boundaries are materially stronger than an unconstrained
     chatbot-to-robot path.

### Critical gaps

The mission plan is not an executable task graph. `MissionSubtask` lacks:

- stable subtask IDs and explicit dependency edges;
- preconditions, effects, invariants, success predicates, and evidence
  requirements;
- targets beyond an integer floor, map frame, pose, region, object/entity, or
  victim identity;
- resource claims, battery/time budgets, deadlines, travel costs, collision or
  communication constraints;
- contingency branches, recovery policies tied to failure causes, and a
  replan budget;
- revision number, invalidation reason, and causal link to the observation
  which changed the plan.

The system has no central agentic action loop:

- the mission LLM makes one tool call and is not allowed to query current
  robot state, sensor evidence, maps, task status, or skill results before
  committing the plan;
- the local robot LLM can make only the special memory-query round, not a
  general `observe -> act -> result -> reason -> next action` loop;
- direct skill calls are converted into a static plan and executed later,
  rather than their results being returned to the model for the next decision;
- scheduler retry/reassign is state-agnostic and may be unsuitable after a
  blocked route, fire spread, sensor fault, victim found, or partial success.

The present allocation method is only capability filtering plus round-robin.
It has no world-state feasibility check, bidding/cost model, robot pose and
battery awareness, priority/deadline handling, shared-resource reservation,
or coordination semantics other than group numbers.

The current LLM planner also makes its plan schema the product boundary. This
prevents it from expressing the important robotics decisions that should be
validated independently of model text. Adding more prose to the prompt or
adding more intent keywords would not solve this architectural limitation.

### OpenClaw comparison

OpenClaw should not be copied wholesale: its tool model, session/chat
assumptions, and permissive software-tool workflows are not safe defaults for
firefighting robots. But its relevant reusable shape is substantially more
mature:

- `run-loop.ts` owns a bounded `while (true)` attempt loop, terminal handling,
  retries, failure/fallback controls, and context compaction integration.
- Each attempt prepares a session-aware prompt and tool catalogue, dispatches
  the model, then normalizes and recovers/continues based on the result.
- Tool adaptation and `before_tool_call` policy are explicit boundaries:
  tool execution is wrapped, approved/blocked/adjusted, observed, and its
  result is part of the continuing agent run.
- Prompt assembly treats session history, hooks, steering, provenance and
  context-cache stability as first-class runtime inputs instead of a small
  ad-hoc memory excerpt.

The FireClaw analogue should be a bounded, stateful *mission deliberation
loop*, not an OpenClaw clone. Its loop must use sensor snapshots and skill
outcomes as authoritative evidence, enforce physical safety and emergency
stop behavior outside the model, and replan only from validated state
transitions.

### Recommended architecture order

1. Replace the flat `MissionPlan` contract with a typed mission task graph.
   Add node IDs, dependencies, typed target/frame, preconditions, expected
   effects, success/evidence predicates, risk/resource constraints, and
   controlled contingency edges. Keep the existing envelope and SafetyGate as
   enforcement layers, not planner-internal prompts.

2. Introduce a mission-state/evidence projection boundary.
   It should read robot status, task traces, sensor observations, maps and
   reservations into a versioned `MissionStateSnapshot`; raw memory and RAG
   remain advisory unless corroborated by current evidence.

3. Build a bounded `MissionDeliberationRuntime` inspired by OpenClaw's loop.
   Its allowed operations should be typed read tools (fleet state, map,
   sensor/evidence, task status, skill catalogue) plus controlled decisions
   (`propose_plan`, `revise_plan`, `request_clarification`, `escalate`). It
   must enforce max iterations/time/cost, cancellation, trace persistence and
   revision causality.

4. Make execution event-driven and replan-safe.
   Define which state changes invalidate a plan; emit a typed failure/partial
   success observation; then permit a fresh validated revision. Retain
   retry/reassign only for idempotent, explicitly declared recovery cases.

5. Upgrade multi-robot allocation after graph semantics exist.
   Start with deterministic feasibility and cost-aware allocation (capability,
   current pose/zone, battery, ETA, availability, communication health), then
   add reservation/conflict checks. Do not ask an LLM to solve this directly.

6. Keep the robot-local agent bounded.
   It should execute each approved graph node with a small observe-act loop,
   return typed evidence/results, and never expand the mission target or
   authority. This is the correct place for local reactive recovery.

### Research assessment

Engineering correctness: existing planning works for narrow demonstrations
such as assigning search/firefighting/patrol commands by floor and executing
predefined skill chains. It is not yet reliable for a dynamic rescue mission.

Research validity: a mission task graph plus evidence-triggered, bounded
hierarchical replanning is a defensible robotics-agent problem. Merely adding
an LLM plan call, RAG, or a generic tool loop is not novel.

Publication-level contribution: potentially viable only with a formal safety
and evidence model, a dynamic-fire simulator benchmark, ablations against
static plans/retry-only/reactive baselines, and measurements for success,
time, unsafe-action blocks, plan revision quality, unnecessary replans and
operator interventions. Real-robot evidence remains necessary for claims
about physical robustness.

### Next recommended step

Before implementation, write a focused design for the mission task graph and
the deliberation/runtime ownership boundary. It must explicitly state why the
OpenClaw loop shape is reused, why its chat/tool assumptions are not, and how
SafetyGate, robot adapters, task envelopes, scheduler, and audit/memory data
interact on every plan revision. Then implement the typed graph and
deterministic validator first, before connecting any LLM loop.

## 2026-07-28T16:04:38+08:00 - Mission Task Graph Foundation Implemented

### User decision

After a concrete before/after explanation, the user explicitly directed this foundation to be implemented. This is not a complete re-planning agent; it establishes the safe plan representation and validation boundary that later re-planning needs.

### Files modified

- `src/fireclaw_core/mission/task_graph.py` (new): adds `MissionTaskGraph`, task nodes, targets, conditions, evidence requirements, a deterministic graph validator, and legacy-plan projection.
- `src/fireclaw_core/mission/mission_plan_validator.py`: retains all legacy validation and validates the projected or supplied task graph.
- `src/fireclaw_core/mission/mission_agent.py`: creates one graph per accepted plan, validates it, writes it in the plan cognitive artifact, and returns it as `task_graph`.
- `tests/test_mission_task_graph.py` (new): covers dependencies, high-risk evidence, revision provenance, resource conflicts, ordered reuse, and agent output integration.

### Contract introduced

`MissionTaskGraph` carries plan/mission identity, intent, command, revision, state-snapshot reference, superseded-plan reference, invalidation evidence, knowledge references, and nodes. Every node has an ID, robot, capability, target frame/floor/area/entity/pose, dependencies, preconditions, effects, completion evidence, risk, exclusive resources, timeout and recovery policy.

Legacy `execution_group` semantics are preserved: every node in group N depends on all earlier groups. A revision above one must cite a state snapshot, a superseded plan and invalidation evidence. High/critical risk nodes require `sensor_observation` evidence. Concurrent nodes claiming the same exclusive resource are blocked unless a dependency orders them.

### OpenClaw adaptation

OpenClaw contributes the shape of a bounded, traceable loop with explicit tool policy and outcomes. FireClaw cannot safely add that loop before it has a physical, independently validated plan representation. This graph therefore models targets, evidence, resources and recovery explicitly rather than delegating them to prompt text. Existing task envelopes, structured-task validation, SafetyGate and scheduler remain authoritative.

### Verification

Using `/home/lpp/miniconda3/envs/py310/bin/python3.10`:

```text
tests/test_mission_task_graph.py
tests/test_mission_plan_validator.py
tests/test_mission_planner.py
tests/test_mission_scheduler.py
tests/test_mission_agent.py
108 passed in 0.45s
```

`compileall` over the changed mission modules and tests passed. `git diff --check` passed. Gateway/CLI regression first had 49 sandbox-only failures, all `PermissionError` while creating localhost sockets; no task-graph assertion failed. The same selected command completed on the host without a business-test failure.

### Current conclusion and next step

The code can now represent and validate why a plan is executable and what would justify replacing it. It cannot yet observe a blocked route and create a revision automatically. Next, build a versioned `MissionStateSnapshot` from robot adapters, maps, task traces and reservations, followed by a bounded `MissionDeliberationRuntime` that can propose validated revisions. Do not add free-form LLM tool execution before those state and authority boundaries.

## 2026-07-28T16:35:31+08:00 - Mission State Snapshot Foundation Implemented

### Task goal

Implement the next planning foundation after `MissionTaskGraph`: create one
versioned, validated representation of the current mission state and bind each
generated task graph to the exact state used by the planner.

### OpenClaw analogue inspected

- `openclaw/src/agents/embedded-agent-runner/run/attempt-prompt-context.ts`

The reusable OpenClaw shape is the separation between current attempt/runtime
context and persisted session history. FireClaw adapts this into a typed
physical-state projection. Current robot/task/environment/resource state is
authoritative for the planning turn; memories and RAG remain advisory and
cannot overwrite it.

### State owners inspected

- `RobotSubagentClient.check_presence()` and `MissionAgent.check_fleet_presence()`
- `FireClawGateway.state()` and `_task_capacity_locked()`
- `RobotState` and `EnvironmentState` in `agent/robot.py`
- `TaskRecord` and `JsonlTaskRegistryStore.list_records()`
- `MissionMemoryStore.list_records()` and embodied observation persistence
- `MissionRuntimePaths` / `build_mission_agent_from_paths()`

The gateway already exposes robot state, local environment state, capacity,
active tasks and emergency-stop state. The task registry exposes mission and
subtask lifecycle. There is not yet a unified owner for shared-map facts or
exclusive resource reservations, so the snapshot accepts those only through
explicit typed providers. An absent provider means an empty collection, not
LLM-invented state.

### Files modified

- `src/fireclaw_core/mission/mission_state.py` (new): typed robot/task state,
  external environment facts, resource reservations, versioned snapshots,
  bounded projection builder and deterministic validator.
- `src/fireclaw_core/mission/mission_agent.py`: builds and validates a snapshot
  before planner invocation, blocks when authoritative state cannot be built,
  persists snapshots as observations, maintains version lineage, passes the
  snapshot into planner context and returns it in planning responses.
- `src/fireclaw_core/mission/mission_planner.py`: adds `state_snapshot` to
  `MissionPlannerContext`.
- `src/fireclaw_core/planner/llm_planner.py`: marks the structured snapshot as
  the current-state authority and states that history cannot override it.
- `src/fireclaw_core/mission/task_graph.py`: legacy-plan projection now accepts
  `state_snapshot_id`.
- `tests/test_mission_state.py` (new) and
  `tests/test_mission_task_graph.py`: cover projection, mission filtering,
  provenance, version chaining, validation, prompt inclusion and task-graph
  binding.

### Important behavior

- Snapshot IDs use `<mission_id>:state:<version>`.
- A later snapshot cites `previous_snapshot_id`; version state is recovered
  from mission memory after restart and cached in-process when no store exists.
- Robot-side free-form task results and error strings are not copied into the
  planner snapshot. Only bounded lifecycle fields and typed gateway state are
  included.
- Shared environment facts require evidence IDs. Invalid provider output,
  malformed state or deterministic validation failure blocks planning.
- Each successful `MissionTaskGraph` now carries the exact snapshot ID used by
  the planner. Plan cognitive artifacts derive from both the operator command
  and the snapshot observation when embodied memory is configured.

### Commands and verification

```text
/tmp/fireclaw-py310-eval/bin/pytest -q \
  tests/test_mission_state.py tests/test_mission_task_graph.py \
  tests/test_mission_planner.py tests/test_llm_planner.py
48 passed in 0.33s

/tmp/fireclaw-py310-eval/bin/pytest -q \
  tests/test_mission_agent.py tests/test_mission_runtime.py \
  tests/test_mission_scheduler.py tests/test_mission_plan_validator.py
97 passed in 0.55s

/tmp/fireclaw-py310-eval/bin/pytest -q
1634 passed, 6 skipped in 119.00s
```

The sandboxed full run first reported 119 socket-related failures and 3
embodied-eval warnings caused by those gateway failures. Re-running the same
full suite outside the socket-restricted sandbox passed. `compileall` and
`git diff --check` also passed.

### Engineering and research conclusion

Engineering correctness: the planner now has a stable, versioned current-state
input instead of loosely combining registry metadata, memory excerpts and
prompt text. This does not yet make FireClaw dynamically replan.

Research validity: the state/evidence boundary is necessary for auditable
evidence-triggered replanning, but it is infrastructure rather than a research
contribution by itself.

Publication-level contribution still requires a bounded deliberation runtime,
formal invalidation rules, evidence-grounded plan revisions and experiments
against static/retry-only/reactive baselines.

### Next recommended step

Implement `MissionDeliberationRuntime` as a bounded state machine. Its first
scope should be read-only inspection plus `propose_plan`,
`request_clarification` and `escalate`; every proposed plan must cite the
snapshot, pass deterministic graph validation and remain non-executable until
the existing safety/dispatch boundaries approve it. Do not begin with a
general unrestricted tool loop.

## 2026-07-28T16:58:33+08:00 - Bounded Mission Deliberation Runtime Implemented

### Task goal

Implement the first bounded deliberation layer after the task graph and state
snapshot foundations. The runtime must be able to inspect only the frozen
snapshot, produce a validated plan proposal or a non-execution terminal
decision, enforce liveness limits, persist its trajectory and leave all robot
dispatch authority in the existing MissionAgent/safety path.

### OpenClaw analogue inspected

- `openclaw/src/agents/embedded-agent-runner/run-loop.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt-prompt-context.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt.run-decisions.ts`
- `openclaw/src/agents/embedded-agent-runner/run/types.ts`

Reused structure:

- a host-owned bounded attempt loop;
- run-global iteration state that survives attempts;
- explicit timeout/cancellation/terminal resolution;
- current runtime context separated from persisted history;
- per-attempt trajectory and terminal reason codes;
- fail-closed handling for malformed tool/runtime outcomes.

FireClaw adaptation:

- there is no general side-effecting tool catalogue;
- reads are served only from one immutable `MissionStateSnapshot`;
- allowed decisions are `inspect_state`, `propose_plan`,
  `request_clarification` and `escalate`;
- the runtime returns a proposal but cannot call a robot, scheduler, adapter or
  skill;
- the MissionAgent repeats deterministic validation before dispatch, retaining
  the existing enforcement boundary.

### Files modified

- `src/fireclaw_core/mission/mission_deliberation.py` (new): adds the limits,
  typed read/decision/request/attempt/result contracts, snapshot reader,
  compatibility policy for existing planners, bounded loop, timeout,
  cancellation, repeated-read guard, proposal validation and revision
  provenance checks.
- `src/fireclaw_core/mission/mission_agent.py`: wraps configured planners in
  the runtime, records deliberation traces as cognitive artifacts, includes
  trace data in responses, accepts only runtime-produced validated graphs and
  preserves validator errors/audit records for rejected proposals.
- `src/fireclaw_core/mission/task_graph.py`: projection accepts explicit plan
  revision, superseded plan and invalidation evidence.
- `tests/test_mission_deliberation.py` (new): covers valid proposals,
  read-before-propose, validator-feedback revision, repeated reads, iteration
  limits, timeout, cancellation, context/snapshot mismatch, malformed
  decisions, projection failures and evidence-bound revisions.
- `tests/test_mission_state.py`: verifies MissionAgent response traces and
  deliberation-trace persistence.

### Runtime behavior

The default limits are four attempts, five seconds and three snapshot
observations. A policy can request bounded views of snapshot metadata, fleet,
one robot, tasks, environment facts or reservations. The reader never refreshes
live state inside the attempt; a different physical state requires a new
versioned snapshot.

A proposal is projected to a `MissionTaskGraph` and deterministically
validated inside the runtime. A custom policy can receive the errors on the
next attempt and repair its proposal. The compatibility adapter for existing
one-shot planners escalates an invalid proposal rather than repeating the same
output. MissionAgent then validates the accepted graph again immediately
before its existing scheduling/submission path.

Repeated identical reads are blocked as no progress. Invalid policy return
types and malformed plans are contained rather than escaping the runtime.
Revision evidence must both satisfy graph requirements and exist in the cited
state snapshot.

### Verification

Focused and related regression:

```text
tests/test_mission_deliberation.py
tests/test_mission_agent.py
tests/test_mission_runtime.py
tests/test_mission_scheduler.py
tests/test_mission_plan_validator.py
tests/test_mission_task_graph.py
tests/test_mission_state.py
tests/test_mission_planner.py
tests/test_llm_planner.py
tests/test_mission_planning_audit.py
tests/test_memory_learning_loop.py
165 passed in 1.18s
```

Full suite outside the localhost-socket-restricted sandbox:

```text
1646 passed, 6 skipped in 119.35s
```

`compileall` and `git diff --check` passed. No commit was requested or made.
The unrelated untracked
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

### Current limitations

- Existing deterministic and LLM planners use the compatibility policy, so a
  normal valid plan completes in one attempt. The multi-attempt read protocol
  is implemented and tested, but the current LLM tool schema does not yet ask
  for snapshot reads itself.
- MissionAgent currently invokes initial plan revision `1`. Automatic
  execution-event invalidation and revision numbering are not connected yet.
- Timeout is enforced around a synchronous policy call; the result is
  discarded after expiry, but interruption of an in-flight provider request
  still depends on the provider's own timeout/cancellation support.
- The existing low-risk primitive fallback remains a separate compatibility
  dispatch path governed by its structured-task and safety checks.

### Engineering and research conclusion

Engineering correctness: FireClaw now has a real host-owned planning runtime
boundary with liveness guards, typed observations, deterministic proposal
validation and auditable attempts. It is no longer only a direct
`planner.plan()` call.

Research validity: the runtime is enabling infrastructure, not yet the main
claim. Evidence-triggered plan revision and measured recovery behavior are
still required.

Publication-level contribution: the defensible contribution remains bounded,
evidence-grounded hierarchical replanning for safety-critical embodied agents,
not the generic existence of a loop.

### Next recommended step

Implement an execution-event-driven `MissionPlanRevisionCoordinator`. It
should classify typed events such as blocked route, robot loss, expired
evidence, resource conflict and partial success; decide deterministically
whether the current plan is invalid; build a new snapshot; then invoke
`MissionDeliberationRuntime` with `revision`, `supersedes_plan_id` and the exact
invalidation evidence IDs. Only after that should the LLM policy be extended
to request multiple snapshot views during a revision attempt.

## 2026-07-28T18:01:56+08:00 - Default LLM Multi-Round Deliberation Activated

### Task goal and corrected priority

The user correctly challenged the value of the prior runtime because the
default `LLMMissionPlanner` still used its one-shot `create_mission_plan` path.
This phase activates the runtime for the configured LLM planner itself. The
execution-event revision coordinator remains important, but implementing it
before a real LLM policy would have left the central multi-round capability
inactive.

### OpenClaw analogue and adaptation

Re-inspected:

- `openclaw/src/agents/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run-loop.ts`

The reused structure is still host-owned iteration state and explicit tool
outcomes carried into later attempts. FireClaw does not expose OpenClaw's
general tool catalogue. Each LLM call can make exactly one of four decisions:
read a frozen snapshot view, propose a plan, request operator clarification,
or escalate. The LLM never receives robot/skill execution tools.

### Files modified

- `src/fireclaw_core/planner/llm_planner.py`
  - `LLMMissionPlanner` now explicitly declares
    `supports_mission_deliberation = True` and implements `decide(request)`.
  - Adds `inspect_mission_state`, `propose_plan`,
    `request_clarification`, and `escalate` tool schemas.
  - Adds a deliberation-specific system prompt and per-turn JSON payload.
  - The prompt includes snapshot identity but deliberately omits the complete
    dynamic snapshot. Only observations already returned by the runtime are
    exposed on later calls.
  - Keeps the legacy `plan()` and `create_mission_plan` entry point compatible.
  - Reuses the existing strict mission plan parser with a configurable expected
    tool name.
  - Fixes a latent parser mismatch: because the plan tool schema has no
    top-level `command`, a missing command now falls back to the authoritative
    operator command instead of producing an empty graph command.
- `src/fireclaw_core/mission/mission_deliberation.py`
  - Parser/runtime proposal errors now preserve the rejected planning result
    and include its message in the next iteration's validation feedback.
  - The one-shot compatibility adapter marks its old `clarify` result as
    `planner_clarification`.
- `src/fireclaw_core/mission/mission_agent.py`
  - Uses an explicitly marked deliberation-capable planner directly as the
    runtime policy.
  - Other planners still use `PlannerDeliberationPolicy`; explicit marking
    avoids false capability detection from mocks or dynamic attributes.
  - Primitive fallback is now limited to legacy `planner_clarification`.
    An LLM's explicit `request_clarification` cannot be bypassed by automatic
    primitive dispatch.
- `tests/test_llm_deliberation_policy.py` (new)
  - Covers two distinct robot reads followed by a valid proposal.
  - Verifies the first model call cannot see battery state and later calls see
    only returned observations from the same snapshot ID.
  - Covers parser-error feedback and corrected proposal on the next call.
  - Covers unknown tools, multiple simultaneous tool calls, direct
    MissionAgent policy selection, read-then-plan dispatch, and the
    no-fallback clarification safety rule.

### Concrete behavior now

For a `去二楼救人` request, the default LLM path can now actually execute:

1. call `inspect_mission_state(robot_state, robot-a)`;
2. receive robot-a's frozen state on the next model call;
3. call `inspect_mission_state(robot_state, robot-b)`;
4. receive both accumulated observations on the next model call;
5. call `propose_plan` selecting robot-b;
6. let the runtime project and validate the task graph;
7. only then return control to MissionAgent's existing validation and dispatch
   boundary.

These are multiple provider calls. Snapshot reads are runtime operations, not
live robot calls, so every turn remains bound to the exact same
`snapshot_id`.

### Failure and safety behavior

- Zero or multiple LLM tool calls terminate through escalation.
- Unknown tools and malformed clarification/escalation arguments terminate
  safely.
- Invalid state-read requests are returned as validation feedback when the
  runtime has another iteration available.
- A malformed/invalid plan proposal can be corrected in the next LLM call.
- An explicit `request_clarification` waits for the operator and cannot invoke
  primitive fallback.
- Provider calls are still synchronous. The five-second runtime limit discards
  a late result after the call returns; interruption of an in-flight request
  remains dependent on provider timeouts.

### Verification

Focused final safety and agent tests:

```text
tests/test_llm_deliberation_policy.py
tests/test_mission_deliberation.py
tests/test_mission_agent.py
96 passed in 0.43s
```

Related planning/runtime regression:

```text
196 passed in 1.28s
```

Final full suite outside the localhost-socket-restricted sandbox:

```text
1652 passed, 6 skipped in 120.00s
```

The sandboxed full suite produced the known 119 localhost socket permission
failures. `compileall` and `git diff --check` passed. No commit was requested
or made. The unrelated untracked
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

### Engineering and research conclusion

Engineering correctness: the runtime now changes the default LLM behavior,
not only its surrounding infrastructure. It supports real multi-call
observation and self-correction while retaining a non-executing planning
boundary.

Research validity: this is still an engineering capability, not sufficient
novelty. It enables experiments on information acquisition and correction but
does not yet establish dynamic replanning from execution evidence.

Publication-level contribution: the next defensible step is the typed
execution-event invalidation and revision coordinator. It must demonstrate
that evidence-triggered replanning improves rescue success/safety over static
planning and retry-only baselines without causing excessive replans or operator
interventions.

### Next recommended step

Implement `MissionPlanRevisionCoordinator`: consume typed execution events,
classify deterministic invalidation causes, build the next snapshot version,
and invoke this now-active multi-round LLM policy with `revision`,
`supersedes_plan_id`, and exact evidence IDs. Do not add broader or
side-effecting LLM tools.

## 2026-07-28 18:27 +08 - Typed execution-event replanning implemented

### Task goal

Continue the task-planning work by connecting bounded deliberation to actual
execution evidence. The required behavior was: distinguish retry, replan,
retain, and safety escalation deterministically; build a new evidence-bound
snapshot; ask the revision-capable planner for a replacement graph; stop the
superseded schedule; and preserve an auditable lineage.

### OpenClaw analogue inspected

Before implementation, inspected the local OpenClaw reference with CodeGraph:

- `openclaw/src/agents/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run/AGENTS.md`
- `openclaw/src/agents/pi-embedded-runner/run-loop.ts`
- `openclaw/src/agents/pi-embedded-runner/attempt-recovery.ts`
- `openclaw/src/agents/pi-embedded-runner/run/types.ts`
- `openclaw/src/agents/pi-embedded-runner/attempt.run-decisions.ts`

Reused the proven host-owned recovery shape: typed outcomes are classified by
deterministic runtime code, bounded attempts are explicit, and the model
cannot directly decide whether safety or retry policy applies. FireClaw adds
robot-state snapshots, evidence IDs, graph revision lineage, authoritative
event sources, and safety-specific no-replan categories.

### Files modified

- `src/fireclaw_core/mission/mission_plan_revision.py` (new)
  - Adds `MissionExecutionEvent`.
  - Adds source authority policy per event type.
  - Adds deterministic `MissionPlanInvalidationPolicy`.
  - Adds `MissionPlanRevisionCoordinator` with event idempotency and a maximum
    plan revision budget.
- `src/fireclaw_core/mission/mission_deliberation.py`
  - Adds revision, superseded-plan, and invalidation-evidence fields.
  - Requires a revision policy to inspect environment facts containing every
    invalidation evidence ID before a proposal can be accepted.
- `src/fireclaw_core/planner/llm_planner.py`
  - Declares explicit revision support.
  - Sends revision lineage and evidence requirements in the system prompt and
    turn payload.
- `src/fireclaw_core/mission/mission_agent.py`
  - Tracks the active task graph.
  - Records typed execution events, the new snapshot, deliberation trace,
    revision audit, and revision artifact.
  - Does not replace the active graph if revision audit persistence fails.
- `src/fireclaw_core/mission/mission_scheduler.py`
  - Extracts only explicit typed invalidation events from robot traces.
  - Stops the old schedule on revised, blocked, clarified, or escalated
    revision outcomes.
  - Leaves transient failures in the existing bounded retry path.
- `tests/test_mission_plan_revision.py` (new)
- `tests/test_mission_deliberation.py`
- `tests/test_llm_deliberation_policy.py`

### Concrete runtime behavior

Example: `robot-a` is executing `去二楼搜索受困人员`, then the execution
monitor reports a typed `route_blocked` event with evidence
`evidence-route-blocked`.

1. Runtime verifies that `execution_monitor` is authoritative for
   `route_blocked`.
2. The deterministic invalidation policy confirms that the event affects a
   node in the active graph.
3. FireClaw freezes `mission-1:state:2`, links it to
   `mission-1:state:1`, and adds the route-block evidence as an environment
   fact.
4. The LLM policy must query that environment-fact view before it may propose
   a replacement.
5. A valid proposal becomes `mission-1:plan:2`, with
   `supersedes_plan_id=mission-1:plan:1` and the exact evidence ID.
6. The scheduler stops the old plan and returns `replanned`; it does not
   dispatch the new graph automatically.
7. If revision audit persistence fails, the scheduler returns `blocked` and
   `mission-1:plan:1` remains the active graph.

The following events do not enter free-form model judgment:

- `transient_failure` and `communication_timeout`: retain the bounded retry
  path.
- `task_succeeded` and `progress_update`: retain the current plan.
- `safety_blocked`, `emergency_stop`, `operator_cancelled`, and
  `authority_denied`: escalate; automatic replanning is forbidden.
- Unknown types, malformed timestamps, or an unauthorized event source:
  reject before planning.

### Verification

Focused revision/deliberation/scheduler/agent regression:

```text
116 passed in 0.51s
```

Broader related regression:

```text
323 passed; one sandbox-only local socket permission failure
```

Final full suite outside the localhost-socket-restricted sandbox:

```text
1664 passed, 6 skipped in 120.13s
```

Also passed:

```text
python -m compileall -q src tests
git diff --check
```

No commit was requested or made. The unrelated untracked
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

### Engineering and research conclusion

Engineering correctness: FireClaw can now react to authoritative execution
evidence with a bounded, evidence-grounded plan revision. This closes the gap
between a planner that only answers once and an execution-aware planning
runtime. The host still owns event classification, safety handling, snapshot
construction, validation, audit, and stopping the superseded schedule.

Research validity: this provides the mechanism needed to compare static
planning, retry-only recovery, and evidence-triggered replanning. It is still
an engineering capability rather than a publication-level contribution by
itself. A defensible claim needs experiments on task success, unsafe-action
rate, unnecessary-replan rate, latency, operator interventions, and
performance under stale or conflicting evidence.

### Current limitations and next recommended step

- The revised graph is not automatically dispatched. That is intentional:
  completed-node preservation and superseded-node cancellation semantics are
  not yet implemented, so automatic dispatch could duplicate completed work.
- Active-plan and revision-event idempotency caches are process-local. Restart
  recovery does not yet reconstruct the active graph from persistent
  artifacts.

Next implement a revision-aware dispatcher: preserve completed nodes, cancel
or fence still-running superseded nodes, carry forward valid results and
resource ownership, dispatch only pending nodes from the revised graph, and
persist enough active-plan state to recover the same decision after restart.

## 2026-07-28 18:57:42 +08 - Robot-side execution event producer

### Task goal

Close the missing production path between a robot-local navigation failure
and the typed mission event already consumed by the central plan-revision
runtime. Previously, revision tests injected `MissionExecutionEvent` fixtures;
a real `FireClawAgent` execution did not create one.

### OpenClaw analogue inspected

- `openclaw/src/agents/embedded-agent-runner/run/attempt-recovery.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt.run-decisions.ts`
- Scoped guides under `openclaw/src/agents/` and
  `openclaw/src/agents/embedded-agent-runner/run/`

Reused structure: the host runtime owns typed normalization and bounded
recovery decisions. The model does not decide whether an arbitrary error
string is authoritative. FireClaw adapts this boundary by projecting robot
action results into mission-level events with physical-safety source rules.

### Files added

- `src/fireclaw_core/mission/execution_event.py`
  - Extracted the lightweight event contract and authority matrix so the
    robot runtime does not depend on central snapshot or planner modules.
- `src/fireclaw_core/execution/execution_event_producer.py`
  - Added deterministic `RobotExecutionEventProducer`.
- `tests/test_execution_event_producer.py`
  - Covers explicit and inferred navigation failures, retry-count
    independence, agent emission, and Gateway trace persistence.

### Files modified

- `src/fireclaw_core/agent/agent.py`
  - Projects final failed execution results and emits
    `mission.plan_invalidated`; producer failures do not mask robot results.
- `src/fireclaw_core/mission/mission_plan_revision.py`
  - Imports the extracted event contract while preserving compatibility.
- `src/fireclaw_core/mission/mission_agent.py`
  - Carries the plan node ID into the structured robot task and mission
    metadata.
- `src/fireclaw_core/mission/mission_scheduler.py`
  - Assigns stable graph node IDs (`task-1`, etc.) at dispatch and preserves
    them through retries and reassignment.
- `src/fireclaw_core/safety/local_failure.py`
  - Recognizes conservative navigation failure phrases including `no path`
    and `route blocked`.
- `tests/test_local_failure.py`
- `tests/test_mission_plan_revision.py`

### Resulting runtime flow

For plan node `task-1` (`robot-a` navigates to floor 2):

1. The mission scheduler sends `task-1` to the robot as the structured task
   ID.
2. The robot adapter returns a failed `RobotActionResult`. A structured
   `failure_category=target_unreachable` is preferred; legacy error text is
   only a fallback.
3. After the local skill reaches its final failed result, the producer maps
   navigation plus `target_unreachable` to `route_blocked`.
4. The event contains mission ID, robot runtime task ID, plan node ID,
   evidence ID, authoritative source, timestamp, classification basis, and
   actual attempt count.
5. `FireClawAgent` emits `mission.plan_invalidated`; Gateway persists it in
   the task trace and also includes it in the terminal result.
6. The existing mission scheduler reads that explicit event and invokes the
   deterministic invalidation/revision path.

This classification is not based on the LLM and is not triggered merely by a
large retry count. A timeout after 99 attempts remains `transient_failure`;
only navigation plus credible target-unreachable evidence becomes
`route_blocked`.

### Classification rules

- Navigation `target_unreachable` -> `route_blocked`
- Non-navigation `target_unreachable` -> `transient_failure`
- `robot_offline` or `low_battery` -> `robot_unavailable`
- `sensor_unavailable` -> `sensor_degraded`
- `emergency_stop` -> `emergency_stop`
- Transport, timeout, or generic action failure -> `transient_failure`
- Successful, cancelled, unknown, or unprojectable results -> no
  invalidation event

Explicit enum and string categories are supported. Retryability is preserved
from structured failure data when supplied; otherwise it comes from the
shared local failure taxonomy.

### Commands and verification

```text
python -m pytest -q tests/test_execution_event_producer.py \
  tests/test_local_failure.py tests/test_robot_agent_structured_task.py \
  tests/test_mission_scheduler.py tests/test_mission_plan_revision.py \
  tests/test_mission_agent_structured_task.py
42 passed in 0.42s

python -m pytest -q tests/test_gateway_structured_task.py \
  tests/test_execution_event_producer.py
15 passed in 4.63s

python -m compileall -q src/fireclaw_core
git diff --check

python -m pytest -q
1671 passed, 6 skipped in 122.21s
```

### Current limitations and next recommended step

- Event production is terminal-result based. It does not yet consume live
  navigation feedback and invalidate a plan while an action is still
  running.
- Real ROS1/ROS2 navigation adapters must return a structured
  `failure_category` for reliable classification. Legacy error-text
  inference is deliberately limited and cannot replace move-base/Nav2 error
  code integration.
- The central scheduler still polls the terminal task trace; there is no
  push/SSE invalidation path from robot Gateway to mission runtime.
- The revised graph is still not automatically dispatched.

Next implement the revision-aware dispatcher described above. A subsequent
robot-integration phase should map move-base/Nav2 result codes to structured
failure categories and add live event delivery.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 23:16 +08 - RAG-assisted task-assumption compiler

### Task goal

Implement the agreed hybrid answer to the missing-dependency problem: RAG may
help discover and cite task common sense, while only reviewed deterministic
rules may add constraints that authorize physical dispatch.

### OpenClaw analogue inspected

CodeGraph was used against the local `openclaw/` reference before designing
the FireClaw change. OpenClaw retrieves memory/knowledge into model context,
while the host runtime still owns tool policy and result validation. No
upstream mechanism was found that safely promotes arbitrary retrieved text
directly into physical-action policy. FireClaw therefore reuses the
context-versus-runtime-policy split and adds an embodied safety adaptation:
retrieved knowledge remains advisory, and versioned approved rules are the
only automatic source of mandatory world-state assumptions.

### Implementation

Added `src/fireclaw_core/mission/task_assumption.py`:

- `RequiredBeliefTemplate`;
- reviewed/versioned `TaskAssumptionRule`;
- deterministic `TaskAssumptionRegistry`;
- grounded requirements tied to a concrete `area_id` or `entity_id`;
- `KnowledgeConstraintCandidate`, permanently marked
  `advisory_only=True`, `can_authorize_action=False`,
  `can_assert_current_state=False`, and
  `requires_current_state_revalidation=True`;
- the first narrow approved rule,
  `navigation-area-entry:v1`, requiring `passage_open=True` and
  `structural_stable=True` for navigation into a named area.

Extended `MissionGraphCompiler`:

- grounds approved rules against each semantic node target;
- auto-adds covered mandatory belief requirements even when the LLM omitted
  them;
- rejects compilation if a mandatory belief is absent, unconfirmed, stale,
  low-confidence, lacks evidence, or has the wrong value;
- rejects an explicit planner/RAG assumption that conflicts with an approved
  rule;
- merges multiple agreeing approved rules using the strictest confidence and
  freshness thresholds;
- persists origin, versioned rule IDs, and cited knowledge IDs into each
  compiled `MissionBeliefRequirement`.

Extended planner/proposal contracts:

- each `MissionGraphBeliefAssumption` can cite bounded `knowledge_refs`;
- the LLM schema and prompt state that RAG knowledge may explain an
  assumption but cannot prove current world state or override approved rules;
- assumption-level citations must be a subset of the proposal's retrieved
  knowledge references.

### Concrete before/after

Before:

```text
LLM proposes: navigate through west-stair
LLM forgets to mention structural stability
Compiler checks only assumptions the LLM wrote
```

After:

```text
LLM proposes: navigate through west-stair
Approved rule adds:
  west-stair/passage_open == true
  west-stair/structural_stable == true
Compiler must find fresh confirmed evidence for both
Dispatch gate checks both again immediately before execution
```

RAG can suggest the same conditions and attach citations, but a retrieved
document cannot by itself make either condition true and cannot become an
active runtime rule without review.

### Files added

- `src/fireclaw_core/mission/task_assumption.py`
- `tests/test_task_assumption.py`

### Files modified in this step

- `src/fireclaw_core/mission/graph_proposal.py`
- `src/fireclaw_core/mission/task_graph.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `tests/test_belief_contract.py`
- `README.md`

### Verification so far

Focused assumption/compiler/planner/task-graph tests:

```text
63 passed in 0.45s
```

### Engineering conclusion

For task types covered by the approved registry, FireClaw no longer relies on
the LLM to remember every mandatory world-state dependency. The compiler can
detect omission and fail closed. RAG remains useful for recall, explanations,
citations, and proposing future rule candidates, without crossing the
physical-action authorization boundary.

### Research conclusion and remaining gaps

This is a defensible safety architecture and an important engineering
capability, but one hard-coded navigation rule is not a publication-level
common-sense reasoning contribution. Stronger research claims require:

- a substantially broader, reviewed firefighting task ontology;
- a measurable RAG-candidate review/promotion workflow;
- coverage and unsafe-omission benchmarks;
- ablations for no RAG, RAG only, registry only, and hybrid enforcement;
- evaluation under incorrect retrieval, conflicting sources, stale facts,
  missing observations, and domain shift;
- comparison with symbolic task planning, behavior trees, and embodied-agent
  baselines.

Next recommended step after full regression: define a configuration/plugin
format for reviewed rules and build an offline candidate review tool rather
than expanding production policy through ad hoc source edits.

### Final regression update - 2026-07-28 23:22 +08

The first full-suite run found three regressions in
`tests/test_llm_planner.py`. All had the same cause: the new
`nodes[].belief_assumptions[].knowledge_refs` enum was accidentally injected
into the legacy `MISSION_PLAN_TOOL`, whose schema contains `subtasks` rather
than `nodes`.

The constraint was moved to `build_constrained_graph_proposal_tool`, while
`build_constrained_mission_plan_tool` continues to constrain only its
top-level legacy `knowledge_refs`. A dedicated graph-tool schema regression
test was added.

Verification after the correction:

```text
87 passed in 0.51s
1740 passed, 6 skipped in 124.56s
```

Also passed:

```text
python -m compileall -q src tests
git diff --check
```

## 2026-07-28 22:57:28 +08 - Dispatch-time world belief contracts

### Task goal

Implement the planning/execution gap discussed with the user:

- an LLM plan may be correct against its frozen planning snapshot;
- a later physical node may start after the environment changed;
- FireClaw must identify which world facts each node depends on and revalidate
  those facts immediately before dispatch rather than waiting for the robot to
  fail inside the hazard.

The concrete acceptance scenario was:

1. planning snapshot says the west stair is open;
2. the compiled navigation node depends on that belief;
3. dispatch snapshot says the west stair is blocked;
4. no robot task is submitted;
5. a typed invalidation event enters the existing plan-revision path.

### OpenClaw analogue inspected

Used CodeGraph against the local `openclaw/` tree before editing. Inspected
OpenClaw's before-tool-call policy/hook boundary and tool execution wrappers,
including:

- `src/agents/agent-tools.before-tool-call.ts`;
- `src/agents/agent-tools.before-tool-call.policy.ts`;
- tool definition execution wrappers.

Structure reused:

- runtime-owned policy executes immediately before a model-requested tool;
- the model proposes intent but cannot bypass the host check;
- blocked execution becomes structured runtime output.

FireClaw-specific adaptation:

- a software tool policy does not need to prove that a physical route belief
  is still true;
- FireClaw therefore binds world-state evidence into the task graph, captures
  a new mission snapshot before physical dispatch, and feeds a deterministic
  failure into plan invalidation.

### Implementation

Added `src/fireclaw_core/mission/belief_contract.py`:

- `BeliefRequirementCheck`;
- `MissionBeliefGateResult`;
- `MissionBeliefGate`;
- fail-closed checks for missing belief, identity change, non-confirmed status,
  value change, confidence below the compiled minimum, invalid timestamp, and
  age beyond the compiled maximum.

Extended planner-authored semantics:

- `MissionGraphBeliefAssumption` contains only `belief_id` and
  `expected_value`;
- `MissionGraphProposalNode.belief_assumptions` is exposed in the
  `propose_task_graph` schema;
- the constrained schema enumerates belief IDs from the frozen snapshot;
- the prompt requires explicit dependencies or an empty list;
- the deliberation runtime rejects assumptions not previously inspected
  through `environment_beliefs`.

The LLM does not control safety thresholds. `MissionGraphCompiler` verifies
the belief exists in the planning snapshot, is `confirmed`, matches the
expected typed value, has at least `0.8` confidence, is at most 15 seconds old,
and carries evidence. It then emits `MissionBeliefRequirement` with:

- stable belief, subject, and kind identity;
- expected value;
- required status `confirmed`;
- minimum confidence `0.8`;
- maximum age 15 seconds;
- planning snapshot ID;
- planning evidence IDs.

Extended executable and compatibility data:

- `MissionTaskNode.belief_requirements`;
- `MissionSubtask.belief_requirements`;
- task graph and plan serialization/round-trip support;
- validator checks for duplicate requirements, status, safe confidence/age
  bounds, planning snapshot lineage, and evidence;
- node execution spec hashing includes the requirement through node
  serialization.

Extended physical dispatch:

- `MissionAgent.refresh_mission_state_snapshot()` captures, validates, records,
  and versions a new current-state snapshot;
- `MissionScheduler._evaluate_dispatch_beliefs()` runs before robot calls;
- initial dispatch, retry, reassign, revised DAG execution, restart recovery,
  and pending-node continuation use the same gate;
- if any ready node fails, the dispatch batch is held before any node in that
  batch is sent;
- failure emits authoritative `belief_requirement_failed` from `safety_gate`;
- the existing revision coordinator receives the event and creates an
  evidence-bound revision snapshot;
- `MissionNodeExecution.dispatch_belief_gate` persists the last gate decision
  in dispatch checkpoints.

Also corrected world-belief time semantics:

- confirmed/uncertain `WorldStateBelief.observed_at` now comes from the
  observations supporting the winning value;
- a newer stale observation from another source can no longer make an older
  winning value appear fresh to the dispatch gate.

### Files added

- `src/fireclaw_core/mission/belief_contract.py`
- `tests/test_belief_contract.py`

### Files modified in this step

- `README.md`
- `src/fireclaw_core/mission/execution_event.py`
- `src/fireclaw_core/mission/graph_proposal.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/mission/mission_planner.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/revision_dispatcher.py`
- `src/fireclaw_core/mission/task_graph.py`
- `src/fireclaw_core/mission/world_state_belief.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `tests/test_world_state_belief.py`

### Verification

Initial structural regression:

```text
42 passed
```

Scheduler/compiler/recovery regression:

```text
46 passed
```

LLM deliberation, snapshot, revision, and event regression:

```text
91 passed
```

Final focused regression after the belief timestamp correction:

```text
90 passed
```

Also passed:

```text
compileall: PASS
git diff --check: PASS
```

Full suite outside the restricted socket sandbox:

```text
1727 passed, 6 skipped, 1 failed in 121.59s
```

The sole failure was
`tests/test_gateway.py::test_gateway_cancel_updates_task_queue_state`.
It expected `cancel_requested`, but the unrelated slow-policy task had already
returned `completed`. Neither `src/fireclaw_core/gateway/gateway.py` nor
`tests/test_gateway.py` was modified in this work. A dedicated rerun reproduced
the same existing timing behavior:

```text
1 failed in 0.78s
```

This remains an explicit unrelated verification gap; it was not hidden by
changing Gateway cancellation semantics inside the planning task.

### Engineering conclusion

FireClaw no longer has to wait for a robot to enter a route and report failure
when a declared world assumption changed. The active node carries the exact
belief/value/evidence contract, and deterministic runtime code refreshes and
checks it before the physical command leaves central mission control.

Backward compatibility is preserved: legacy plans and semantic nodes with no
declared belief assumptions have an empty requirement tuple and retain their
existing execution behavior.

### Research validity and remaining gap

This is a strong safety and auditability mechanism, but not by itself a novel
planning algorithm. The current `0.8` and 15-second limits are engineered
defaults and require calibration from simulator and real robot logs.

The major remaining semantic gap is dependency discovery: the planner must
explicitly declare relevant belief assumptions. FireClaw deterministically
validates every declaration, but it does not yet prove that the LLM listed
every world dependency. A stronger next layer needs a versioned firefighting
task/environment ontology or task-specific assumption compiler that can infer
mandatory route, hazard, localization, victim, and resource beliefs and reject
omissions.

For publication-level evidence, compare:

- frozen-snapshot-only execution;
- LLM-only rechecking;
- fixed periodic replanning;
- explicit belief contracts with dispatch-time gates.

Measure unsafe dispatch rate, unnecessary blocks, mission success, response
latency, replanning count, energy use, and sensitivity to confidence/freshness
thresholds under delayed, stale, conflicting, and correlated observations.

No commit was requested or made.

## 2026-07-28 22:21:52 +08 - Multi-source world-state belief projection

### Task goal

Implement the missing layer between raw environment observations and the
frozen planner snapshot:

```text
multi-source observations
-> deterministic freshness/source/conflict handling
-> planner-facing world-state beliefs
-> immutable mission snapshot
```

The user specifically challenged the assumption that snapshots eliminate
conflicts. The implementation now makes the distinction explicit: a snapshot
freezes what the planner sees, while the belief builder determines whether
the observations available at that capture time agree, conflict, are stale,
or remain below a confirmation threshold.

### OpenClaw analogue inspected

The local reference directory is `openclaw/`. Its source was inspected with
CodeGraph:

- `openclaw/packages/agent-core/src/agent-loop.ts`
- `runAgentLoop`
- `runAgentLoopContinue`
- `runLoop`
- `streamAssistantResponse`
- `executeToolCalls`

Relevant upstream structure:

- host code executes tools and appends ordered tool-result messages;
- `transformContext` and `normalizeCoreContextMessages` prepare the model
  context before every model call;
- the model consumes host-prepared context but does not own tool execution,
  abort handling, result ordering, or loop termination.

FireClaw adaptation:

- raw robot/sensor observations remain auditable;
- deterministic host code produces a bounded belief projection before the LLM
  sees it;
- the LLM may reason about the projected status but cannot promote an
  uncertain/conflicted/stale observation to authoritative state.

### Implementation

Added `src/fireclaw_core/mission/world_state_belief.py`:

- `WorldStateObservation`;
- `WorldStateBeliefCandidate`;
- `WorldStateBelief`;
- `WorldStateBeliefBuilder`;
- stable SHA-256-derived belief IDs;
- statuses `confirmed`, `uncertain`, `conflicted`, and `stale`;
- source reliability weights;
- explicit expiry, maximum-age, and future-clock-skew handling;
- latest-observation-wins behavior per source;
- conservative `value=None` for conflicted and stale beliefs;
- full supporting, conflicting, stale, superseded, source, and evidence
  provenance.

Default deterministic parameters:

```text
confirmation_threshold = 0.8
conflict_threshold = 0.5
default_confidence = 0.5
default_max_age_seconds = 30.0
future_clock_skew_seconds = 5.0
```

Agreement from different latest-per-source observations is combined with
`1 - product(1 - effective_confidence)`. Source reliability multiplies the
reported confidence before combination. Multiple candidates at or above the
conflict threshold yield `conflicted`; a sole leading candidate below the
confirmation threshold yields `uncertain`.

Important safety correction during review:

- the initial implementation filtered stale reports before choosing the
  latest report per source;
- this could have allowed an older "open" report to become active again when
  the same sensor's newer "blocked" report had expired;
- the order was corrected to choose the latest report per source first, then
  evaluate freshness;
- a regression test proves that a stale latest report suppresses, rather than
  resurrects, the older source value.

Extended `MissionEnvironmentFact`:

- it is now explicitly an auditable observation rather than an already-fused
  fact;
- optional `subject_id` identifies the physical/logical entity whose
  property is being observed;
- observations only fuse across the same `(subject_id, kind)` key;
- legacy facts without a subject remain isolated under a fact-scoped subject,
  preventing accidental fusion of unrelated entities.

Extended `MissionStateSnapshot` and its builder:

- raw `environment_facts` remain present for audit;
- `environment_beliefs` and an `environment_belief_summary` are added;
- `belief_projection_version=1` marks snapshots whose raw observations must
  be fully represented by beliefs;
- `with_environment_facts` atomically replaces raw observations, recomputes
  beliefs, and rebuilds evidence IDs;
- plan-revision event projection uses this method, preventing stale beliefs
  after an invalidation event is appended.

Extended snapshot validation:

- validates belief IDs, keys, status, timestamps, confidence, candidates, and
  referenced observation IDs;
- rejects belief projections that omit raw observations or reference unknown
  ones;
- rejects resolved values for `conflicted` or `stale` beliefs;
- validates optional fact subjects and expiry timestamp syntax;
- retains `belief_projection_version=0` compatibility for direct legacy
  snapshots.

Extended planner read/runtime behavior:

- new read-only `environment_beliefs` snapshot view;
- filtering supports belief ID, subject ID, kind, and status;
- snapshot metadata exposes belief counts;
- the LLM prompt distinguishes raw observations from planner-facing beliefs;
- the prompt forbids treating `uncertain`, `conflicted`, or `stale` values as
  confirmed;
- a plan proposal is deterministically rejected until every unresolved
  belief has been inspected;
- version-1 plan revisions must inspect invalidation evidence through the
  belief surface rather than relying directly on raw facts.

### Files added

- `src/fireclaw_core/mission/world_state_belief.py`
- `tests/test_world_state_belief.py`

### Files modified in this step

- `src/fireclaw_core/mission/mission_state.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/mission/mission_plan_revision.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `tests/test_mission_state.py`
- `tests/test_mission_deliberation.py`
- `tests/test_llm_deliberation_policy.py`
- `tests/test_mission_plan_revision.py`
- `tests/test_completion_contract.py`

### Verification

Focused fusion, snapshot, deliberation, revision, and completion tests:

```text
57 passed in 0.42s
```

Expanded planning/runtime regression after the final safety correction:

```text
110 passed in 0.74s
```

An expanded sandboxed run produced 40 localhost socket permission errors in
`test_mission_gateway.py`, with all 109 non-socket tests passing. The complete
suite was then run outside the socket-restricted sandbox:

```text
1722 passed, 6 skipped in 122.68s
```

Also passed:

```text
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 \
  -m compileall -q src/fireclaw_core
git diff --check
```

### Engineering conclusion

FireClaw no longer assumes that snapshot inclusion makes an observation true.
The planner now receives both raw provenance and a deterministic conclusion
about whether a world property is confirmed, uncertain, conflicted, or stale.
This follows the OpenClaw host-owned-context boundary while adding the
freshness, source disagreement, and fail-safe value suppression required for
embodied operation.

### Research validity and remaining gaps

- The current confidence combination assumes independent sources. Correlated
  sensors or robots sharing the same map can be over-counted unless providers
  use a common source identity or a later correlation model.
- Thresholds and the 30-second default age are engineering defaults, not
  empirically calibrated firefighting values.
- Source reliability is configured, not learned or continuously estimated.
- Provenance is structurally traceable but not authenticated or
  cryptographically attested.
- Planner awareness is enforced, but the task graph does not yet bind each
  physical action to the exact beliefs it relies on. Therefore deterministic
  code cannot yet prove that an inspected unresolved belief was not ignored by
  the LLM.
- Successful reconnaissance does not yet trigger a dedicated observation
  refresh and continuation protocol.
- This is an auditable engineering baseline, not by itself a
  publication-level novel evidence-fusion method.

Next recommended implementation:

1. add explicit `required_belief_ids` or typed world assumptions to semantic
   graph nodes;
2. make the compiler/safety gate require those beliefs to be confirmed before
   physical dispatch;
3. allow a reconnaissance node to refresh the affected beliefs and trigger a
   bounded continuation/replan;
4. build an evaluation dataset and metrics for false confirmation, conflict
   detection, stale-evidence rejection, unnecessary escalation, planning
   latency, and mission success;
5. calibrate freshness/confidence policies from simulator and robot logs, with
   ablations against raw-fact LLM planning and simple latest-value baselines.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 21:58:43 +08 - Hybrid recovery loop with LLM replanning

### Task goal

Implement the hybrid recovery architecture agreed with the user:

- do not constrain FireClaw to hard-coded planning;
- return semantic execution failures to the existing bounded LLM planner,
  following the same high-level result-to-next-turn pattern used by OpenClaw;
- keep event authority, retry bounds, immutable snapshots, plan validation,
  physical dispatch, cancellation, and checkpoint state under deterministic
  runtime ownership.

### OpenClaw analogue inspected

Used CodeGraph on the local OpenClaw reference before designing the change:

- `openclaw/packages/agent-core/src/agent-loop.ts`
- `runLoop`
- `executeToolCalls`
- `runAgentLoopContinue`

Relevant upstream structure:

- tool result messages are appended to the agent context;
- the inner loop calls the model again while tool calls continue;
- host code owns tool execution, abort handling, sequential/parallel
  execution, result insertion, stop conditions, and event ordering.

FireClaw adaptation:

- a robot completion rejection is projected into a trusted typed event rather
  than sending raw robot text directly to the model;
- the event is merged into a new immutable state snapshot;
- the LLM can inspect the new facts and propose a replacement graph;
- `MissionGraphCompiler`, deterministic validators, revision reconciliation,
  cancellation/fencing, and the scheduler remain authoritative.

### Implementation

Added `src/fireclaw_core/mission/recovery_orchestrator.py`:

- `RecoveryDecision`;
- `CompletionRecoveryOrchestrator`;
- deterministic classification of result-delivery gaps versus semantic
  completion-contract failures;
- stable SHA-256-derived event/evidence IDs;
- bounded result recheck count;
- suggested recovery action (`reassign`, `replan`, `retry`, etc.) is advisory
  input to the planner and never directly actuates a robot.

Extended execution event policy:

- added authoritative `completion_evidence_rejected`;
- it is invalidating and can only originate from `execution_monitor`;
- malformed, wrong-source, or free-form event types remain rejected.

Extended revision snapshot projection:

- adds a primary `completion_evidence_rejected` environment fact tied to the
  affected node;
- adds bounded `completion_requirement_failed` facts for each failed
  requirement kind;
- adds a validated `suggested_recovery_action` fact;
- all facts reference the deterministic completion-validation evidence ID.

Connected the scheduler:

- every rejected nominal success now receives a structured recovery decision;
- semantic failures attach an invalidation event and enter
  `MissionAgent.handle_execution_event`;
- the existing revision coordinator builds a new snapshot and invokes the
  multi-round deliberation policy;
- a valid LLM replacement graph goes through compilation, audit, revision
  reconciliation, checkpointing, old execution fencing, and dispatch;
- escalation/abort policies do not invoke the planner.

Checkpoint persistence:

- `MissionNodeExecution` now stores `recovery_attempt` and
  `recovery_action`;
- both fields survive JSONL checkpoint round trips and restart recovery;
- carried/preserved revision nodes retain those fields.

Evidence format compatibility:

- `ExecutionEvidenceValidator` now accepts both existing nested
  `output.data` results and the real `PlanExecutor` flattened output format
  (`output.floor`, `output.victims_found`, etc.).

### Important safety correction during implementation

The first implementation treated a missing structured result as a local
`retry` and resubmitted the whole subtask. Tests passed, but review identified
that this could repeat a physical action when only the result delivery was
incomplete.

That design was rejected and replaced before completion:

- current action is `recheck`, not task retry;
- FireClaw polls the same `task_id` once more;
- no new robot task is submitted and no new actuation occurs;
- if the same result remains incomplete, the event becomes
  `completion_evidence_rejected` and returns to the planner.

The end-to-end test explicitly asserts one robot task submission and at least
two trace reads.

### Concrete verified flows

#### Semantic evidence rejection to LLM

1. Policy proposes a typed victim-search task for robot-a.
2. Robot-a reports success and confirms floor 2, but omits
   `victims_found`.
3. Completion validation rejects `victim_search_result`.
4. Recovery orchestrator emits `completion_evidence_rejected` with suggested
   action `reassign`.
5. Revision coordinator creates the next snapshot.
6. Planner inspects the new environment facts and proposes robot-b.
7. Graph validation and revision reconciliation succeed.
8. Robot-b returns complete evidence.
9. Mission finishes `succeeded`.

#### Incomplete result delivery without repeated actuation

1. One robot task is submitted.
2. First trace reports success but has no structured execution result.
3. Runtime records `recheck`, increments the persisted recovery attempt, and
   reads the same task again.
4. Second trace contains complete evidence.
5. Mission succeeds with exactly one task submission.

### Files added

- `src/fireclaw_core/mission/recovery_orchestrator.py`
- `tests/test_recovery_orchestrator.py`

### Files modified in this step

- `src/fireclaw_core/mission/completion_contract.py`
- `src/fireclaw_core/mission/execution_event.py`
- `src/fireclaw_core/mission/mission_plan_revision.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/revision_dispatcher.py`
- `tests/test_completion_contract.py`

### Verification

Focused recovery/completion/revision/scheduler/checkpoint tests:

```text
54 passed in 0.36s
```

Expanded planning/runtime regression:

```text
103 passed in 0.79s
```

Final full suite outside the localhost-socket-restricted sandbox:

```text
1714 passed, 6 skipped in 125.55s
```

Also passed:

```text
PYTHONPATH=src python3 -m compileall -q src/fireclaw_core
git diff --check
```

### Engineering conclusion

FireClaw now implements the essential OpenClaw-like loop at mission level:

```text
execute -> observe -> validate -> feed structured failure to model
-> propose replacement graph -> validate -> reconcile -> continue
```

It is not restricted to hard-coded planning. Deterministic code decides
whether evidence is authoritative and whether a proposal is executable; the
LLM decides semantic plan changes from the frozen snapshot.

### Research validity and remaining gaps

- Recovery classification is currently a deterministic engineered policy,
  not a learned or uncertainty-aware recovery model.
- The suggested action is advisory; experiments must measure whether it
  biases LLM revisions usefully or causes anchoring errors.
- Evidence is structurally authoritative by source policy but is not yet
  cryptographically attested or fused across independent sensors.
- Result recheck is polling-based. A dedicated robot-side result-refresh or
  push/SSE acknowledgement protocol would reduce latency.
- The planner sees typed failed requirements but not calibrated evidence
  confidence or cross-sensor disagreement.
- Node-level recovery for non-completion failures in the revised DAG still
  depends on the existing event taxonomy and does not yet have a unified
  policy interface.

Next recommended step: add provenance-aware multi-source evidence fusion and
confidence/disagreement handling. Before that research work, add metrics for
recheck success, LLM revision success, recovery latency, unnecessary
replanning, and unsafe/invalid proposal rejection.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 21:01:44 +08 - Semantic task graph proposal and deterministic compiler

### Task goal

Implement the main gap identified in the 20:18 planning reassessment:
the LLM should express a stable semantic task DAG instead of directly
selecting robots in a flat `MissionPlan`. A host-owned compiler must allocate
robots and inject physical execution constraints before the graph reaches the
scheduler.

Target flow:

```text
LLM policy
  -> MissionGraphProposal (non-executable semantics)
  -> MissionGraphCompiler (deterministic allocation and constraints)
  -> MissionTaskGraph (validated executable contract)
  -> MissionScheduler
```

### OpenClaw analogue inspected

Re-inspected these local OpenClaw paths with CodeGraph before editing:

- `openclaw/src/agents/embedded-agent-runner/run-orchestrator.ts`
- `openclaw/src/agents/embedded-agent-runner/run-loop.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt-execution-phase.ts`
- tool policy, tool result middleware, session, and recovery paths adjacent to
  the embedded runner.

Structure reused: the host runtime owns the bounded tool loop, policy
enforcement, validation, recovery, and persistent execution state. The model
only proposes the next semantic operation.

FireClaw adaptation: unlike OpenClaw's general computer/tool agent, a semantic
proposal is deliberately non-executable. Robot allocation, live-capacity
checks, emergency-stop checks, exclusive resources, timeout, and recovery
policy are compiled deterministically from one frozen mission-state snapshot.

### Files added

- `src/fireclaw_core/mission/graph_proposal.py`
  - `MissionGraphProposalNode`
  - `MissionGraphProposal`
  - `MissionGraphProposalValidator`
  - `MissionGraphCompiler`
  - `MissionGraphCompilationError`
  - `CompiledMissionGraph`
- `tests/test_mission_graph_proposal.py`

### Files modified in this step

- `src/fireclaw_core/mission/mission_planner.py`
  - `MissionSubtask` can carry stable `node_id`, `task_type`, complete target,
    and `completion_goal`.
  - `MissionPlanningResult` can carry a pre-compilation graph proposal.
- `src/fireclaw_core/mission/task_graph.py`
  - Task nodes persist task type and completion goal.
  - Legacy projection uses stable IDs and complete targets when available.
- `src/fireclaw_core/task/task_contract.py`
  - Structured robot tasks preserve area/entity/pose targets, semantic task
    type, stable task ID, and completion goal.
- `src/fireclaw_core/mission/mission_plan_validator.py`
  - Floor is optional when a typed target exists.
- `src/fireclaw_core/mission/revision_dispatcher.py`
  - Completion-contract hashes include task type and completion goal.
- `src/fireclaw_core/mission/mission_scheduler.py`
  - Initial scheduling uses planner node IDs.
  - Reassign and restart recovery preserve full semantic task data.
  - Pose/area/entity-only nodes no longer disappear during checkpoint restore.
- `src/fireclaw_core/mission/mission_deliberation.py`
  - New proposals are compiled before existing deterministic validation.
  - Compilation failures feed back into the bounded deliberation loop.
  - Legacy flat proposals remain supported.
- `src/fireclaw_core/planner/llm_planner.py`
  - Added `propose_task_graph`.
  - Its schema contains no `robot_id`, resource lock, timeout, or recovery
    policy field.
  - `propose_plan` remains as a compatibility tool.
- `src/fireclaw_core/mission/mission_agent.py`
  - Persists both the original semantic proposal and compiled plan/task graph
    as cognitive artifacts.
- `tests/test_llm_deliberation_policy.py`
  - Covers the new LLM-to-compiler-to-task-graph path and tool boundary.

### Resulting behavior

For an operator command such as `去二楼救人`, the LLM can now return:

```text
search_second_floor:
  task_type = victim_search
  target = building floor 2 / second_floor
  capability = search_for_victims
  completion_goal = search completed and victim locations reported
  depends_on = []
```

The LLM does not choose `robot-a` or `robot-b`. Given the frozen snapshot,
the compiler:

1. excludes offline, stale, emergency-stopped, incapable, full, unreachable,
   or exclusively reserved robots;
2. selects a robot deterministically using current assignment count, target
   floor, active load, battery, and robot ID;
3. injects `robot_enabled`, `robot_has_capability`, and
   `emergency_stop_inactive` preconditions;
4. injects the exclusive `robot:<id>` resource, a timeout, success-evidence
   requirement, and `replan`/`reassign` recovery policy;
5. serializes otherwise parallel nodes if they must share one robot;
6. emits both a compatibility `MissionPlan` and the native
   `MissionTaskGraph`.

In the end-to-end test, both robots are online on floor 1, but `robot-a` has
20% battery and `robot-b` has 85%. The LLM emits no robot ID; the compiler
selects `robot-b`.

### Verification

Focused semantic graph and planning regression:

```text
61 passed in 0.43s
```

The first full run inside the restricted sandbox produced:

```text
1578 passed, 6 skipped, 119 failed
```

All 119 failures were caused by `PermissionError: [Errno 1] Operation not
permitted` when gateway tests tried to bind localhost sockets. The embodied
evaluation tests caught the same socket error and reported `warn`.

The required full rerun outside that socket restriction passed:

```text
1697 passed, 6 skipped in 123.35s
```

Also passed:

```text
git diff --check
```

### Engineering conclusion

FireClaw now has a genuine agent-planning boundary rather than only a richer
serialization format:

- the model owns semantic decomposition and dependency intent;
- deterministic code owns robot selection and physical execution policy;
- the same compiled graph is used by validation, persistence, revision,
  restart recovery, and scheduling;
- invalid semantic graphs or impossible allocations fail closed before any
  robot-side action.

The old flat planner remains operational, so existing deterministic planners
and deployments are not forced to migrate atomically.

### Research conclusion and next recommended step

This is an engineering-correct safety architecture, not yet a standalone
publication-level planning contribution. Deterministic compilation makes the
system auditable and enables clean ablations, but the allocation score and
generic completion evidence are currently heuristic.

The next development priority should be **execution-grounded completion
contracts and capability-specific graph templates**:

- map `completion_goal` to typed sensor/skill evidence instead of the generic
  `task_terminal_success`;
- define task-type-specific preconditions, evidence, timeout, and recovery
  contracts;
- evaluate semantic-plan validity, allocation feasibility, intervention rate,
  unsafe proposal rejection, replanning latency, and task success against the
  legacy flat planner;
- only after this contract layer is reliable should FireClaw broaden the
  planner's read/query tools toward OpenClaw-like generality.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 20:18:29 +08 - Post-implementation agent-planning reassessment

### User request

Pause further implementation and reassess whether FireClaw can now perform
agent-level planning, especially compared with the local OpenClaw reference.

### Evidence inspected

FireClaw was inspected with CodeGraph across the complete path:

- `MissionAgent.plan_and_submit()`
- `MissionDeliberationRuntime.deliberate()`
- `LLMMissionPlanner.decide()`
- `MissionSnapshotReader.read()`
- `task_graph_from_mission_plan()`
- `MissionPlanInvalidationPolicy.evaluate()`
- `RobotExecutionEventProducer.produce()`
- `MissionScheduler.schedule()`, revised-graph execution and checkpoint resume
- `RevisionDispatchReconciler.reconcile()`

The current OpenClaw analogue was re-inspected from its own CodeGraph index:

- `src/agents/embedded-agent-runner/run-orchestrator.ts`
- `src/agents/embedded-agent-runner/run-loop.ts`
- `src/agents/embedded-agent-runner/run/attempt.ts`
- `src/agents/embedded-agent-runner/run/attempt-execution-phase.ts`
- agent tool policy, tool-result middleware, session and recovery paths

OpenClaw's current implementation is a broad session-oriented tool-using agent
runtime with tool-result continuation, context management, provider/profile
fallback, replay and plugin hooks. It does not expose a directly comparable
physical mission DAG with robot targets, evidence, resources and revision
lineage. FireClaw should therefore not be judged as a smaller clone of every
OpenClaw tool surface.

### Reassessment conclusion

FireClaw now has a real but deliberately bounded agent-level mission-planning
loop:

1. It accepts a natural-language mission and freezes authoritative robot,
   task, environment, resource and emergency-stop state.
2. The default LLM policy may inspect selected views over several model turns,
   request clarification, escalate, or propose a plan.
3. The host projects and deterministically validates a task graph before any
   dispatch.
4. The scheduler executes dependencies, observes typed robot failures, and
   distinguishes retain, retry, replan and escalation outside model judgment.
5. An invalidating event creates a new snapshot and evidence-bound plan
   revision; completed work can be carried, unchanged active work preserved,
   superseded work cancelled/fenced, and new dependency-ready work dispatched.
6. Durable checkpoints allow a new process to reconcile active physical task
   IDs and continue without blindly resubmitting work.

This satisfies the core definition of agent-level planning for the supported
mission contract: goal decomposition, state acquisition, constrained decision,
execution, outcome observation and plan adaptation form an end-to-end loop.
The old assessment that FireClaw only calls a one-shot planner is no longer
accurate.

### Important remaining gap

The LLM proposal contract is still the legacy `MissionPlan` shape:
`intent`, `knowledge_refs`, and subtasks containing robot, command, floor,
capability and execution group. `task_graph_from_mission_plan()` then derives:

- positional node IDs such as `task-1`;
- group-barrier dependencies;
- building/floor targets;
- robot/capability preconditions;
- generic terminal-success evidence;
- one exclusive robot resource;
- a default `reassign` recovery policy.

Therefore the runtime representation is richer than what the default LLM can
currently author. The model cannot yet directly express arbitrary dependency
edges, area/entity/pose targets, per-node evidence contracts, explicit
resources, risks, timeouts, effects or recovery policies. Pose/area/entity-only
nodes are also not yet projected into the current robot dispatch contract.

Other maturity gaps:

- snapshot reads are intentionally read-only and do not return live skill
  results within the same deliberation run;
- environment/map/resource providers may be absent in deployment;
- execution invalidation is terminal-result polling, not live feedback push;
- planning intents and robot task contracts remain closed and domain-specific;
- startup recovery is synchronous, retry/reassign intent has a crash window,
  and there is no cross-process mission ownership lease;
- correctness is well unit-tested, but real-model, simulator and real-robot
  planning quality has not yet been established.

### Comparison judgment

- Against the previous FireClaw: a substantial architectural improvement from
  one-shot assignment to an execution-aware, evidence-grounded planning loop.
- Against OpenClaw runtime maturity: still behind in general tool breadth,
  session continuity, context management, fallback machinery and operational
  hardening.
- For embodied planning semantics: FireClaw now has structures OpenClaw does
  not need, including physical state authority, mission DAG validation,
  evidence-bound invalidation, task fencing and robot execution reconciliation.
- Overall: usable as a bounded firefighting mission agent framework, not yet a
  general or fully expressive planning framework.

### Verification

Focused planning/revision/recovery regression:

```text
85 passed in 0.69s
```

No production code was changed and no commit was made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` remains untouched.

### Recommended next decision

Do not immediately add the recovery supervisor. The highest planning-specific
gap is to replace the legacy LLM `MissionPlan` proposal with a validator-safe
native `MissionTaskGraph` proposal contract, while retaining deterministic
allocation, safety and dispatch authority outside the model. Before
implementation, validate the current closed loop with several real-model
simulator scenarios so schema work is driven by observed planning failures
rather than only interface completeness.

## 2026-07-28 19:55:51 +08 - Restart-safe dispatch recovery

### Task goal

Make a fresh FireClaw mission runtime recover incomplete physical execution
from `MissionDispatchCheckpoint` instead of forgetting the active plan,
duplicating robot work, or leaving dependency-ready nodes undispatched.

### OpenClaw analogue inspected

- `openclaw/src/gateway/server-methods/agent-restart-recovery-context.ts`
- `openclaw/src/agents/main-session-restart-recovery-resume-policy.ts`
- `openclaw/src/acp/control-plane/manager.runtime-resume-state.ts`

Reused structure: durable recovery authority is rehydrated only when the
persisted session/run identity matches; interrupted work is classified by the
host runtime; corrupt or non-resumable state is blocked or reset rather than
passed to the model. FireClaw adapts this by additionally requiring an exact
task-graph/specification hash, robot task identity, live Gateway status, and
confirmed cancellation of superseded physical actions.

### Files modified

- `src/fireclaw_core/mission/task_graph.py`
  - Added strict `from_dict()` reconstruction for targets, conditions,
    evidence requirements, nodes, and complete task graphs.
- `src/fireclaw_core/mission/revision_dispatcher.py`
  - Checkpoints now persist the complete active task graph, command/plan
    memory lineage, fenced task IDs, and superseded executions whose
    cancellation is not yet durably confirmed.
  - Added per-mission latest checkpoint enumeration.
  - Added a strict recovery reader: a complete corrupt JSONL record blocks
    recovery; only an unterminated final append is treated as a crash tail.
- `src/fireclaw_core/mission/mission_scheduler.py`
  - Added `resume_pending_dispatches()` and `resume_from_checkpoint()`.
  - Validates graph identity, registry presence, node sets, specification
    hashes, task-ID uniqueness, and active task reconstructability.
  - Re-queries every active robot task before continuing.
  - Restores the active graph without invoking the planner.
  - Reuses existing robot task IDs for monitoring and dispatches only pending
    dependency-ready nodes.
  - Before resuming a revised plan, re-queries, re-cancels when necessary, and
    waits for every persisted superseded execution to terminate.
  - Performs fresh presence and emergency-stop checks before recovered DAG
    nodes are dispatched.
  - Converts a Gateway trace loss during polling into a fail-safe `lost`
    execution rather than continuing dependencies.
  - Persists immediately after initial, retry, and reassignment dispatch so a
    restart can recover the concrete robot task ID.
  - Unified initial/recovered DAG dedupe keys as
    `<mission_id>-<plan_id>-<node_id>`.
- `src/fireclaw_core/mission/mission_agent.py`
  - Added validated graph hydration, automatic recovery entry point, and
    `dispatch_recovery_report`.
- `src/fireclaw_core/mission/mission_runtime.py`
  - Production path construction automatically runs dispatch recovery.
  - Added injectable `subagent_client` for deterministic startup integration
    tests and `resume_dispatches=False` as an explicit opt-out.
- `src/fireclaw_core/mission/mission_gateway.py`
  - `/fleet/doctor` now exposes the startup dispatch recovery report.
- `tests/test_revision_dispatcher.py`
- `tests/test_mission_scheduler.py`
- `tests/test_mission_runtime.py`
- `tests/test_mission_gateway.py`

### Recovery sequence

1. Read the latest complete checkpoint for each mission.
2. Reject old checkpoints without an embedded task graph and reject corrupt
   complete records rather than falling back to a possibly stale plan.
3. Validate graph/plan/revision identity and every node specification hash.
4. If a plan revision had old executions awaiting cancellation, query them,
   issue idempotent cancellation when still active, wait up to the existing
   five-second cancellation deadline, then persist that cancellation cleanup.
5. Query every current active `task_id` from its owning robot Gateway and
   project the authoritative status into the mission registry/checkpoint.
6. Hydrate `MissionAgent._active_task_graphs` only for non-terminal work.
7. Monitor already-running nodes; dispatch only pending nodes whose
   dependencies are durably successful.
8. Preserve plan/command memory lineage and all old-task fences.
9. Unknown state, identity mismatch, unavailable robot, active emergency stop,
   corrupt checkpoint, failed checkpoint write, or unconfirmed cancellation
   returns `blocked` and performs no replacement dispatch.

### Verified scenarios

- Active task succeeds after controller restart; its dependent task is
  dispatched once, while the active task is not resubmitted.
- A pending pre-dispatch checkpoint resumes with the stable dedupe key.
- Unknown robot task state blocks recovery.
- Legacy checkpoint without a task graph blocks recovery.
- A crash between plan:2 checkpointing and old-task cancellation recovers by
  cancelling the old task first, then dispatching plan:2.
- Complete corrupt JSONL lines block automatic recovery; an unterminated final
  crash tail is ignored.
- Runtime construction automatically resumes a pending checkpoint.
- Fleet doctor exposes blocked/successful recovery results.

### Verification

Focused recovery and revision regression:

```text
48 passed in 0.46s
```

Full suite with localhost socket permission:

```text
1691 passed, 6 skipped in 123.70s
```

Also passed:

```text
python -m compileall -q src/fireclaw_core
git diff --check
```

### Remaining limitations and next recommended step

- Startup recovery is synchronous in `build_mission_agent_from_paths()`.
  This preserves ordering and prevents new dispatch before reconciliation, but
  a long-running robot task can delay Mission Gateway readiness. A dedicated
  recovery supervisor should later separate quick startup reconciliation from
  continued background monitoring while exposing a readiness/degraded state.
- Checkpoint writes use an in-process `RLock`; there is no cross-process
  mission lease. Stable Gateway dedupe keys reduce duplicate execution risk,
  but multi-process/split-brain deployment requires a durable ownership lease
  before claiming exactly-one dispatcher semantics.
- If the process dies after a failed task checkpoint but before the existing
  retry/reassign decision is persisted, the failure remains terminal after
  restart. Persisted node-level recovery intent and counters are the next
  scheduler correctness step.
- Robot Gateway restart still marks its own non-terminal queue records `lost`
  rather than replaying physical actions. The mission controller correctly
  blocks downstream work, but robot-local action resumption requires separate
  ROS/Nav2-aware design.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 19:29:40 +08 - Revision-aware mission dispatcher

### Task goal

Turn a validated `plan:2` from a passive planning artifact into continued
physical execution. Preserve valid progress, stop and fence superseded robot
tasks, dispatch only dependency-ready nodes, and persist enough runtime state
to audit the transition.

### OpenClaw analogue inspected

- `openclaw/src/agents/embedded-agent-runner/run/attempt-recovery.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt.run-decisions.ts`

Reused structure: the host runtime, not the model, owns attempt terminal
classification, interruption state, bounded recovery, and serialization of
side effects. FireClaw adapts this to revisioned robot DAGs and requires
physical cancellation confirmation before a replacement action is dispatched.

### Files added

- `src/fireclaw_core/mission/revision_dispatcher.py`
  - `MissionNodeExecution`: physical execution state separate from the
    planner's cognitive graph.
  - `RevisionDispatchReconciler`: deterministic carry, preserve, cancel,
    fence, and pending decisions.
  - `MissionDispatchCheckpoint` and `JsonlMissionDispatchStore`: append-only
    execution checkpoints with an in-process write/read lock.
  - Stable execution-spec and completion-contract hashes.
- `tests/test_revision_dispatcher.py`

### Files modified

- `src/fireclaw_core/mission/mission_scheduler.py`
  - Persists a checkpoint before initial dispatch and before revision side
    effects.
  - Detects explicit invalidation events before every parallel peer reaches a
    terminal state.
  - Reconciles the active graph against the revised graph.
  - Requests cancellation for changed or removed active nodes, fences their
    old robot task IDs, and waits up to 5 seconds for an actual terminal state.
  - Blocks replacement dispatch if checkpointing, cancellation acknowledgement,
    or cancellation termination fails.
  - Executes revised graphs by explicit DAG dependencies and dispatches only
    ready nodes.
  - Supports another bounded revision during revised-plan execution.
- `src/fireclaw_core/mission/mission_agent.py`
  - Carries the plan-revision memory event into new subtask lineage.
  - Exposes `revision_dispatch`, cancellation evidence, `active_plan_id`,
    `active_task_graph`, and final node executions.
- `tests/test_mission_scheduler.py`
- `tests/test_mission_plan_revision.py`

### Reconciliation rules

- Completed node:
  - Carry only when the same node ID has the same command, target, capability,
    expected effects, success-evidence contract, and risk level.
  - Robot assignment may change because the physical outcome is already
    complete.
  - Never carry the node explicitly named by the invalidation event.
- Active node:
  - Preserve only when the full execution specification is unchanged,
    including robot assignment, dependencies, preconditions, resources,
    timeout, and recovery policy.
- Changed or removed active node:
  - Persist its old task ID as fenced.
  - Send cancellation.
  - Wait for a terminal state before dispatching plan:2.
- Failed, cancelled, invalidated, or changed node:
  - Return to pending when it exists in the revised graph.
- Late old-plan result:
  - Remains in mission history but cannot satisfy a plan:2 node because the
    dispatcher uses the new plan/task execution identity and persisted fence.

### Concrete verified scenarios

1. Single failed `task-1`: robot-a reports route blocked; plan:2 assigns
   robot-b; scheduler dispatches robot-b and finishes `succeeded`.
2. `task-1` succeeded and `task-2` failed: plan:2 changes both robot
   assignments; `task-1` is carried and only robot-b's `task-2` is submitted.
3. Parallel `task-1` fails while `task-2` is still running: the poller returns
   early, cancels `task-2`, waits for its terminal state, fences its old task
   ID, and only then dispatches plan:2. The test deliberately makes the old
   task report late success; that result does not complete the revised node.
4. Checkpoint storage failure: scheduler returns `blocked` before making any
   robot submission.
5. Revision audit failure: active graph remains plan:1 and no plan:2 robot
   task is submitted.

### Verification

Focused revision/scheduler/agent regression:

```text
30 passed in 0.29s
```

Final full suite outside the localhost-socket-restricted sandbox:

```text
1680 passed, 6 skipped in 121.61s
```

Also passed:

```text
python -m compileall -q src/fireclaw_core
git diff --check
```

### Current limitations and next recommended step

- Checkpoints are persisted, but a fresh MissionAgent process does not yet
  hydrate `active_task_graph` and resume polling from the latest checkpoint.
- Robot invalidation delivery still uses mission-trace polling rather than a
  push/SSE subscription, although polling now stops early on an explicit
  invalidation payload.
- Revised DAG execution currently supports floor-targeted `MissionSubtask`
  dispatch. Pose/area/entity-only targets need a richer robot task contract.
- A revised node that fails without producing a typed invalidation event ends
  the revised DAG as failed; node-level retry/reassign policies are not yet
  applied by the DAG runner.
- Node IDs are currently positional (`task-1`, etc.). Conservative contract
  hashes prevent unsafe carry, but planner-issued stable logical task IDs
  would make revisions more robust when nodes are inserted or reordered.

Next implement restart hydration from `MissionDispatchCheckpoint`, followed by
node-level recovery policies in the revised DAG runner. Push invalidation
delivery and stable planner task identities are the next architecture gaps.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.

## 2026-07-28 21:11:58 +08 - Future multi-robot scheduling algorithm

### User decision

The current `MissionGraphCompiler` robot allocation is a deterministic greedy
heuristic. Record robot allocation as a future scheduling-algorithm extension
point rather than treating the current score as the final design.

### Intended architecture

Keep the existing deterministic safety filter before optimization:

- enabled, online, and non-stale;
- emergency stop inactive;
- required capability available in both registry and snapshot;
- execution capacity available;
- target reachable;
- exclusive robot resource not already reserved.

After filtering, replace the hard-coded lexicographic greedy score with a
pluggable allocator/scheduler interface. Keep the current greedy allocator as:

- deterministic engineering baseline;
- fallback when an optimizer times out or has insufficient inputs;
- ablation baseline for research evaluation.

Candidate future implementations include:

- weighted bipartite matching for independent tasks;
- min-cost flow for capacity-constrained assignment;
- MILP/CP-SAT for task dependencies, deadlines, battery, travel cost, risk,
  resources, and multi-robot constraints;
- online or rolling-horizon scheduling when snapshots and route conditions
  change during execution.

The optimizer must return a candidate assignment only. Safety validation,
resource fencing, execution confirmation, and fail-closed behavior remain
owned by deterministic FireClaw runtime code and must not be delegated to an
LLM or bypassed by the optimizer.

### Evaluation requirements

Compare scheduling algorithms on:

- mission success rate;
- makespan and response latency;
- travel distance and energy consumption;
- workload balance;
- deadline misses;
- unsafe/infeasible assignment rejection;
- replanning frequency and computation time;
- performance under robot loss, route blockage, and stale state.

This direction can become a research contribution only if the scheduling
objective, uncertainty model, online adaptation, and experiments go beyond a
simple replacement of the greedy score.

## 2026-07-28 21:31:29 +08 - Typed completion contracts and evidence validation

### Task goal

Implement the next planning capability layer discussed with the user:

- a `TaskTypeRegistry` that defines machine-readable semantics for supported
  mission task types;
- a deterministic `CompletionContractCompiler` that turns an LLM graph node
  into enforceable completion requirements;
- an `ExecutionEvidenceValidator` that rejects robot-reported success when
  the required task, skill, result, or target evidence is missing.

The safety goal is to stop treating a terminal `status: succeeded` field as
sufficient proof that a physical task was completed.

### OpenClaw analogue inspected

Used CodeGraph before implementation to inspect:

- `openclaw/src/agents/agent-tool-definition-adapter.ts`
- OpenClaw tool-result normalization and post-tool hook flow, including
  `normalizeToolExecutionResult`, `after_tool_call`, and
  `tool_result_persist`.

Structure reused:

- the host runtime, not the model, owns raw result normalization;
- validation runs after tool execution and before the result becomes trusted
  persisted state;
- failures are converted into explicit structured results.

FireClaw-specific adaptation:

- OpenClaw can normalize a software tool result but does not need to prove
  that a physical robot reached the requested target or observed the required
  outcome;
- FireClaw therefore adds typed task semantics and authoritative execution
  evidence checks at the central scheduler boundary.

### Implementation

Added `src/fireclaw_core/mission/completion_contract.py`:

- `TaskTypeDefinition` and `TaskTypeRegistry`;
- default definitions for `victim_search`, `navigation`,
  `fire_suppression`, `reconnaissance`, `patrol`, and `transport`;
- per-type allowed capabilities, successful skill names, timeout, recovery
  policy, risk level, and optional result criteria;
- `CompletionContractCompiler`;
- `EvidenceObservation`, `EvidenceRequirementCheck`, and
  `EvidenceValidationResult`;
- `ExecutionEvidenceValidator`;
- criteria operators `eq`, `in`, `contains`, `gte`, `lte`, `approx`, and
  `present`, plus optional freshness checks.

Extended task graph and compatibility data:

- `MissionEvidenceRequirement` now persists structured `criteria`;
- `MissionSubtask` now carries `completion_contract`;
- structured robot-task constraints include that contract;
- `task_graph_from_mission_plan` preserves typed evidence, timeout, recovery,
  and risk when rebuilding the graph;
- legacy subtasks with no contract retain the old generic terminal-success
  requirement for compatibility;
- once a subtask declares a contract, an empty evidence list or mismatched
  task type/completion goal fails closed.

Integrated the compiler:

- `MissionGraphCompiler` compiles every proposed node through the task
  registry before allocation and graph creation;
- an unknown task type or capability/task-type mismatch rejects compilation;
- the LLM graph-proposal schema constrains `task_type` to the registered
  default task types;
- compiled evidence and policy are copied into both `MissionTaskGraph` and
  compatibility `MissionPlan` forms.

Integrated runtime evidence validation:

- `MissionScheduler._update_execution_states` validates every reported
  succeeded/completed node against the active graph node's contract;
- a valid result stores the full per-requirement validation result;
- a nominal success with missing evidence is rewritten to `status: block`,
  preserves `reported_status: succeeded`, and enters normal failure policy;
- validation results are persisted in `MissionNodeExecution` checkpoints so
  restart/revision logic can audit why a node was or was not accepted;
- reassign, task-graph continuation, and revision carry paths preserve the
  compiled contract and evidence record.

Concrete `victim_search` contract for floor 2 now requires:

1. the execution monitor observed a terminal success;
2. `search_for_victims` itself succeeded;
3. the skill returned an integer `victims_found >= 0`;
4. robot output confirmed `floor == 2`.

A robot returning only `status: succeeded`, or returning search results for
floor 3, is not accepted as completion.

### Files added

- `src/fireclaw_core/mission/completion_contract.py`
- `tests/test_completion_contract.py`

### Files modified in this step

- `src/fireclaw_core/mission/task_graph.py`
- `src/fireclaw_core/mission/graph_proposal.py`
- `src/fireclaw_core/mission/mission_planner.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/revision_dispatcher.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `src/fireclaw_core/task/task_contract.py`
- `tests/test_mission_graph_proposal.py`

### Verification

Focused new and compiler tests:

```text
14 passed in 0.20s
```

Planning/task-graph/scheduler/revision regression:

```text
67 passed in 0.40s
```

After the final fail-closed contract projection check:

```text
51 passed in 0.30s
```

The first full suite run inside the restricted sandbox produced 119
localhost-socket permission failures:

```text
1587 passed, 119 failed, 6 skipped in 59.47s
```

This was confirmed as environmental by rerunning outside the socket-restricted
sandbox. Final complete result after all edits:

```text
1707 passed, 6 skipped in 125.59s
```

Also passed:

```text
git diff --check
```

### Engineering and research conclusion

Engineering correctness:

- FireClaw now distinguishes "the robot process returned success" from "the
  requested physical subtask satisfied its compiled acceptance contract";
- the planner cannot weaken completion requirements by writing arbitrary
  prose, choosing an unknown task type, or pairing a task type with an
  unrelated capability;
- legacy plans remain operational, while new graph-compiled plans receive
  strict typed validation.

Research validity:

- this improves safety, auditability, and interpretability, but the current
  registry contents and evidence extractors are engineered domain contracts,
  not a publication-level novel method by themselves;
- stronger research claims require uncertainty-aware evidence fusion,
  provenance/authenticity guarantees, temporal consistency, disagreement
  handling across sensors/robots, and evaluation against false-positive
  completion under noisy or adversarial reports.

### Remaining gaps and next recommended step

- Contracts currently cover a small built-in task catalogue; expand through a
  versioned plugin/config registration API rather than hard-coding every
  firefighting behavior.
- `robot_gateway` evidence is structurally validated but not yet
  cryptographically attested or cross-checked against independent sensor
  sources.
- Task-scoped traces reduce stale evidence risk, but compiled defaults do not
  yet set per-evidence freshness windows.
- The scheduler's legacy failure-policy loop is still globally configured;
  compiled node recovery policy is not yet the sole policy source.
- The revised-DAG runner still needs node-level retry/reassign/replan behavior.
- Completion rejection should eventually emit a typed invalidation event so
  the central planner can revise the plan rather than only escalate through
  the generic failure policy.

Next recommended implementation: add typed completion-rejection execution
events and connect each node's compiled recovery policy to the revised-DAG
runner. After that, implement provenance-aware multi-source evidence fusion.

No commit was requested or made. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.
