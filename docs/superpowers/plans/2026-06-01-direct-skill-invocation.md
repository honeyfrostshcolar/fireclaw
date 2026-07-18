# Direct Skill Invocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let FireClaw handle natural-language commands that directly invoke a registered skill, including workspace skills such as `echo_policy`, through the same planner, safety gate, executor, and memory pipeline.

**Architecture:** Extend the rule-based planner so it can recognize direct skill invocation intent and generate a one-step `Plan` for a registered skill. Keep skill existence validation in the safety gate by passing the existing registry through the normal agent flow. Do not special-case workspace skills in the executor; built-in and workspace skills should use the same `Skill` interface.

**Tech Stack:** Python 3.11, `pytest`, existing planner/safety/executor/memory/skill registry.

---

### Task 1: Planner Direct Skill Invocation

**Files:**
- Modify: `src/fireclaw_core/planner.py`
- Test: `tests/test_planner.py`

- [x] **Step 1: Write failing planner tests**

Add tests for:

- `运行 echo_policy` producing a one-step plan with skill name `echo_policy`;
- `调用 echo_policy` producing the same plan;
- `运行 echo_policy 处理 二楼` passing a simple text payload into step inputs.

Expected plan shape:

```python
Plan(
    intent="direct_skill_invocation",
    steps=[
        PlanStep(
            skill_name="echo_policy",
            inputs={"text": "二楼"}
        )
    ],
)
```

For commands without a payload, inputs should be `{}`.

- [x] **Step 2: Run planner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: new tests fail because direct skill invocation is not parsed.

- [x] **Step 3: Implement direct skill parsing**

Update `RuleBasedPlanner.plan(...)` to detect patterns:

- `运行 <skill_name>`
- `调用 <skill_name>`
- `执行 <skill_name>`
- optional payload after `处理`, `输入`, or `参数`

Skill names should allow letters, digits, `_`, `.`, and `-`.

- [x] **Step 4: Run planner tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: all planner tests pass.

### Task 2: Safety and Agent Behavior for Direct Skill Invocation

**Files:**
- Modify: `src/fireclaw_core/safety.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_safety.py`

- [x] **Step 1: Write failing agent/safety tests**

Add tests for:

- direct invocation of loaded `echo_policy` succeeds through `FireClawAgent`;
- direct invocation of a missing skill is blocked before execution;
- blocked direct invocation writes memory with `status="block"`;
- non-dry-run direct invocation is blocked by the existing dry-run gate.

- [x] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_safety.py -v
```

Expected: loaded skill invocation may fail before planner support is complete, and missing skill behavior should be verified against safety gate output.

- [x] **Step 3: Adjust safety messages if needed**

Keep the safety gate as the authority for skill existence. If current missing-skill errors are already structured and clear, do not change behavior. If direct invocation returns confusing messages, update error text while preserving existing tests.

- [x] **Step 4: Run tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_safety.py -v
```

Expected: all agent and safety tests pass.

### Task 3: CLI Direct Skill Invocation

**Files:**
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI tests**

Add tests that run:

```bash
.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼" --memory-path <tmp>
```

Expected:

- command exits 0;
- result status is `succeeded`;
- execution has one step named `echo_policy`;
- output includes the payload text.

Also add a missing skill CLI test:

```bash
.venv/bin/python -m fireclaw_core "运行 missing_skill" --memory-path <tmp>
```

Expected:

- command exits nonzero;
- result status is `block`;
- message mentions missing skill.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: direct skill invocation tests fail until planner/agent behavior is complete.

- [x] **Step 3: Implement any CLI status handling needed**

The existing CLI should already return 0 for `succeeded` and nonzero for `block`. Only adjust if tests expose a mismatch.

- [x] **Step 4: Run CLI tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: all CLI tests pass.

### Task 4: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document direct skill invocation**

Update README with examples:

```bash
.venv/bin/python -m fireclaw_core "运行 echo_policy"
.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼"
```

Explain that direct skill invocation still passes through safety and memory.

- [x] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass.

- [x] **Step 3: Run CLI demos**

Run:

```bash
.venv/bin/python -m fireclaw_core "运行 echo_policy 处理 二楼" --memory-path /tmp/fireclaw-demo-memory.jsonl
.venv/bin/python -m fireclaw_core "运行 missing_skill" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected:

- `echo_policy` returns `status="succeeded"`;
- `missing_skill` returns `status="block"` and nonzero exit.

- [x] **Step 4: Update memory record**

Append implemented files, verification commands, test results, and remaining gaps to:

```text
memory/2026-06-01/fireclaw-dry-run-core.md
```

