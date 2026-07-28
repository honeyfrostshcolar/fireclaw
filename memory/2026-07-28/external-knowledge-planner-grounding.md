# External Knowledge Planner Grounding

## 2026-07-28 13:18:23 +08 - Interrupted task resumed and verified

### Task goal

Resume the uncommitted external-firefighting-knowledge grounding work left by
the interrupted prior session, determine whether it was complete, finish its
safety boundary, and validate the complete non-ROS repository.

### Starting state

- Branch: `master`, synchronized with `origin/master` before these uncommitted
  changes were created.
- The worktree already contained 15 modified and 3 untracked files for this
  feature.
- The newest memory record documented Python/RAG environment readiness but did
  not document this external-knowledge implementation, confirming that the
  prior session stopped before handoff and final verification.

### OpenClaw analogue inspected

CodeGraph was used before source inspection. The OpenClaw index was queried for
prompt assembly and memory-search boundaries, including:

- `openclaw/src/agents/memory-search.ts`
- `openclaw/src/agents/sessions/agent-session-prompting.ts`
- `openclaw/src/agents/embedded-agent-runner/run/attempt-prompt-assembly.ts`
- `openclaw/src/plugins/memory-state.ts`

The reusable shape is separation between retrieval/configuration and agent
prompt assembly. FireClaw keeps that separation but does not reuse OpenClaw's
chat/session assumptions. It adds typed source namespaces, bounded external
content, explicit non-authority flags, sensor revalidation requirements,
closed-set knowledge references, mission planning audit integration, and
existing deterministic Safety Gate validation for embodied execution.

### Implementation present and reviewed

- `src/fireclaw_core/rag/knowledge_grounding.py`
  - admits only `source_kind=external_knowledge` RAG hits;
  - requires a chunk identity, usable text, `allowed_use`, and source
    provenance;
  - produces advisory-only planner references that cannot authorize an action
    or assert current state;
  - bounds excerpts and deduplicates by knowledge ID.
- `src/fireclaw_core/memory/planner_memory_context.py`
  - retrieves external knowledge independently of mission-memory authority;
  - fail-softs retrieval errors into auditable warnings;
  - exposes accepted IDs and availability in the memory guard decision.
- `src/fireclaw_core/mission/mission_agent.py` and `mission_planner.py`
  - pass external references into `MissionPlannerContext`;
  - persist `knowledge_refs` in `MissionPlan.to_dict()` and plan memory events.
- `src/fireclaw_core/planner/llm_planner.py`
  - serializes external content as untrusted reference data in the system
    prompt;
  - tells the model not to execute document instructions or treat them as
    observations/authorization;
  - constrains and parser-validates `knowledge_refs` against the retrieved IDs;
  - records used references in the planner guard decision.
- `src/fireclaw_core/mission/mission_runtime.py`, `mission_cli.py`,
  `gateway/config.py`, and `gateway/serve.py`
  - provide a separate `[mission.knowledge_rag]` namespace and runtime wiring;
  - enforce `RagRuntimeConfig.source_kind == "external_knowledge"`.
- `src/fireclaw_core/rag/index_preparation.py`
  - labels prepared document chunks as `external_knowledge`.
- `docs/rag/external-knowledge-planner-grounding.zh-CN.md` and
  `fireclaw.example.toml`
  - document index preparation, configuration, and the planning safety
    boundary.

### Additional safety fix in this resume

The interrupted implementation bounded only the excerpt. That still allowed
arbitrarily large IDs, URLs, paths, titles, publisher names, and other metadata
to inflate the prompt, tool schema, and audit record.

Updated `knowledge_grounding.py` to:

- cap knowledge IDs at 256 characters;
- cap source URL/path values at 2048 characters;
- cap general metadata at 512 characters and language at 64 characters;
- reject records that lack bounded required provenance;
- reject reversed page ranges;
- normalize NaN/infinite retrieval scores to `0.0`.

Added a focused regression in `tests/test_external_knowledge_grounding.py` and
updated the Chinese grounding document.

### Commands and results

Initial focused regression outside the managed socket sandbox:

```text
87 passed in 18.09s
```

After the metadata-boundary fix:

```text
88 passed in 18.32s
```

Focused files were:

```text
tests/test_external_knowledge_grounding.py
tests/test_llm_planner.py
tests/test_mission_runtime.py
tests/test_mission_cli.py
tests/test_gateway_robot_agent_cli.py
tests/test_rag_index_preparation.py
```

Static verification:

```text
compileall over changed planner/memory/RAG/mission/gateway modules and tests: PASS
git diff --check: PASS
```

First full host-side non-ROS run:

```text
1619 passed, 1 failed, 6 deselected in 115.74s
```

The single failure was
`test_embodied_eval_records_structured_task_metadata`: the mission was terminal
but the sampled trace contained `structured_task=None`. It is outside the
external-knowledge path. The test passed alone, as a complete file, and after
all tests collected before it (`164` relevant passes total), so this is evidence
of an existing low-probability evaluation/trace timing issue rather than a
deterministic feature regression. No unrelated production change was made
without a stable reproduction.

Final full host-side non-ROS run after the safety fix:

```text
1621 passed, 6 deselected in 113.86s
```

The six deselected tests are `ros1_smoke`; no ROS master or real robot was
started.

### Current conclusion

Engineering correctness:

- External knowledge grounding is wired end to end for prebuilt BM25, dense,
  hybrid, and reranked RAG indexes.
- The complete non-ROS suite passes.
- External content remains advisory and cannot bypass the existing mission
  validator or robot Safety Gate.
- The implementation is still uncommitted; no commit was requested.

Research validity:

- This is necessary infrastructure for grounded planning and auditable
  provenance, but infrastructure alone is not a research contribution.
- The current tests establish contracts and safety isolation, not retrieval
  quality, reduced hallucination, or improved rescue outcomes.

Publication-level evidence still needed:

- a versioned real firefighting corpus with authority/license policy;
- retrieval metrics for BM25, dense, hybrid, and reranking;
- planner task-success, citation precision, unsupported-action, and stale-state
  error rates with and without grounding;
- prompt-injection/adversarial-document evaluation;
- ablations for provenance constraints and sensor revalidation;
- simulator scenarios followed by tightly controlled real-robot validation.

### Next recommended step

Review and commit this coherent external-knowledge feature group, then build a
small versioned firefighting corpus and run an end-to-end LLM planning study.
Track the one observed embodied-eval trace timing failure separately if it
reappears; capture the failed robot task trace and terminal event ordering
before changing that unrelated code path.
