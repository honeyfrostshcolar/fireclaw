# Fleet Heartbeat v1 Implementation Plan

**Goal:** Add fleet presence tracking so the main agent knows which robot subagents are online before submitting tasks.

**Architecture:** RobotRegistryEntry gets `last_seen_at` tracking. RobotSubagentClient gets `check_presence()` that pings `/state`. MissionAgent filters by online status when planning.

---

## File Structure

- Modify: `src/fireclaw_core/robot_registry.py` — add `last_seen_at` to `RobotRegistryEntry`
- Modify: `src/fireclaw_core/subagent_client.py` — add `check_presence()` method
- Modify: `src/fireclaw_core/mission_agent.py` — filter by online status
- Modify: `tests/test_robot_registry.py` — add heartbeat tests
- Modify: `tests/test_subagent_client.py` — add presence check tests
- Modify: `tests/test_mission_agent.py` — add online filtering tests

---

### Task 1: Add last_seen_at to RobotRegistryEntry

- [ ] **Step 1: Write the failing test**

```python
def test_robot_registry_entry_has_last_seen_at():
    entry = RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")
    assert entry.last_seen_at is None

def test_robot_registry_entry_online_status():
    entry = RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765")
    assert entry.is_online is False
    
    entry = RobotRegistryEntry(robot_id="r1", base_url="http://r1:8765", last_seen_at="2026-06-08T00:00:00+00:00")
    assert entry.is_online is True
```

- [ ] **Step 2: Implement**

```python
@dataclass
class RobotRegistryEntry:
    robot_id: str
    base_url: str
    capabilities: tuple[str, ...] = ()
    zone: str | None = None
    enabled: bool = True
    last_seen_at: str | None = None
    
    @property
    def is_online(self) -> bool:
        return self.last_seen_at is not None
```

- [ ] **Step 3: Commit**

---

### Task 2: Add check_presence to RobotSubagentClient

- [ ] **Step 1: Write the failing test**

```python
def test_subagent_client_check_presence():
    # Mock server that returns state
    ...
    result = client.check_presence(entry)
    assert result["online"] is True
    assert "last_seen_at" in result
```

- [ ] **Step 2: Implement**

```python
def check_presence(self, entry: RobotRegistryEntry) -> dict[str, Any]:
    try:
        state = self.get_state(entry)
        now = datetime.now(timezone.utc).isoformat()
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": now,
            "state": state,
        }
    except Exception as exc:
        return {
            "robot_id": entry.robot_id,
            "online": False,
            "error": str(exc),
        }
```

- [ ] **Step 3: Commit**

---

### Task 3: Add fleet heartbeat to MissionAgent

- [ ] **Step 1: Write the failing test**

```python
def test_mission_agent_skips_offline_robots():
    # Robot that fails health check
    ...
    result = mission.plan_and_submit("去二楼搜索")
    assert result["status"] == "clarify"
    assert "offline" in result["message"]
```

- [ ] **Step 2: Implement**

```python
def check_fleet_presence(self) -> dict[str, dict[str, Any]]:
    results = {}
    for entry in self.registry.enabled_entries():
        result = self.subagent_client.check_presence(entry)
        results[entry.robot_id] = result
        if result["online"]:
            entry.last_seen_at = result["last_seen_at"]
    return results
```

- [ ] **Step 3: Commit**

---

### Task 4: Full suite verification

- [ ] Run all tests
- [ ] Update README
- [ ] Commit
