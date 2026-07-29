# Active observation loop and context evaluation

## 2026-07-29T11:01:15+08:00

### Task goal

Continue improving FireClaw's planning maturity after unified context
management. Implement:

1. a deterministic evaluation baseline for context compaction/budgeting;
2. a safe planning-time active-observation loop;
3. structured observation promotion into a new belief projection and mission
   snapshot;
4. bounded replanning against the new snapshot.

### OpenClaw analogue inspected

- `openclaw/packages/agent-core/src/agent-loop.ts`
- `openclaw/AGENTS.md`
- `openclaw/src/agents/AGENTS.md`
- `openclaw/src/agents/embedded-agent-runner/run/AGENTS.md`
- `openclaw/src/agents/tools/AGENTS.md`

OpenClaw's reusable shape is the model/tool/result/model loop: execute a tool,
append the structured result, optionally prepare updated context, and continue
the next model turn. FireClaw adapts this boundary because a robot sensor or
physical skill cannot be exposed as an unrestricted LLM tool. The LLM only
proposes an observation; the host validates and compiles it, the robot-local
gateway executes it, and only a structured result can update authoritative
state.

### Files modified

- `src/fireclaw_core/context/evaluation.py`
- `src/fireclaw_core/context/__init__.py`
- `src/fireclaw_core/mission/active_observation.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `tests/test_context_evaluation.py`
- `tests/test_active_observation.py`
- `tests/test_llm_deliberation_policy.py`
- `README.md`

### Implemented behavior

#### Context evaluation

- `ContextEvaluationCase` freezes a benchmark input.
- `evaluate_context_case` counts the same request before and after context
  management.
- The report includes raw/managed input tokens, absolute/relative savings,
  compacted source count, omitted advisory count, and exact preservation checks
  for authoritative and continuity sections.
- This is an engineering baseline. It does not claim that a real LLM produces
  an equivalent plan; model-backed mission benchmarks are still required.

#### Active observation request

- New planner operation/tool: `request_observation`.
- The tool is exposed only after the planner has inspected an unresolved
  `belief_id`.
- The request contains `belief_id`, typed `MissionTarget`, approved observation
  capability, optional required sensor, and reason.
- The deliberation runtime remains read-only. It validates the request and
  terminates with `observation_required`; it never dispatches a robot.

#### Host compiler and execution boundary

- `MissionObservationCompiler` accepts only unresolved beliefs and the
  allowlisted capabilities `recon`, `monitor_environment`, and
  `search_for_victims`.
- Candidate robots must be enabled, online, non-stale, emergency-stop-free,
  unreserved, capable, sensor-compatible, reachable, and have task capacity.
- Allocation is deterministic: target-floor match, active task count, battery,
  then robot ID.
- The result is an existing typed `MissionSubtask` with a compiled completion
  contract. It travels through `MissionAgent.submit_subtask`, structured task
  generation, and the robot-local gateway/safety boundary.

#### Observation result and replanning

- The host accepts observations only from a successful structured task trace:
  top-level `observation`, `result.observation`, or a successful execution
  step's `output.observation`.
- Free text is never promoted to an environment fact.
- `belief_id`, subject, and kind must match the requested belief; confidence
  and evidence IDs are validated.
- The host assigns `source=robot:<robot_id>` instead of trusting a robot-supplied
  source field.
- The new fact is merged with current/provider facts, beliefs are recomputed
  atomically, `state:N+1` is persisted, and a new bounded deliberation starts.
- Default host limit: 2 active-observation rounds, 30 seconds per robot task,
  0.2-second poll interval.
- Each round records the request, compiled task, dispatch result, terminal
  status, structured fact, old snapshot ID, and new snapshot ID in the API
  result. Deliberation and snapshot records continue through existing memory
  paths.

### Verification executed

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_context_manager.py tests/test_context_evaluation.py
Result: 5 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_active_observation.py
Result: 5 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_llm_deliberation_policy.py tests/test_active_observation.py \
  tests/test_context_evaluation.py
Result: 15 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_context_manager.py tests/test_context_evaluation.py \
  tests/test_active_observation.py tests/test_mission_deliberation.py \
  tests/test_llm_deliberation_policy.py tests/test_mission_agent.py \
  tests/test_mission_state.py tests/test_belief_contract.py
Result: 125 passed

git diff --check
Result: clean

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests -m 'not ros1_smoke'
Result: 1761 passed, 6 deselected in 127.79s

Post-regression audit-persistence adjustment:

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_active_observation.py tests/test_mission_agent.py \
  tests/test_mission_deliberation.py tests/test_llm_deliberation_policy.py \
  tests/test_context_evaluation.py
Result: 106 passed
```

The end-to-end test verifies:

```text
mission-1:state:1
-> inspect unresolved west-stairs belief
-> request thermal observation
-> host selects robot-b
-> structured robot observation
-> host-attributed MissionEnvironmentFact
-> mission-1:state:2
-> resumed deliberation
-> final mission plan dispatch
```

### Current conclusion

FireClaw now has a bounded active-perception planning loop rather than a purely
passive frozen-snapshot planner. The safety boundary remains different from
OpenClaw: the LLM proposes evidence acquisition but cannot directly call
physical sensors or skills.

### Remaining uncertainty and next recommended step

- Run the full non-ROS regression suite after this record is written.
- Active observation is synchronous and in-process. A coordinator restart
  during the wait does not yet resume the observation continuation.
- General push-based sensor streams are not integrated.
- Observation confidence/source policies need calibration from simulator and
  robot logs.
- Context evaluation still needs model-backed task suites measuring plan
  validity, safety decisions, and plan consistency under compaction.
- The user plans to improve deterministic robot scheduling separately; do not
  replace the current allocation score with an LLM.

### Worktree note

`memory/2026-07-28/codex-usage-reset-skill-check.md` was already untracked and
is unrelated to this task. It was not modified.
