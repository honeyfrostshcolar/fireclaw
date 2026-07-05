# Workspace Skill Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Allow FireClaw to discover project-level external skills from `skills/**/*.skill.json`, register them alongside built-in dry-run skills, list them through the agent, and execute them through the existing subprocess runtime.

**Architecture:** Keep `fireclaw_core` lightweight. External robotics, CUDA, RL, or perception algorithms should live outside the core package as subprocess skills described by JSON manifests. The core loads manifests, validates them, registers the resulting `Skill` objects, and lets the existing executor call them through the same skill interface.

**Tech Stack:** Python 3.11, `pytest`, JSON manifests, subprocess skill runtime, dataclasses.

---

### Task 1: Workspace Skill Discovery

**Files:**
- Create: `src/fireclaw_core/workspace_skills.py`
- Test: `tests/test_workspace_skills.py`

- [x] **Step 1: Write failing discovery tests**

Create tests that build a temporary `skills/` directory containing:

- one valid `*.skill.json` manifest;
- one nested valid `*.skill.json` manifest;
- one unrelated JSON file;
- one invalid manifest.

Expected behavior:

- valid manifests are loaded;
- unrelated files are ignored;
- invalid manifests are reported as load errors instead of crashing discovery.

- [x] **Step 2: Run discovery tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workspace_skills.py -v
```

Expected: fails because `fireclaw_core.workspace_skills` does not exist.

- [x] **Step 3: Implement workspace skill discovery**

Implement:

- `WorkspaceSkillLoadResult`
- `WorkspaceSkillLoadError`
- `load_workspace_skills(root: str | Path) -> WorkspaceSkillLoadResult`

The loader should scan `root/**/*.skill.json`, sort paths deterministically, call `load_subprocess_skill_from_manifest(...)`, collect valid skills, and collect structured errors for invalid manifests.

- [x] **Step 4: Run discovery tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_workspace_skills.py -v
```

Expected: all workspace discovery tests pass.

### Task 2: Registry Merge Support

**Files:**
- Modify: `src/fireclaw_core/skills.py`
- Test: `tests/test_execution.py`

- [x] **Step 1: Write failing registry merge tests**

Add tests for:

- adding an external skill to an existing registry;
- rejecting duplicate skill names by default;
- allowing explicit override only when requested.

- [x] **Step 2: Run registry tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: fails because registry merge support does not exist.

- [x] **Step 3: Implement registry merge support**

Add methods to `SkillRegistry`:

- `register(skill: Skill, *, replace: bool = False) -> None`
- `extend(skills: list[Skill], *, replace: bool = False) -> None`

Duplicate names should raise `ValueError` unless `replace=True`.

- [x] **Step 4: Run registry tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_execution.py -v
```

Expected: all execution/registry tests pass.

### Task 3: Agent Integration for Workspace Skills

**Files:**
- Modify: `src/fireclaw_core/agent.py`
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_cli.py`

- [x] **Step 1: Write failing agent integration tests**

Add tests that:

- construct `FireClawAgent(..., workspace_skills_dir=path)`;
- verify `你有哪些技能` includes both built-in and workspace skills;
- verify invalid workspace skill manifests appear in a `skill_load_errors` field;
- verify no skill listing query writes memory.

- [x] **Step 2: Run agent integration tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_cli.py -v
```

Expected: fails because agent/CLI do not accept `workspace_skills_dir`.

- [x] **Step 3: Implement agent and CLI integration**

Add:

- `workspace_skills_dir: str | Path | None = None` to `FireClawAgent`;
- default CLI option `--skills-dir skills`;
- optional `--no-workspace-skills` CLI flag;
- `skill_load_errors` in skill listing output.

Keep workspace skill loading non-fatal: invalid manifests should not stop the agent from running built-in skills.

- [x] **Step 4: Run agent integration tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py tests/test_cli.py -v
```

Expected: all agent and CLI tests pass.

### Task 4: Example External Skill

**Files:**
- Create: `skills/examples/echo_policy.skill.json`
- Create: `skills/examples/echo_policy.py`
- Test: `tests/test_workspace_skill_example.py`

- [x] **Step 1: Write failing example skill test**

Create an integration test that loads `skills/examples/echo_policy.skill.json`, runs the skill through the registry, and verifies the subprocess returns structured JSON.

- [x] **Step 2: Run example skill test and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_workspace_skill_example.py -v
```

Expected: fails because the example skill files do not exist.

- [x] **Step 3: Implement example external skill**

Create:

```text
skills/examples/echo_policy.skill.json
skills/examples/echo_policy.py
```

The script should:

- read JSON from stdin;
- return `{ "ok": true, "data": { ... } }`;
- include `dry_run: true`;
- not import FireClaw core.

The manifest should use:

```json
{
  "name": "echo_policy",
  "description": "Example external subprocess skill for algorithm wrappers.",
  "runtime": "subprocess",
  "command": ["../../.venv/bin/python", "echo_policy.py"],
  "timeout_seconds": 5,
  "dry_run_only": true
}
```

- [x] **Step 4: Run example skill test and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_workspace_skill_example.py -v
```

Expected: example skill test passes.

### Task 5: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `memory/2026-06-01/fireclaw-dry-run-core.md`

- [x] **Step 1: Document workspace skill workflow**

Update README with:

- `skills/**/*.skill.json` discovery;
- example manifest;
- wrapper script JSON stdin/stdout contract;
- how to point to conda/CUDA environments;
- safety note that shell string commands are rejected.

- [x] **Step 2: Run full test suite**

Run:

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass.

- [x] **Step 3: Run CLI demos**

Run:

```bash
.venv/bin/python -m fireclaw_core "你有哪些技能" --memory-path /tmp/fireclaw-demo-memory.jsonl
.venv/bin/python -m fireclaw_core "去二楼救人" --memory-path /tmp/fireclaw-demo-memory.jsonl
```

Expected:

- skill listing includes built-in skills and `echo_policy`;
- rescue command still returns `status="succeeded"`.

- [x] **Step 4: Update memory record**

Append implemented files, verification commands, test results, and remaining gaps to:

```text
memory/2026-06-01/fireclaw-dry-run-core.md
```
