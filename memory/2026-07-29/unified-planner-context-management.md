# Unified Planner Context Management

## Task goal

Implement model-tokenizer-level budgeting, long-session semantic compaction,
and one context-management policy for the central mission LLM planner and the
robot-local LLM planner.

## Resume state

- Timestamp: 2026-07-29, Asia/Ulaanbaatar timezone.
- Recent planning work already provided frozen state snapshots, belief
  projection, bounded deliberation, RAG/task-assumption grounding, graph
  compilation, revision dispatch, completion contracts, and restart recovery.
- The remaining context gap was that the first context envelope used a
  serialized-character budget and covered central mission planning only.

## OpenClaw analogues inspected

- `openclaw/src/agents/context-window-guard.ts`
  - effective context window resolution and warning/block boundaries.
- `openclaw/packages/agent-core/src/harness/compaction/compaction.ts`
  - token estimation, retention of a recent suffix, and summary boundaries.
- `openclaw/packages/normalization-core/src/cjk-chars.ts`
  - CJK-aware fallback estimation.
- `openclaw/src/agents/embedded-agent-runner/tool-result-context-guard.ts`
  - tool-result context protection.

FireClaw reuses the model-window guard and recent-history retention shape, but
uses deterministic structured compaction instead of an LLM summary because
robot planning requires stable provenance and must not turn a generated
summary into authoritative physical evidence.

## Implementation

### Shared context module

Added:

- `src/fireclaw_core/context/__init__.py`
- `src/fireclaw_core/context/manager.py`

The module provides:

- `HuggingFaceTokenCounter` for an injected or locally available concrete
  tokenizer;
- `CjkHeuristicTokenCounter` as a conservative recorded fallback;
- `StructuredSemanticCompactor` that extracts bounded task/status/location/
  risk facts from old records and records source refs plus a SHA-256 digest;
- `ModelAwareContextManager` that counts full messages and tool schemas,
  reserves output and safety space, admits advisory context by section order,
  and blocks without truncating authoritative context.

The model input formula is:

```text
context_window - output_reserve - safety_margin
```

Actual input usage is stored in the host manifest rather than fed back into
the prompt, avoiding a self-referential token-count mismatch.

### Model metadata and configuration

- `ModelDescriptor` now accepts optional `tokenizer_id`.
- `planner_builder.py` loads an optional `ModelCatalog` for central and
  robot-local provider runtimes.
- Mission CLI and server assembly accept `--catalog`.
- Robot-local gateway accepts `--robot-agent-catalog`.
- TOML supports `[provider].catalog`; `[robot_agent.provider].catalog`
  overrides it and otherwise inherits it.
- Tokenizers are loaded with `local_files_only=True`; planning does not
  trigger network downloads.

### Central mission planner

- `MissionPlanningContextManifest` carries a model-context manifest.
- `LLMMissionPlanner` fits each bounded deliberation request through the
  shared manager and counts the complete system prompt, user envelope, and
  tool schemas.
- Retrieved mission memory is semantically compacted.
- Critical-context overflow returns a fail-closed escalation before calling
  the provider.
- The selected model budget determines `max_tokens`.
- The deliberation trace persists the model context manifest.

### Robot-local planner

- `LLMRobotAgentPlanner` uses the same manager and trust-tiered envelope.
- Authoritative content includes the task contract, current robot/environment
  state, sensors, skill inventory, and skill metadata.
- Session history and entity-memory query results are advisory and compactable.
- A memory-tool round is rebuilt and recounted before the final plan call;
  raw unbounded tool transcripts are not carried forward.
- `RobotLocalPlan` and runtime events persist the context manifest.
- Gateway planning context now includes up to 50 recent session records.

### Interactive local planner compatibility

`PlannerContext` now carries a standardized context envelope and manifest.
Older interactive records are compacted deterministically while the five most
recent records remain whole. Legacy fields are retained for existing
deterministic planners.

## Commands and results

Focused integration tests before final configuration wiring:

```text
86 passed in 0.64s
```

Final focused command:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_context_manager.py \
  tests/test_model_catalog.py \
  tests/test_planner_builder.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_robot_agent_planner.py \
  tests/test_llm_deliberation_policy.py
```

Result:

```text
42 passed in 1.60s
```

Also passed:

```text
python3.10 -m compileall -q src <changed tests>
git diff --check
```

## Engineering conclusion

FireClaw now has one host-enforced context policy for both planning levels.
The LLM sees explicit source/trust sections, but cannot promote memory or RAG
into current physical truth. Long history is bounded before provider calls,
and safety-critical state is either preserved whole or the call is blocked.

## Research conclusion

This is engineering infrastructure rather than a standalone publication-level
novelty. It creates the controlled context substrate needed to study useful
research questions: safety-aware context selection, uncertainty-dependent
memory retention, cross-robot context allocation, and the relationship
between compression, planning success, unsafe action rate, latency, and token
cost.

## 2026-07-29 final regression

The full host-side non-ROS suite was run with local socket access:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests -m 'not ros1_smoke'
```

Result:

```text
1754 passed, 6 deselected in 127.97s
```

## Remaining uncertainty and next step

- Exact accounting depends on a compatible locally available tokenizer;
  fallback estimates should be calibrated against deployed models.
- The structured compactor covers a bounded fact vocabulary and can lose
  unusual but useful semantics from old records.
- Fallback provider chains do not yet reserve against the smallest context
  window in the whole chain.
- Recommended next research/engineering step: build context-quality
  evaluation fixtures and ablations comparing no history, raw recent history,
  structured compaction, and retrieval-selected history on long rescue
  missions.

No commit was requested. The unrelated
`memory/2026-07-28/codex-usage-reset-skill-check.md` was not modified.
