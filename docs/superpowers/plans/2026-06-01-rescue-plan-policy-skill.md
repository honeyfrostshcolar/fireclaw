# Rescue Plan Policy Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Allow FireClaw to embed a named external policy/algorithm skill into the multi-step rescue plan, so commands like `去二楼救人 使用 echo_policy` execute the policy skill through the normal planner, safety, executor, and memory flow.

**Architecture:** Extend the rule-based rescue planner to parse an optional policy skill reference. The planner should insert the policy skill as the first step of the rescue plan and pass it the target floor and original command. Safety remains responsible for checking whether the named policy skill exists. The executor remains unchanged because all skills share the same `Skill` interface.

**Tech Stack:** Python 3.11, `pytest`, existing planner/safety/executor/workspace skill loading.

---

### Task 1: Planner Parses Optional Rescue Policy Skill

**Files:**
- Modify: `src/fireclaw_core/planner.py`
- Test: `tests/test_planner.py`

- [x] **Step 1: Write failing planner tests**

Add tests for:

- `去二楼救人 使用 echo_policy`
- `去二楼救人 导航策略用 echo_policy`
- `去2楼救人 用 echo_policy`

Expected behavior:

- planner returns `status="planned"`;
- `intent="rescue_victim"`;
- `target_floor=2`;
- first step is `echo_policy`;
- `echo_policy` inputs include `{"floor": 2, "command": original_command}`;
- the five existing rescue steps remain after the policy step.

- [x] **Step 2: Run planner tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: new tests fail because policy skill references are not parsed.

- [x] **Step 3: Implement policy skill parsing**

Add helper methods to `RuleBasedPlanner`:

- `_extract_policy_skill(command: str) -> str | None`
- `_build_rescue_steps(floor: int, command: str, policy_skill: str | None) -> list[PlanStep]`

Skill names should allow letters, digits, `_`, `.`, and `-`.

- [x] **Step 4: Run planner tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py -v
```

Expected: all planner tests pass.

### Task 2: Agent Executes Mixed Rescue Plans

**Files:**
- Test: `tests/test_agent.py`

- [x] **Step 1: Write failing agent tests**

Add tests for:

- `去二楼救人 使用 echo_policy` with `workspace_skills_dir="skills"` succeeds;
- execution step order starts with `echo_policy`, then the five rescue skills;
- `echo_policy` output contains the floor payload;
- memory records the mixed plan.

Add a missing policy test:

- `去二楼救人 使用 missing_policy` blocks before execution;
- message mentions `Missing skill: missing_policy`;
- memory records `status="block"`.

- [x] **Step 2: Run agent tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: tests fail until planner inserts the policy skill.

- [x] **Step 3: Implement only if planner changes are insufficient**

The existing agent, safety gate, and executor should already support arbitrary plan steps. If tests pass after Task 1, no agent code changes are needed.

- [x] **Step 4: Run agent tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py -v
```

Expected: all agent tests pass.

### Task 3: CLI Mixed Rescue Plan

**Files:**
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing CLI tests**

Add tests that run:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人 使用 echo_policy" --memory-path <tmp>
```

Expected:

- command exits 0;
- result status is `succeeded`;
- execution first step is `echo_policy`;
- execution contains six steps total.

Add missing policy CLI test:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人 使用 missing_policy" --memory-path <tmp>
```

Expected:

- command exits nonzero;
- result status is `block`;
- message mentions missing policy skill.

- [x] **Step 2: Run CLI tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_cli.py -v
```

Expected: mixed rescue policy tests fail until planner behavior is complete.

- [x] **Step 3: Implement CLI changes only if needed**

The CLI should already load workspace skills by default and route all commands through `FireClawAgent`. If tests pass after planner changes, no CLI code changes are needed.

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

- [x] **Step 1: Document policy skill insertion**

Update README with examples:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人 使用 echo_policy"
.venv/bin/python -m fireclaw_core "去二楼救人 导航策略用 echo_policy"
```

Explain that the named policy skill is inserted into the rescue plan before navigation and must be registered.

- [x] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass.

- [x] **Step 3: Run CLI demos**

Run:

```bash
.venv/bin/python -m fireclaw_core "去二楼救人 使用 echo_policy" --memory-path /tmp/fireclaw-demo-memory.jsonl
.venv/bin/python -m fireclaw_core "去二楼救人 使用 missing_policy" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected:

- `echo_policy` mixed rescue plan returns `status="succeeded"` with six execution steps;
- `missing_policy` returns `status="block"` and nonzero exit.

- [x] **Step 4: Update memory record and mark plan complete**

Append implemented files, verification commands, test results, and remaining gaps to:

```text
memory/2026-06-01/fireclaw-dry-run-core.md
```

Then mark all plan checkboxes complete.

