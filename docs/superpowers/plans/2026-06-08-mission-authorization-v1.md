# Mission Authorization v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mission-level authorization scopes to MissionAgent, aligning with OpenClaw's operator scope pattern.

**Architecture:** MissionAgent accepts an optional ControlPolicy and OperatorContext. Before executing mission operations (submit, cancel, plan, trace), MissionAgent evaluates the required mission-level scope. If denied, returns deny result without contacting robot subagents. Robot-level authorization remains independent.

**Tech Stack:** Python, pytest, existing FireClaw control.py pattern

---

## OpenClaw Analogue

OpenClaw's authorization architecture uses:

1. **Operator Scopes** (`operator-scopes.ts`): `operator.admin`, `operator.read`, `operator.write`, `operator.approvals`, `operator.pairing`, `operator.talk.secrets`
2. **Method Scopes** (`method-scopes.ts`): Each gateway method maps to a required scope. `authorizeOperatorScopesForMethod(method, scopes)` checks authorization. `admin` bypasses all checks; `write` satisfies `read`.
3. **Core Descriptors** (`core-descriptors.ts`): Static mapping of method name → scope (e.g., `tasks.cancel` → `operator.write`, `node.invoke` → `operator.write`)

FireClaw adaptation:

- OpenClaw `operator.admin` → FireClaw `admin` (bypasses all checks)
- OpenClaw `operator.read` → FireClaw `state.read`
- OpenClaw `operator.write` → FireClaw `task.submit`, `task.cancel`
- OpenClaw method-scope mapping → FireClaw mission operation-scope mapping
- OpenClaw `authorizeOperatorScopesForMethod` → FireClaw `ControlPolicy.evaluate`

## File Structure

- Modify: `src/fireclaw_core/control.py` — add mission-level scopes to `ROLE_SCOPES`
- Modify: `src/fireclaw_core/mission_agent.py` — add `ControlPolicy` and `OperatorContext` to `MissionAgent`
- Modify: `src/fireclaw_core/mission_cli.py` — add `--operator-id`, `--role`, `--scopes` flags
- Modify: `tests/test_mission_agent.py` — add authorization tests
- Modify: `tests/test_mission_cli.py` — add CLI authorization tests

---

### Task 1: Add Mission Scopes to ROLE_SCOPES

**Files:**
- Modify: `src/fireclaw_core/control.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_control.py (or create if needed)
def test_mission_scopes_included_in_role_scopes():
    from fireclaw_core.control import ROLE_SCOPES, scopes_for_role
    operator_scopes = scopes_for_role("operator")
    assert "mission.submit" in operator_scopes
    assert "mission.cancel" in operator_scopes
    assert "mission.plan" in operator_scopes
    assert "mission.read" in operator_scopes

def test_admin_role_has_all_mission_scopes():
    from fireclaw_core.control import scopes_for_role
    admin_scopes = scopes_for_role("admin")
    assert "mission.submit" in admin_scopes
    assert "mission.cancel" in admin_scopes
    assert "mission.plan" in admin_scopes
    assert "mission.read" in admin_scopes

def test_observer_role_has_only_mission_read():
    from fireclaw_core.control import scopes_for_role
    observer_scopes = scopes_for_role("observer")
    assert "mission.read" in observer_scopes
    assert "mission.submit" not in observer_scopes
    assert "mission.cancel" not in observer_scopes
    assert "mission.plan" not in observer_scopes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_control.py::test_mission_scopes_included_in_role_scopes -v`
Expected: FAIL with "mission.submit" not in operator scopes

- [ ] **Step 3: Write minimal implementation**

```python
# Modify ROLE_SCOPES in src/fireclaw_core/control.py
ROLE_SCOPES: dict[str, set[str]] = {
    "observer": {"state.read", "mission.read"},
    "operator": {
        "task.submit", "task.confirm", "task.cancel", "state.read",
        "mission.submit", "mission.cancel", "mission.plan", "mission.read",
    },
    "supervisor": {
        "task.submit", "task.confirm", "task.cancel", "safety.override", "state.read",
        "mission.submit", "mission.cancel", "mission.plan", "mission.read",
    },
    "admin": {
        "task.submit", "task.confirm", "task.cancel", "safety.override", "emergency.stop", "state.read",
        "mission.submit", "mission.cancel", "mission.plan", "mission.read",
    },
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_control.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/control.py tests/test_control.py
git commit -m "feat: add mission-level scopes to ROLE_SCOPES"
```

---

### Task 2: Add ControlPolicy and OperatorContext to MissionAgent

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_mission_agent.py
def test_mission_agent_denies_submit_without_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "denied"
    assert "mission.submit" in result["message"]
    assert client.calls == []

def test_mission_agent_allows_submit_with_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="operator",
        control_scopes=scopes_for_role("operator"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(client.calls) == 1

def test_mission_agent_denies_cancel_without_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    mission_registry = JsonlMissionRegistry(tmp_path / "missions.jsonl")
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        control_policy=policy,
        operator=operator,
    )
    # First submit with admin to create mission
    admin_operator = OperatorContext(
        operator_id="admin",
        role="admin",
        control_scopes={"state.read", "mission.submit", "mission.cancel", "mission.plan", "mission.read"},
    )
    mission_admin = MissionAgent(
        registry=registry,
        subagent_client=client,
        mission_registry=mission_registry,
        control_policy=policy,
        operator=admin_operator,
    )
    submitted = mission_admin.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    result = mission.cancel_mission(submitted["mission_id"])

    assert result["status"] == "denied"
    assert "mission.cancel" in result["message"]

def test_mission_agent_denies_plan_without_mission_scope():
    from fireclaw_core.control import ControlPolicy, OperatorContext
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="test-operator",
        role="observer",
        control_scopes={"state.read", "mission.read"},
    )
    planner = FakeMissionPlanner(MissionPlanningResult(
        status="planned", message="ok", intent="search",
        plan=MissionPlan(intent="search", command="test", subtasks=[]),
    ))
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
        planner=planner,
    )

    result = mission.plan_and_submit("去二楼搜索", session_id="mission-1")

    assert result["status"] == "denied"
    assert "mission.plan" in result["message"]
    assert client.calls == []

def test_mission_agent_admin_bypasses_mission_scopes():
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    registry = RobotRegistry([
        RobotRegistryEntry(robot_id="robot-1", base_url="http://robot-1.local:8765"),
    ])
    client = FakeSubagentClient()
    policy = ControlPolicy()
    operator = OperatorContext(
        operator_id="admin",
        role="admin",
        control_scopes=scopes_for_role("admin"),
    )
    mission = MissionAgent(
        registry=registry,
        subagent_client=client,
        control_policy=policy,
        operator=operator,
    )

    result = mission.submit_subtask("robot-1", "去二楼搜索", session_id="mission-1")

    assert result["status"] == "accepted"
    assert len(client.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_agent.py::test_mission_agent_denies_submit_without_mission_scope -v`
Expected: FAIL with `MissionAgent.__init__() got an unexpected keyword argument 'control_policy'`

- [ ] **Step 3: Write minimal implementation**

```python
# Modify MissionAgent in src/fireclaw_core/mission_agent.py
from fireclaw_core.control import ControlPolicy, OperatorContext, operator_from_payload

class MissionAgent:
    def __init__(
        self,
        *,
        registry: RobotRegistry,
        subagent_client: SubagentClient | None = None,
        mission_registry: JsonlMissionRegistry | None = None,
        planner: Any | None = None,
        control_policy: ControlPolicy | None = None,
        operator: OperatorContext | None = None,
    ) -> None:
        self.registry = registry
        self.subagent_client = subagent_client or RobotSubagentClient()
        self.mission_registry = mission_registry
        self.planner = planner
        self.control_policy = control_policy
        self.operator = operator

    def _authorize(self, action: str) -> dict[str, Any] | None:
        """Check mission-level authorization. Returns deny dict if denied, None if allowed."""
        if self.control_policy is None or self.operator is None:
            return None  # No authorization configured, allow
        decision = self.control_policy.evaluate(self.operator, action)
        if decision.status == "deny":
            return {
                "status": "denied",
                "message": f"Operator {self.operator.operator_id} lacks required scope: {action}",
                "decision": decision.to_dict(),
            }
        return None

    def submit_subtask(self, ...):
        deny = self._authorize("mission.submit")
        if deny is not None:
            return deny
        # ... existing implementation

    def cancel_mission(self, ...):
        deny = self._authorize("mission.cancel")
        if deny is not None:
            return deny
        # ... existing implementation

    def plan_and_submit(self, ...):
        deny = self._authorize("mission.plan")
        if deny is not None:
            return deny
        # ... existing implementation

    def mission_trace(self, ...):
        deny = self._authorize("mission.read")
        if deny is not None:
            return deny
        # ... existing implementation
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/mission_agent.py tests/test_mission_agent.py
git commit -m "feat: add mission-level authorization to MissionAgent"
```

---

### Task 3: Add Operator Flags to Mission CLI

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_mission_cli.py
def test_mission_cli_rejects_submit_without_mission_scope(tmp_path):
    gateway = FireClawGateway(GatewayConfig(
        host="127.0.0.1", port=0, adapter="simulator", robot_id="robot-1",
        memory_path=str(tmp_path / "robot-memory.jsonl"),
        event_path=str(tmp_path / "robot-events.jsonl"),
        task_queue_path=str(tmp_path / "robot-tasks.jsonl"),
        workspace_skills_dir=None,
    ))
    gateway.start()
    try:
        robot_registry_path = tmp_path / "robots.json"
        mission_registry_path = tmp_path / "missions.jsonl"
        robot_registry_path.write_text(
            json.dumps({"robots": [{"robot_id": "robot-1", "base_url": gateway.base_url}]}),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [
                ".venv/bin/python", "-m", "fireclaw_core.mission_cli",
                "submit-subtask",
                "--robot", "robot-1",
                "--command", "去二楼救人",
                "--robot-registry", str(robot_registry_path),
                "--mission-registry", str(mission_registry_path),
                "--operator-id", "test-observer",
                "--role", "observer",
            ],
            check=False, cwd=".", text=True, capture_output=True,
        )
        result = json.loads(completed.stdout)
    finally:
        gateway.stop()

    assert result["status"] == "denied"
    assert "mission.submit" in result["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mission_cli.py::test_mission_cli_rejects_submit_without_mission_scope -v`
Expected: FAIL (observer role should not have mission.submit scope)

- [ ] **Step 3: Write minimal implementation**

```python
# Modify mission_cli.py
def _add_shared_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--robot-registry", required=True, help="Path to robot registry JSON.")
    parser.add_argument("--mission-registry", required=True, help="Path to mission registry JSONL.")
    parser.add_argument("--operator-id", default="mission-agent", help="Operator ID for authorization.")
    parser.add_argument("--role", default="operator", help="Operator role (observer, operator, supervisor, admin).")
    parser.add_argument("--scopes", nargs="*", default=None, help="Explicit operator scopes (overrides role defaults).")

def _build_mission_agent(args: argparse.Namespace) -> MissionAgent:
    from fireclaw_core.control import ControlPolicy, OperatorContext, scopes_for_role
    scopes = set(args.scopes) if args.scopes else scopes_for_role(args.role)
    operator = OperatorContext(
        operator_id=args.operator_id,
        role=args.role,
        control_scopes=scopes,
        source="mission_cli",
    )
    return MissionAgent(
        registry=load_robot_registry(args.robot_registry),
        mission_registry=JsonlMissionRegistry(args.mission_registry),
        control_policy=ControlPolicy(),
        operator=operator,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mission_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/mission_cli.py tests/test_mission_cli.py
git commit -m "feat: add operator authorization flags to mission CLI"
```

---

### Task 4: Full Suite Verification

- [ ] **Step 1: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_mission_agent.py tests/test_mission_cli.py tests/test_control.py -q`
Expected: PASS

- [ ] **Step 2: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (247+ tests)

- [ ] **Step 3: Update README**

Add documentation for mission-level authorization scopes and CLI flags.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add mission-level authorization documentation"
```
