# P0 Robot Execution Authority

## 2026-07-29 19:05 +08

### Task goal

Implement the three P0 findings from the FireClaw/OpenClaw architecture audit:

1. replace `operator_confirmed=True` with exact, auditable execution authority;
2. replace independent JSONL authoritative state with transactional SQLite WAL;
3. enforce `PhysicalSkillPlugin.resource_locks` at the Adapter side-effect boundary.

### User decisions

- Implement all three P0 items before the P1 plugin host / shared Agent Harness work.
- Inspect and adapt OpenClaw first; do not invent unrelated APIs.
- Robot Agent remains safety-constrained and does not treat natural-language
  confirmation as authority.

### OpenClaw analogues inspected

- `openclaw/src/agents/bash-tools.exec-approval-request.ts`
  - approval request is registered server-side before the pending response is
    exposed, preventing an approve-before-registration race.
- `openclaw/src/infra/exec-approvals-authorization.ts`
  - delayed authorization is revalidated against current policy and committed
    under a transaction.
- `openclaw/src/infra/exec-approvals-store.ts`
  - SQLite authority, fail-closed parsing, write transactions, and compare-and-
    swap restore.
- `openclaw/src/sessions/session-lifecycle-admission.ts`
  - ordered admission/locking and lifecycle mutation ownership.
- `openclaw/src/gateway/server-methods/sessions-mutations.ts`
  - stale revision rejection.
- `openclaw/src/process/command-queue.ts`
  - stable lane keys and generation-fenced ownership so stale owners cannot
    release newer work.

### FireClaw adaptation

- OpenClaw command approval became `ExecutionAuthorization`, bound to:
  original command, structured task without the grant field, robot identity,
  exact ordered skill set, canonical input hashes, operator, expiry, nonce,
  policy ID, and HMAC signature.
- Natural-language `确认执行` no longer creates authority. The Gateway first
  persists an exact `AuthorizationRequest`; a supervisor with
  `safety.override` can sign it. The Agent recomputes the exact scope before
  SafetyGate and execution.
- Authorization use is consumed before the physical side effect. Repeating the
  same operation ID fails closed; unknown crash outcomes require
  reconciliation instead of automatic replay.
- OpenClaw's local state transaction pattern became one robot-local SQLite WAL
  authority for tasks, task transitions, events, loop checkpoints, approvals,
  execution grants, grant use, runtime flags, secrets, and resource leases.
  JSONL remains an fsync-backed audit/export mirror only.
- OpenClaw's lane ownership generation became a persistent per-robot resource
  lease generation. Executor release includes the generation fence, so a stale
  executor cannot delete a reassigned lease.
- Emergency stop closes persistent resource admission before cancellation and
  Adapter emergency-stop dispatch.

### Files added

- `src/fireclaw_core/approval/execution_authorization.py`
- `src/fireclaw_core/infra/runtime_state.py`
- `tests/test_execution_authorization.py`
- `tests/test_authoritative_runtime_state.py`
- `tests/test_resource_leases.py`
- `tests/test_gateway_execution_authorization.py`

### Files modified for this task

- `src/fireclaw_core/agent/agent.py`
- `src/fireclaw_core/execution/executor.py`
- `src/fireclaw_core/gateway/control.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/gateway/config.py`
- `src/fireclaw_core/safety/safety.py`
- `src/fireclaw_core/task/task_contract.py`
- `tests/test_agent.py`
- `tests/test_cli.py`
- `tests/test_gateway.py`
- `tests/test_gateway_robot_agent_cli.py`
- `tests/test_physical_skill_plugin.py`
- `README.md`
- `fireclaw.example.toml`
- architecture alignment and physical skill runtime documents.

### Important implementation details

- SQLite uses `journal_mode=WAL`, `synchronous=FULL`, foreign keys,
  `busy_timeout=5000`, `BEGIN IMMEDIATE`, revision CAS, and terminal overwrite
  rejection.
- Database and JSONL audit mirror permissions are forced to `0600`.
- Audit mirror writes run only after DB commit and call `flush` plus `fsync`.
- EventBus publication is deferred until the enclosing DB transaction commits.
- Pending approval registration and the externally visible
  `awaiting_confirmation` result commit in one transaction.
- A custom Gateway `memory_path` defines the implicit event/task/runtime storage
  namespace unless explicit paths are supplied.
- `--runtime-state-path` and `[robot_gateway].runtime_state_path` are supported.

### Validation already run

- Focused P0 and legacy unit set: `71 passed`.
- Gateway approval restart integration and P0 set: `13 passed`.
- Physical skill resource integration: `9 passed`.
- Agent/CLI/config/P0 set after compatibility updates: `82 passed`.
- Full suite before the final race/fencing refinements:
  `1810 passed, 9 failed, 6 deselected`; all 9 failures were subsequently
  addressed.
- Former nine failures rerun: `9 passed`.
- Expanded Gateway run exposed one approval-registration race; after moving
  registration before pending visibility, focused rerun: `4 passed`.
- `python3.10 -m compileall -q src/fireclaw_core`: passed.
- `git diff --check`: passed before the latest documentation/fencing edits and
  must be rerun.

### Current conclusion

The three P0 defects now have architectural fixes rather than boolean or
in-process-only patches. The remaining work in this session is final full-suite
validation, removal of test-generated SQLite artifacts, and a final diff/status
review. Do not commit unless the user explicitly asks.

### Research impact

This is engineering correctness and safety infrastructure, not a standalone
publication contribution. It becomes research-relevant only if evaluated as a
formal evidence-bound execution protocol under approval races, process crashes,
stale writes, resource contention, expired leases, and emergency-stop faults,
with comparisons against boolean approval and in-memory locking baselines.

### Next recommended step after P0

Resume the architecture audit's P1 work:

1. unify the four registration systems under one Plugin Host contribution
   model, following OpenClaw's plugin registry ownership/lifecycle shape;
2. then lift model/tool assembly and response parsing into a shared Agent
   Harness above `BoundedAgentLoop`.

## 2026-07-29 19:13 +08 final validation

- A full-suite run exposed two concurrency regressions:
  - a task row could become visible between SQLite commit and EventBus
    after-commit publication;
  - an outer approval/result transaction reversed the existing
    `_task_lock -> database transaction` order used by cancellation.
- Final fix:
  - `SqliteAuthoritativeRuntimeStore` now holds a per-store visibility lock
    through commit and all after-commit callbacks; reads use the same lock;
  - approval registration remains committed before the pending task result,
    matching OpenClaw's two-phase registration;
  - approval also verifies that the persisted task result is actually
    `awaiting_confirmation`, so an orphan request cannot authorize execution;
  - the outer transaction that caused lock inversion was removed.
- Focused concurrency rerun: `5 passed`.
- Final full suite:
  - command:
    `/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest tests -m 'not ros1_smoke' -q --tb=short`
  - result: `1822 passed, 6 deselected in 149.83s`.
- Removed only the two untracked SQLite artifacts created by early test runs:
  `memory/fireclaw-demo-tasks.sqlite3` and
  `memory/fireclaw-gateway-tasks.sqlite3`.
- The full run also revealed two tests that constructed a Gateway with fully
  default storage. They now use `tmp_path`, preventing future test runs from
  creating `memory/fireclaw-gateway-runtime.sqlite3`.
- Storage-isolation rerun: `41 passed in 4.01s`; `git status --short` contains
  no generated SQLite runtime artifact.
