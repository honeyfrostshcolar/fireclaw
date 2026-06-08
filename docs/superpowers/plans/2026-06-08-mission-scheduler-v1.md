# Mission Scheduler v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `MissionScheduler` that executes `MissionPlan.execution_group` in ordered batches, polling between groups and making failure decisions.

**Architecture:** New `MissionScheduler` class wraps `MissionAgent`. It groups subtasks by `execution_group`, submits each group, polls robot-local traces until all subtasks in the group reach terminal state, then decides whether to proceed to the next group based on configurable failure policy.

**Tech Stack:** Python, pytest, existing `MissionAgent`/`JsonlMissionRegistry`/`RobotSubagentClient`

---

## File Structure

- Create: `src/fireclaw_core/mission_scheduler.py` — `MissionScheduler`, `MissionSchedulerConfig`
- Create: `tests/test_mission_scheduler.py` — scheduler tests
- Modify: `src/fireclaw_core/mission_agent.py` — expose `subagent_client` and `registry` for scheduler access
- Modify: `README.md` — document mission scheduler

---

### Task 1: Create MissionScheduler data types and config

**Files:**
- Create: `src/fireclaw_core/mission_scheduler.py`
- Create: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_config_defaults():
    from fireclaw_core.mission_scheduler import MissionSchedulerConfig
    config = MissionSchedulerConfig()
    assert config.failure_policy == "stop"
    assert config.poll_interval_seconds == 0.1
    assert config.group_timeout_seconds == 300.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_config_defaults -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MissionSchedulerConfig:
    failure_policy: str = "stop"  # "stop" or "continue"
    poll_interval_seconds: float = 0.1
    group_timeout_seconds: float = 300.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_config_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 2: Implement MissionScheduler with group execution

**Files:**
- Modify: `src/fireclaw_core/mission_scheduler.py`
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test for parallel group scheduling**

```python
def test_scheduler_submits_parallel_group(tmp_path):
    """Two robots in same execution_group both get submitted."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
        RobotRegistryEntry(robot_id="r2", base_url="http://r2:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r2", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=0),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(poll_interval_seconds=0.01))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 1
    assert result["group_results"][0]["group_index"] == 0
    assert len(result["group_results"][0]["subtask_results"]) == 2
    assert len(client.calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_submits_parallel_group -v`
Expected: FAIL with `ImportError` or `ModuleNotFoundError`

- [ ] **Step 3: Implement MissionScheduler**

The scheduler:
1. Groups plan subtasks by `execution_group`
2. For each group: submit subtasks via `MissionAgent.submit_subtask()`
3. Poll `MissionAgent.mission_trace()` until all subtasks in group are terminal
4. Check group results against failure policy
5. If policy allows, proceed to next group
6. Return aggregated results

```python
@dataclass
class MissionScheduler:
    mission_agent: MissionAgent
    config: MissionSchedulerConfig = field(default_factory=MissionSchedulerConfig)

    def schedule(
        self,
        plan: MissionPlan,
        *,
        mission_id: str,
        session_id: str | None = None,
        operator: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Group subtasks by execution_group
        groups: dict[int, list[MissionSubtask]] = {}
        for subtask in plan.subtasks:
            groups.setdefault(subtask.execution_group, []).append(subtask)
        
        sorted_group_indices = sorted(groups.keys())
        group_results: list[dict[str, Any]] = []
        
        for group_index in sorted_group_indices:
            group_subtasks = groups[group_index]
            # Submit all subtasks in this group
            subtask_results = []
            for subtask in group_subtasks:
                result = self.mission_agent.submit_subtask(
                    subtask.robot_id,
                    subtask.command,
                    session_id=mission_id,
                    dedupe_key=f"{mission_id}-{subtask.robot_id}-{subtask.floor}",
                    operator=operator,
                    mission={"mission_id": mission_id, "execution_group": subtask.execution_group},
                )
                subtask_results.append(result)
            
            # Poll until all subtasks in group are terminal
            group_terminal = self._poll_group_terminal(mission_id, group_subtasks)
            
            group_result = {
                "group_index": group_index,
                "subtask_results": subtask_results,
                "terminal_states": group_terminal,
            }
            group_results.append(group_result)
            
            # Check failure policy
            if self.config.failure_policy == "stop":
                bad_statuses = {"failed", "block", "denied", "lost"}
                if any(s.get("status") in bad_statuses for s in group_terminal):
                    return {
                        "status": "stopped",
                        "message": f"Group {group_index} had failures, stopping.",
                        "mission_id": mission_id,
                        "group_results": group_results,
                    }
        
        return {
            "status": "succeeded",
            "mission_id": mission_id,
            "group_results": group_results,
        }
    
    def _poll_group_terminal(self, mission_id: str, group_subtasks: list[MissionSubtask]) -> list[dict[str, Any]]:
        """Poll until all subtasks in group reach terminal state."""
        import time
        deadline = time.monotonic() + self.config.group_timeout_seconds
        robot_ids = {s.robot_id for s in group_subtasks}
        
        while time.monotonic() < deadline:
            trace = self.mission_agent.mission_trace(mission_id)
            terminal = []
            for subtask in trace.get("subtasks", []):
                if subtask.get("robot_id") in robot_ids:
                    if subtask.get("status") in TERMINAL_SUBTASK_STATUSES:
                        terminal.append(subtask)
            if len(terminal) >= len(group_subtasks):
                return terminal
            time.sleep(self.config.poll_interval_seconds)
        
        # Timeout - return current state
        trace = self.mission_agent.mission_trace(mission_id)
        return [s for s in trace.get("subtasks", []) if s.get("robot_id") in robot_ids]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_submits_parallel_group -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 3: Add sequential group scheduling test

**Files:**
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_sequential_groups(tmp_path):
    """One robot reused across two execution_groups. Group 1 starts after group 0 completes."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
    )
    plan = MissionPlan(
        intent="search",
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(poll_interval_seconds=0.01))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 2
    assert result["group_results"][0]["group_index"] == 0
    assert result["group_results"][1]["group_index"] == 1
    # Two separate submissions (one per group)
    assert len(client.calls) == 2
```

- [ ] **Step 2: Run test to verify it passes**

The scheduler already handles this because it iterates groups in order. The key is that group 1 only starts after group 0's subtasks reach terminal state. Since the FakeSubagentClient returns "accepted" immediately, and the mission trace aggregation marks them as terminal, this should work.

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_sequential_groups -v`
Expected: PASS (after Task 2 implementation)

- [ ] **Step 3: Commit**

---

### Task 4: Add failure policy stop test

**Files:**
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_stops_on_group_failure(tmp_path):
    """When failure_policy='stop', group 1 is not submitted if group 0 has a failed subtask."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    # Make the first subtask fail
    client.traces[("r1", "task-robot-1")] = {
        "task_id": "task-robot-1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "robot malfunction"},
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
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(
        poll_interval_seconds=0.01,
        failure_policy="stop",
    ))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "stopped"
    assert len(result["group_results"]) == 1  # Only group 0 was attempted
    assert len(client.calls) == 1  # Only group 0's subtask was submitted
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_stops_on_group_failure -v`
Expected: PASS

- [ ] **Step 3: Commit**

---

### Task 5: Add failure policy continue test

**Files:**
- Modify: `tests/test_mission_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scheduler_continues_on_failure_when_policy_is_continue(tmp_path):
    """When failure_policy='continue', group 1 is submitted even if group 0 failed."""
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", capabilities=("search_for_victims",)),
    ])
    client = FakeSubagentClient()
    client.traces[("r1", "task-robot-1")] = {
        "task_id": "task-robot-1",
        "robot_id": "r1",
        "status": "failed",
        "result": {"status": "failed", "message": "robot malfunction"},
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
        command="去二楼和三楼搜索",
        subtasks=[
            MissionSubtask(robot_id="r1", command="去2楼搜索受困人员", floor=2, capability_required="search_for_victims", execution_group=0),
            MissionSubtask(robot_id="r1", command="去3楼搜索受困人员", floor=3, capability_required="search_for_victims", execution_group=1),
        ],
    )
    scheduler = MissionScheduler(mission_agent=mission, config=MissionSchedulerConfig(
        poll_interval_seconds=0.01,
        failure_policy="continue",
    ))

    result = scheduler.schedule(plan, mission_id="m1", session_id="m1")

    assert result["status"] == "succeeded"
    assert len(result["group_results"]) == 2
    assert len(client.calls) == 2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_scheduler.py::test_scheduler_continues_on_failure_when_policy_is_continue -v`
Expected: PASS

- [ ] **Step 3: Commit**

---

### Task 6: Full suite verification and README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All tests pass (265+)

- [ ] **Step 2: Update README**

Add Mission Scheduler documentation after the Fleet Presence section.

- [ ] **Step 3: Commit**
