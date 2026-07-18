# Primitive Allowed Skills Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve and enforce `allowed_skills` through the full primitive-composition path so robot-local LLM planning can safely compose primitive skills such as `navigate_to_floor`.

**Architecture:** `allowed_skills` becomes a first-class `StructuredRobotTask` field. Gateway validation accepts primitive-composition tasks with empty `required_skills` only when `allowed_skills` is non-empty, and `RobotAgentTaskEnvelope` uses that explicit allowlist for LLM planning and policy validation.

**Tech Stack:** Python 3.11, pytest, FireClaw `StructuredRobotTask`, `MissionAgent`, `FireClawGateway`, `RobotAgentRuntime`, robot profile validation.

---

## Current Failure

The current primitive fallback creates this payload:

```python
{
    "task_type": "primitive_composition",
    "required_skills": [],
    "allowed_skills": ["navigate_to_floor", "report_status"],
}
```

But `StructuredRobotTask.from_dict()` drops `allowed_skills`, gateway rejects empty `required_skills`, and `envelope_from_structured_task()` falls back to only `report_status` and `return_to_safe_zone`. So the robot-local LLM cannot actually choose `navigate_to_floor`.

---

## File Structure

- Modify `src/fireclaw_core/task/task_contract.py`
  - Add `allowed_skills` to `StructuredRobotTask`.
  - Adjust validation rules for `primitive_composition`.
- Modify `src/fireclaw_core/agent/robot_agent.py`
  - Build `RobotAgentTaskEnvelope.allowed_skills` from explicit task allowlist when present.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - Validate `allowed_skills` type in `/tasks` payloads.
- Modify `src/fireclaw_core/mission/mission_agent.py`
  - Keep primitive fallback `required_skills=[]`, but ensure generated tasks round-trip through `StructuredRobotTask`.
- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Validate `primitive_skills` against registry and metadata.
- Test files:
  - `tests/test_task_contract.py`
  - `tests/test_robot_agent_planner.py`
  - `tests/test_mission_agent.py`
  - `tests/test_robot_profile.py`
  - `tests/test_mission_gateway.py` or `tests/test_gateway_robot_profile_config.py`

---

### Task 1: Make `allowed_skills` A First-Class Structured Task Field

**Files:**
- Modify: `src/fireclaw_core/task/task_contract.py`
- Test: `tests/test_task_contract.py`

- [ ] **Step 1: Write failing round-trip test**

Create `tests/test_task_contract.py` if it does not exist, or append:

```python
from fireclaw_core.task.task_contract import StructuredRobotTask


def test_structured_task_preserves_allowed_skills():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor", "report_status"],
    })

    assert task.allowed_skills == ["navigate_to_floor", "report_status"]
    assert task.to_dict()["allowed_skills"] == ["navigate_to_floor", "report_status"]
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py::test_structured_task_preserves_allowed_skills -q
```

Expected: FAIL with `AttributeError` or missing `allowed_skills`.

- [ ] **Step 3: Add the field and parser**

In `src/fireclaw_core/task/task_contract.py`, update the dataclass:

```python
@dataclass(frozen=True)
class StructuredRobotTask:
    task_id: str
    task_type: str
    target: dict[str, Any]
    required_skills: list[str]
    allowed_skills: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    priority: str = "normal"
    risk_level: str = "low"
    operator_id: str | None = None
    mission_id: str | None = None
    robot_id: str | None = None
    command: str | None = None
```

Update `from_dict()`:

```python
allowed_skills=[str(item) for item in payload.get("allowed_skills") or []],
```

Place it after `required_skills=...`.

- [ ] **Step 4: Run test and verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py::test_structured_task_preserves_allowed_skills -q
```

Expected: PASS.

---

### Task 2: Validate Primitive Composition Correctly

**Files:**
- Modify: `src/fireclaw_core/task/task_contract.py`
- Test: `tests/test_task_contract.py`

- [ ] **Step 1: Write failing validation tests**

Append:

```python
from fireclaw_core.task.task_contract import validate_structured_robot_task


def test_primitive_composition_allows_empty_required_skills_when_allowed_skills_present():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor"],
    })

    assert validate_structured_robot_task(task) == []


def test_primitive_composition_rejects_empty_allowed_skills():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": [],
    })

    errors = validate_structured_robot_task(task)

    assert any("allowed_skills" in error for error in errors)


def test_required_skills_must_be_within_allowed_skills_when_allowlist_is_explicit():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "search",
        "target": {"floor": 2},
        "required_skills": ["search_for_victims"],
        "allowed_skills": ["navigate_to_floor"],
    })

    errors = validate_structured_robot_task(task)

    assert any("search_for_victims" in error and "allowed_skills" in error for error in errors)
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_task_contract.py::test_primitive_composition_allows_empty_required_skills_when_allowed_skills_present \
  tests/test_task_contract.py::test_primitive_composition_rejects_empty_allowed_skills \
  tests/test_task_contract.py::test_required_skills_must_be_within_allowed_skills_when_allowlist_is_explicit \
  -q
```

Expected: at least the primitive-composition tests FAIL under current validation.

- [ ] **Step 3: Implement validation rules**

Replace:

```python
if not task.required_skills:
    errors.append("required_skills must not be empty")
```

with:

```python
if task.task_type == "primitive_composition":
    if not task.allowed_skills:
        errors.append("allowed_skills must not be empty for primitive_composition")
else:
    if not task.required_skills:
        errors.append("required_skills must not be empty")
```

Add after priority/risk checks:

```python
if task.allowed_skills:
    allowed = set(task.allowed_skills)
    for skill_name in task.required_skills:
        if skill_name not in allowed:
            errors.append(f"required skill {skill_name!r} is not in allowed_skills")
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
.venv/bin/python -m pytest tests/test_task_contract.py -q
```

Expected: PASS.

---

### Task 3: Use Explicit `allowed_skills` In Robot Agent Envelopes

**Files:**
- Modify: `src/fireclaw_core/agent/robot_agent.py`
- Test: `tests/test_robot_agent_planner.py`

- [ ] **Step 1: Write failing envelope test**

Append to `tests/test_robot_agent_planner.py`:

```python
from fireclaw_core.agent.robot_agent import envelope_from_structured_task
from fireclaw_core.task.task_contract import StructuredRobotTask


def test_envelope_from_structured_task_uses_explicit_allowed_skills():
    task = StructuredRobotTask.from_dict({
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor"],
        "robot_id": "r1",
    })

    envelope = envelope_from_structured_task(task, fallback_robot_id="fallback")

    assert "navigate_to_floor" in envelope.allowed_skills
    assert "report_status" in envelope.allowed_skills
    assert envelope.required_skills == []
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py::test_envelope_from_structured_task_uses_explicit_allowed_skills -q
```

Expected: FAIL because `navigate_to_floor` is not preserved.

- [ ] **Step 3: Update envelope construction**

In `src/fireclaw_core/agent/robot_agent.py`, replace:

```python
allowed_skills = list(dict.fromkeys([*task.required_skills, *SAFE_SUPPLEMENTAL_SKILLS]))
```

with:

```python
base_allowed = task.allowed_skills if task.allowed_skills else task.required_skills
allowed_skills = list(dict.fromkeys([*base_allowed, *SAFE_SUPPLEMENTAL_SKILLS]))
```

- [ ] **Step 4: Run test and verify it passes**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_agent_planner.py::test_envelope_from_structured_task_uses_explicit_allowed_skills -q
```

Expected: PASS.

---

### Task 4: Make Gateway Accept Primitive Composition Payloads

**Files:**
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Test: `tests/test_mission_gateway.py` or `tests/test_gateway_robot_profile_config.py`

- [ ] **Step 1: Write failing gateway validation test**

Use the existing gateway test helper in `tests/test_mission_gateway.py` or `tests/test_gateway_robot_profile_config.py`. Add a test that POSTs `/tasks` with:

```python
payload = {
    "command": "导航到 x=2 y=0",
    "structured_task": {
        "task_id": "t1",
        "task_type": "primitive_composition",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor", "report_status"],
        "robot_id": "gazebo_turtlebot3",
    },
}
```

Expected assertion:

```python
assert response.status_code != 400
```

If the local test harness uses raw `urllib`, assert the response JSON status is not a validation error containing `required_skills must not be empty`.

- [ ] **Step 2: Run test and verify it fails**

Run the exact test you added:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway.py::test_gateway_accepts_primitive_composition_allowed_skills -q
```

If you placed it in `tests/test_gateway_robot_profile_config.py`, run that exact path instead.

Expected: FAIL with 400 validation error under current code.

- [ ] **Step 3: Validate `allowed_skills` type**

In `src/fireclaw_core/gateway/gateway.py`, near the existing `required_skills` type check, add:

```python
raw_allowed_skills = structured_task.get("allowed_skills")
if raw_allowed_skills is not None and not isinstance(raw_allowed_skills, list):
    self._write_error(handler, HTTPStatus.BAD_REQUEST, "allowed_skills must be a list")
    return
```

Do not special-case `primitive_composition` in gateway. Let `validate_structured_robot_task()` enforce semantics.

- [ ] **Step 4: Add bad-type gateway test**

Add:

```python
payload["structured_task"]["allowed_skills"] = "navigate_to_floor"
```

Expected response:

```python
assert response.status_code == 400
assert "allowed_skills must be a list" in response_text
```

- [ ] **Step 5: Run gateway tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_gateway.py tests/test_gateway_robot_profile_config.py -q
```

Expected: PASS.

---

### Task 5: Prove Mission Fallback Round-Trips Into Robot Agent Envelope

**Files:**
- Modify: `tests/test_mission_agent.py`

- [ ] **Step 1: Add round-trip assertion to existing fallback test**

In `test_plan_and_submit_dispatches_primitive_fallback_on_clarify`, after retrieving `structured_task`, add:

```python
from fireclaw_core.agent.robot_agent import envelope_from_structured_task
from fireclaw_core.task.task_contract import StructuredRobotTask

task = StructuredRobotTask.from_dict(structured_task)
envelope = envelope_from_structured_task(task, fallback_robot_id="r1")

assert "navigate_to_floor" in envelope.allowed_skills
assert "search_area" in envelope.allowed_skills
```

- [ ] **Step 2: Run test**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py::test_plan_and_submit_dispatches_primitive_fallback_on_clarify -q
```

Expected after Tasks 1-3: PASS.

- [ ] **Step 3: Add regression for no fallback when gateway-invalid**

Add:

```python
def test_primitive_fallback_structured_task_validates_after_round_trip():
    structured_task = {
        "task_id": "m1:r1:primitive-composition",
        "mission_id": "m1",
        "robot_id": "r1",
        "task_type": "primitive_composition",
        "command": "导航到 x=2 y=0",
        "target": {},
        "required_skills": [],
        "allowed_skills": ["navigate_to_floor", "report_status"],
        "risk_level": "low",
        "constraints": {"source": "mission_primitive_fallback"},
    }

    task = StructuredRobotTask.from_dict(structured_task)

    assert validate_structured_robot_task(task) == []
```

Import `validate_structured_robot_task` from `fireclaw_core.task.task_contract`.

- [ ] **Step 4: Run mission agent tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent.py -q
```

Expected: PASS.

---

### Task 6: Validate Profile `primitive_skills`

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing validation tests**

Append to `tests/test_robot_profile.py`:

```python
from dataclasses import replace


def test_profile_rejects_unknown_primitive_skill(existing_profile, registry):
    profile = replace(existing_profile, primitive_skills=("missing_skill",))

    errors = validate_robot_capability_profile(profile, registry)

    assert any("primitive skill 'missing_skill' is not registered" in error for error in errors)


def test_profile_rejects_composite_skill_in_primitive_skills(existing_profile, registry):
    profile = replace(
        existing_profile,
        primitive_skills=("search_for_victims",),
        enabled_skills=tuple(dict.fromkeys([*existing_profile.enabled_skills, "search_for_victims"])),
    )

    errors = validate_robot_capability_profile(profile, registry)

    assert any("not marked as primitive" in error for error in errors)


def test_profile_rejects_primitive_skill_not_enabled(existing_profile, registry):
    profile = replace(
        existing_profile,
        primitive_skills=("report_status",),
        enabled_skills=tuple(skill for skill in existing_profile.enabled_skills if skill != "report_status"),
    )

    errors = validate_robot_capability_profile(profile, registry)

    assert any("primitive skill 'report_status' is not in enabled_skills" in error for error in errors)
```

If the file does not have `existing_profile` and `registry` fixtures, create local objects using the same pattern as nearby validation tests.

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_robot_profile.py::test_profile_rejects_unknown_primitive_skill \
  tests/test_robot_profile.py::test_profile_rejects_composite_skill_in_primitive_skills \
  tests/test_robot_profile.py::test_profile_rejects_primitive_skill_not_enabled \
  -q
```

Expected: FAIL because `validate_robot_capability_profile()` does not inspect `primitive_skills`.

- [ ] **Step 3: Implement validation**

In `src/fireclaw_core/agent/robot_profile.py`, inside `validate_robot_capability_profile()` after `enabled_set = set(profile.enabled_skills)`, add:

```python
for skill_name in profile.primitive_skills:
    skill = registry.get(skill_name)
    if skill is None:
        errors.append(f"primitive skill {skill_name!r} is not registered")
        continue
    if skill_name not in enabled_set:
        errors.append(f"primitive skill {skill_name!r} is not in enabled_skills")
    if skill.metadata.get("kind") != "primitive":
        errors.append(f"primitive skill {skill_name!r} is not marked as primitive")
```

- [ ] **Step 4: Run robot profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: PASS.

---

### Task 7: Final Verification

**Files:**
- No new files.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_task_contract.py \
  tests/test_robot_agent_planner.py \
  tests/test_robot_agent_policy.py \
  tests/test_mission_agent.py \
  tests/test_robot_profile.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_skill_inventory.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run broader regression around gateway/mission**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_gateway.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_mission_agent.py \
  tests/test_robot_agent_planner.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run minimal smoke snippet**

Run:

```bash
.venv/bin/python - <<'PY'
from fireclaw_core.task.task_contract import StructuredRobotTask, validate_structured_robot_task
from fireclaw_core.agent.robot_agent import envelope_from_structured_task

payload = {
    "task_id": "m1:r1:primitive-composition",
    "mission_id": "m1",
    "robot_id": "r1",
    "task_type": "primitive_composition",
    "command": "导航到 x=2 y=0",
    "target": {},
    "required_skills": [],
    "allowed_skills": ["navigate_to_floor", "report_status"],
    "risk_level": "low",
    "constraints": {"source": "mission_primitive_fallback"},
}
task = StructuredRobotTask.from_dict(payload)
print("errors=", validate_structured_robot_task(task))
envelope = envelope_from_structured_task(task, fallback_robot_id="fallback")
print("allowed=", envelope.allowed_skills)
PY
```

Expected output contains:

```text
errors= []
allowed= ['navigate_to_floor', 'report_status', 'return_to_safe_zone']
```

---

## Self-Review

- Spec coverage: Covers all review findings: `allowed_skills` field preservation, primitive validation semantics, robot-local envelope propagation, gateway payload acceptance, mission fallback round-trip, and profile primitive validation.
- Placeholder scan: No implementation placeholders remain; code snippets and exact commands are included for every task.
- Type consistency: Uses existing names `StructuredRobotTask`, `validate_structured_robot_task`, `envelope_from_structured_task`, `primitive_skills`, `allowed_skills`, and `required_skills`.
- Scope check: This plan intentionally does not add new primitives like `navigate_to_pose`; it only fixes the broken allowlist plumbing needed for primitive composition to work safely.
