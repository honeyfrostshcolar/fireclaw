# Mission Trace Stream v1 Implementation Plan

**Goal:** Add a polling-based mission event stream that detects changes between `mission_trace()` snapshots and yields structured `MissionEvent` objects.

**Architecture:** New `MissionTraceStream` class wraps `MissionAgent`. It polls `mission_trace()` at a configurable interval, compares the current snapshot with the previous one, and yields events for new subtasks, status changes, and mission status transitions. Works as a generator — testable without HTTP infrastructure.

---

## File Structure

- Create: `src/fireclaw_core/mission_trace_stream.py` — `MissionEvent`, `MissionTraceStream`
- Create: `tests/test_mission_trace_stream.py` — stream tests
- Modify: `README.md` — document trace stream

---

### Task 1: Add MissionEvent data type and MissionTraceStream

- [ ] **Step 1: Write failing tests**

```python
def test_stream_emits_subtask_status_change():
    """When a subtask changes status, stream emits an event."""

def test_stream_emits_mission_completed():
    """When all subtasks are terminal, stream emits mission.completed."""

def test_stream_ends_when_mission_terminal():
    """Generator stops when mission reaches terminal status."""
```

- [ ] **Step 2: Implement**

`MissionTraceStream`:
- `stream(mission_id, timeout_seconds)` generator
- Polls `mission_agent.mission_trace(mission_id)` on interval
- Tracks previous subtask statuses `(robot_id, task_id) -> status`
- Yields `MissionEvent` for each detected change
- Stops when mission status is terminal (`succeeded`, `failed`, `cancelled`, `aborted`, `escalated`) or timeout

`MissionEvent` dataclass:
- `type`: "subtask.submitted", "subtask.status_changed", "mission.completed", "mission.failed", "mission.escalated"
- `mission_id`, `robot_id`, `task_id`, `status`, `previous_status`, `timestamp`, `details`

- [ ] **Step 3: Verify tests pass**
- [ ] **Step 4: Commit**

### Task 2: Full suite verification and README

- [ ] Run full test suite
- [ ] Update README
- [ ] Commit
