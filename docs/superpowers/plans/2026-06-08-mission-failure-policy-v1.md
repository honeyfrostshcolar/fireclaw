# Mission Failure Policy v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the coarse group-level `failure_policy` string with per-subtask failure decisions supporting retry, reassign, escalate, skip, and abort.

**Architecture:** New `MissionFailurePolicy` dataclass with per-decision config. After a group reaches terminal state, the scheduler evaluates each failed subtask against the policy and executes the resulting decision (retry → resubmit to same robot, reassign → find alternative robot with matching capability, escalate → mark as needing operator, skip → continue, abort → stop mission).

**Tech Stack:** Python, pytest, existing `MissionScheduler`/`MissionAgent`/`RobotRegistry`

---

## File Structure

- Modify: `src/fireclaw_core/mission_scheduler.py` — add `MissionFailurePolicy`, `FailureDecision`, refactor scheduler
- Modify: `tests/test_mission_scheduler.py` — add failure policy tests
- Modify: `README.md` — document failure policy

---

### Task 1: Add MissionFailurePolicy data types

**Files:**
- Modify: `src/fireclaw_core/mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_failure_policy_defaults():
    from fireclaw_core.mission_scheduler import MissionFailurePolicy
    policy = MissionFailurePolicy()
    assert policy.on_failed == "reassign"
    assert policy.on_denied == "abort"
    assert policy.on_lost == "abort"
    assert policy.max_retries == 1
    assert policy.max_reassigns == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_failure_policy_defaults -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement data types**

```python
@dataclass(frozen=True)
class MissionFailurePolicy:
    """Per-subtask failure decisions.

    Decision values: "retry", "reassign", "skip", "escalate", "abort".
    """
    on_failed: str = "reassign"
    on_denied: str = "abort"
    on_lost: str = "abort"
    on_block: str = "escalate"
    max_retries: int = 1
    max_reassigns: int = 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_failure_policy_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 2: Refactor scheduler to use MissionFailurePolicy

**Files:**
- Modify: `src/fireclaw_core/mission_scheduler.py`
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test for reassign on failure**

```python
def test_scheduler_reassigns_failed_subtask(tmp_path):
    """When on_failed='reassign', a failed subtask is reassigned to another capable robot."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    # r1's first attempt fails
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "sensor malfunction"},
        "events": [{"type": "task.failed"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_failed="reassign", max_reassigns=1),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    # r1 failed, then r2 was reassigned
    assert len(client.calls) == 2
    assert client.calls[0][0].robot_id == "r1"
    assert client.calls[1][0].robot_id == "r2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_reassigns_failed_subtask -v`
Expected: FAIL (scheduler doesn't reassign yet)

- [ ] **Step 3: Implement failure policy evaluation in scheduler**

Refactor `schedule()` to:
1. After group terminal, evaluate each failed subtask against the policy
2. Execute decisions: retry (resubmit to same robot), reassign (find alt robot), skip (ignore), escalate (mark), abort (stop mission)
3. Track retry/reassign counts per subtask
4. Loop until no more actions needed or abort

Key implementation changes:
- Replace `MissionSchedulerConfig.failure_policy: str` with `failure_policy: MissionFailurePolicy`
- Add `_evaluate_failure()` method that returns a decision per failed subtask
- Add `_find_reassign_robot()` method that searches registry for alternative robot
- Add retry/reassign tracking in the schedule loop

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_reassigns_failed_subtask -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 3: Add abort on denied test

**Files:**
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_aborts_on_denied(tmp_path):
    """When on_denied='abort', mission stops immediately on denied subtask."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "denied",
        "result": {"status": "denied", "message": "safety gate blocked"},
        "events": [{"type": "task.denied"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_denied="abort"),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "aborted"
    assert len(client.calls) == 1  # No retry/reassign for denied
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_aborts_on_denied -v`
Expected: PASS

- [ ] **Step 3: Commit**

---

### Task 4: Add escalate test

**Files:**
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_escalates_on_block(tmp_path):
    """When on_block='escalate', subtask is marked escalated and mission continues."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-r1")] = {
        "task_id": "task-r1",
        "robot_id": "r1",
        "status": "block",
        "result": {"status": "block", "message": "需要人工确认"},
        "events": [{"type": "task.blocked"}],
    }
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(
        mission_agent=mission,
        config=MissionSchedulerConfig(
            poll_interval_seconds=0.01,
            failure_policy=MissionFailurePolicy(on_block="escalate"),
        ),
    )

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    # Escalated subtask is noted in the result
    assert any(d.get("decision") == "escalated" for d in result.get("failure_decisions", []))
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_escalates_on_block -v`
Expected: PASS

- [ ] **Step 3: Commit**

---

### Task 5: Update MissionSchedulerConfig and backward compatibility

**Files:**
- Modify: `src/fireclaw_core/mission_scheduler.py`
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Update MissionSchedulerConfig to use MissionFailurePolicy**

```python
@dataclass(frozen=True)
class MissionSchedulerConfig:
    failure_policy: MissionFailurePolicy = field(default_factory=MissionFailurePolicy)
    poll_interval_seconds: float = 0.1
    group_timeout_seconds: float = 300.0
```

- [ ] **Step 2: Update existing tests that use string failure_policy**

Replace `failure_policy="stop"` with `failure_policy=MissionFailurePolicy(on_failed="abort", on_denied="abort", on_lost="abort")` in existing tests that test abort behavior.

- [ ] **Step 3: Run all scheduler tests**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

---

### Task 6: Full suite verification and README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All tests pass (270+)

- [ ] **Step 2: Update README**

Replace the existing Mission Scheduler section with updated failure policy documentation.

- [ ] **Step 3: Commit**
