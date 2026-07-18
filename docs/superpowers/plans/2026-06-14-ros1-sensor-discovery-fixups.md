# ROS1 Sensor Discovery Fixups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the gaps found in review of the ROS1/Gazebo sensor discovery implementation so verified runtime sensors actually drive SafetyGate, `/state` stays responsive, profile write-back remains parseable, and CLI/docs match profile-driven usage.

**Architecture:** Keep the existing discovery boundary, but make `FireClawAgent` defer to `RobotState.available_sensors` unless sensors were explicitly overridden. Add short-lived caching to ROS1 discovery so HTTP state checks do not repeatedly run slow ROS subprocess probes. Treat profile write-back as structured replacement/merge of the discovery block, not blind append.

**Tech Stack:** Python 3.11 dataclasses, pytest, existing FireClaw gateway/agent/profile modules, ROS1 CLI provider abstraction with deterministic fakes in tests.

---

## Review Findings Being Fixed

1. `FireClawAgent` caches `robot.available_sensors` at construction, so ROS1 discovery results in `robot_state.available_sensors` are ignored by SafetyGate.
2. `/state` can take about 11 seconds in Gazebo because every request triggers multiple `rostopic` subprocess calls and per-topic `rostopic echo` probes.
3. `robot-profile discover --write-profile` appends a second `[robot.sensor_discovery]` table and can make a profile invalid TOML.
4. `fireclaw_core serve --help` does not expose `--robot-profile`, although the profile-driven runtime supports profiles through config.
5. `fireclaw.example.toml` currently points users back toward hand-written robot gateway fields and static `available_sensors`.

---

## File Structure

- Modify `src/fireclaw_core/agent/agent.py`
  - Track whether sensors were explicitly provided.
  - Pass explicit sensors to SafetyGate only when provided; otherwise let SafetyGate use `robot_state.available_sensors`.
  - Expose current sensors in robot-agent planner context from robot state, not stale cache.
- Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`
  - Add TTL cache to `Ros1SensorDiscovery.discover()`.
  - Add optional topic allowlist derived from mapping rules so unmatched topics do not trigger message probes.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Add `serve --robot-profile`.
  - Fix `robot-profile discover --write-profile` so it replaces or appends only `[[robot.sensor_discovery.rules]]` safely.
- Modify `fireclaw.example.toml`
  - Restore profile-driven mission and robot gateway example fields.
- Modify `tests/test_agent.py`
  - Add regression for verified sensors from robot state being used by SafetyGate.
- Modify `tests/test_gateway_structured_task.py`
  - Add regression for robot-agent context using runtime verified sensors.
- Modify `tests/test_ros1_sensor_discovery.py`
  - Add cache behavior test.
- Modify `tests/test_mission_cli.py`
  - Add `serve --robot-profile` help/merge coverage and valid write-profile TOML regression.
- Modify `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`
  - Record the fixup plan and final verification.

---

### Task 1: Make SafetyGate Use Runtime Verified Sensors

**Files:**
- Modify: `src/fireclaw_core/agent/agent.py`
- Test: `tests/test_agent.py`
- Test: `tests/test_gateway_structured_task.py`

- [ ] **Step 1: Add failing test for FireClawAgent using discovered sensors**

Append to `tests/test_agent.py`:

```python
from fireclaw_core.agent.robot import Ros1RobotAdapter
from fireclaw_core.ros.ros1_config import Ros1AdapterConfig
from fireclaw_core.ros.ros1_sensor_discovery import (
    Ros1SensorDiscovery,
    StaticRos1GraphProvider,
    StaticRos1MessageProbe,
)


def test_agent_uses_robot_state_verified_sensors_when_no_override() -> None:
    robot = Ros1RobotAdapter(
        config=Ros1AdapterConfig(robot_id="robot-1"),
        sensor_discovery=Ros1SensorDiscovery(
            graph_provider=StaticRos1GraphProvider({
                "/camera/image_raw": "sensor_msgs/Image",
                "/thermal/image_raw": "sensor_msgs/Image",
            }),
            message_probe=StaticRos1MessageProbe({
                "/camera/image_raw": True,
                "/thermal/image_raw": True,
            }),
        ),
        dry_run=True,
    )
    agent = FireClawAgent(robot=robot, workspace_skills_dir=None, dry_run=True)

    result = agent.run("去二楼救人")

    assert result["status"] in {"succeeded", "completed"}
    assert result["robot_state"]["available_sensors"] == ["rgb_camera", "thermal_camera"]
```

- [ ] **Step 2: Run the regression and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_agent.py::test_agent_uses_robot_state_verified_sensors_when_no_override -q
```

Expected before fix: FAIL with status `block` and missing `rgb_camera` or `thermal_camera`.

- [ ] **Step 3: Update FireClawAgent sensor override semantics**

Modify `src/fireclaw_core/agent/agent.py`.

In `FireClawAgent.__init__`, replace the current sensor initialization block with:

```python
        self._available_sensors_override = available_sensors is not None
        self.available_sensors = available_sensors or set()
```

Add helper method inside `FireClawAgent`:

```python
    def _safety_available_sensors(self) -> set[str] | None:
        if self._available_sensors_override:
            return self.available_sensors
        return None
```

Replace every SafetyGate call argument:

```python
            available_sensors=self.available_sensors,
```

with:

```python
            available_sensors=self._safety_available_sensors(),
```

This keeps explicit CLI/config overrides working, while allowing `robot_state.available_sensors` to be authoritative when no override exists.

- [ ] **Step 4: Update robot-agent planner context sensors**

In `src/fireclaw_core/gateway/gateway.py`, replace the context construction in `_run_robot_agent_structured_task()`:

```python
        context = {
            "robot_state": agent._state_snapshot(agent._get_robot_state()),
            "environment_state": agent._state_snapshot(agent._get_environment_state()),
            "available_sensors": sorted(agent.available_sensors),
            "skill_tools": skill_tools,
            "skill_metadata": skill_metadata,
        }
```

with:

```python
        robot_state_object = agent._get_robot_state()
        robot_state = agent._state_snapshot(robot_state_object)
        runtime_sensors = robot_state.get("available_sensors")
        context = {
            "robot_state": robot_state,
            "environment_state": agent._state_snapshot(agent._get_environment_state()),
            "available_sensors": sorted(runtime_sensors or agent.available_sensors),
            "skill_tools": skill_tools,
            "skill_metadata": skill_metadata,
        }
```

- [ ] **Step 5: Add context regression test**

In `tests/test_gateway_structured_task.py`, add a test that injects a robot whose `get_robot_state()` returns `available_sensors=["rgb_camera"]`, then asserts the captured robot-agent planner context contains `["rgb_camera"]` rather than an empty list.

Use the local fake planner pattern already present in this file:

```python
def test_robot_agent_context_uses_runtime_verified_sensors(tmp_path):
    gateway = FireClawGateway(GatewayConfig(
        port=0,
        robot_agent_enabled=True,
        workspace_skills_dir=None,
    ))
    gateway.robot.available_sensors = []

    class RuntimeSensorRobot(type(gateway.robot)):
        pass

    gateway.robot.available_sensors = []
    original_get_state = gateway.robot.get_robot_state

    def get_state_with_runtime_sensor():
        state = original_get_state()
        state.available_sensors = ["rgb_camera"]
        return state

    gateway.robot.get_robot_state = get_state_with_runtime_sensor

    captured = {}

    class CapturingPlanner:
        def plan_structured_task(self, task, *, context, **kwargs):
            captured["available_sensors"] = context["available_sensors"]
            from fireclaw_core.agent.robot_agent import RobotLocalPlan
            return RobotLocalPlan(
                task_id=task.task_id,
                robot_id=task.robot_id,
                steps=[],
                confidence=1.0,
            )

    from fireclaw_core.agent.robot_agent import RobotAgentRuntime
    gateway.robot_agent_runtime = RobotAgentRuntime(planner=CapturingPlanner())
    agent = gateway._create_agent(task_id="task-1", session_id="session-1")
    from fireclaw_core.task.task_contract import StructuredRobotTask
    task = StructuredRobotTask(
        task_id="task-1",
        robot_id=gateway.config.robot_id,
        task_type="search",
        command="search",
        target={"floor": 2},
        required_skills=[],
    )

    gateway._run_robot_agent_structured_task(
        agent=agent,
        task_object=task,
        session_id="session-1",
        task_id="task-1",
    )

    assert captured["available_sensors"] == ["rgb_camera"]
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent.py::test_agent_uses_robot_state_verified_sensors_when_no_override \
  tests/test_gateway_structured_task.py::test_robot_agent_context_uses_runtime_verified_sensors \
  -q
```

Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add src/fireclaw_core/agent/agent.py src/fireclaw_core/gateway/gateway.py tests/test_agent.py tests/test_gateway_structured_task.py
git commit -m "fix: use runtime verified sensors for safety checks"
```

---

### Task 2: Cache ROS1 Sensor Discovery to Keep `/state` Responsive

**Files:**
- Modify: `src/fireclaw_core/ros/ros1_sensor_discovery.py`
- Test: `tests/test_ros1_sensor_discovery.py`

- [ ] **Step 1: Add failing cache test**

Append to `tests/test_ros1_sensor_discovery.py`:

```python
class CountingGraphProvider:
    def __init__(self) -> None:
        self.calls = 0

    def topic_types(self) -> dict[str, str]:
        self.calls += 1
        return {"/scan": "sensor_msgs/LaserScan"}


class CountingMessageProbe:
    def __init__(self) -> None:
        self.calls = 0

    def has_recent_message(self, topic: str, timeout_seconds: float) -> bool:
        self.calls += 1
        return True


def test_ros1_discovery_uses_cache_within_ttl() -> None:
    graph = CountingGraphProvider()
    probe = CountingMessageProbe()
    discovery = Ros1SensorDiscovery(
        graph_provider=graph,
        message_probe=probe,
        cache_ttl_seconds=30.0,
    )

    first = discovery.discover()
    second = discovery.discover()

    assert first.verified_sensors() == ["lidar"]
    assert second.verified_sensors() == ["lidar"]
    assert graph.calls == 1
    assert probe.calls == 1
```

- [ ] **Step 2: Run cache test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py::test_ros1_discovery_uses_cache_within_ttl -q
```

Expected before fix: FAIL because `cache_ttl_seconds` is not accepted or calls are `2`.

- [ ] **Step 3: Implement TTL cache**

Modify `src/fireclaw_core/ros/ros1_sensor_discovery.py`.

Add import:

```python
import time
```

Extend `Ros1SensorDiscovery` fields:

```python
    cache_ttl_seconds: float = 5.0
    _cached_report: SensorDiscoveryReport | None = None
    _cached_at: float = 0.0
```

At the start of `discover()`:

```python
        now = time.monotonic()
        if (
            self._cached_report is not None
            and self.cache_ttl_seconds > 0
            and now - self._cached_at <= self.cache_ttl_seconds
        ):
            return self._cached_report
```

Before every `return SensorDiscoveryReport(...)`, assign through a helper:

```python
    def _remember(self, report: SensorDiscoveryReport) -> SensorDiscoveryReport:
        self._cached_report = report
        self._cached_at = time.monotonic()
        return report
```

Then return with:

```python
        return self._remember(SensorDiscoveryReport(findings=tuple(findings), source="ros1"))
```

and use `_remember(...)` in the exception path too.

- [ ] **Step 4: Add cache TTL override when wiring gateway**

In `src/fireclaw_core/gateway/gateway.py`, keep the default cache unless a profile field is added later. The first fix does not need a profile schema change.

- [ ] **Step 5: Run ROS1 discovery tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_ros1_sensor_discovery.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/ros/ros1_sensor_discovery.py tests/test_ros1_sensor_discovery.py
git commit -m "fix: cache ros1 sensor discovery results"
```

---

### Task 3: Keep `robot-profile discover --write-profile` TOML Parseable

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Add failing regression for existing discovery table**

Append to `tests/test_mission_cli.py`:

```python
def test_robot_profile_discover_write_profile_keeps_existing_discovery_table_parseable(tmp_path, monkeypatch):
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[robot.sensor_discovery]
enabled = true
message_timeout_seconds = 2.0
""".format(data_dir=tmp_path / "robot-data").strip(),
        encoding="utf-8",
    )

    from fireclaw_core.ros import ros1_sensor_discovery

    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliGraphProvider,
        "topic_types",
        lambda self: {"/scan": "sensor_msgs/LaserScan"},
    )
    monkeypatch.setattr(
        ros1_sensor_discovery.Ros1CliMessageProbe,
        "has_recent_message",
        lambda self, topic, timeout_seconds: True,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "mission-cli",
            "robot-profile",
            "discover",
            "--profile",
            str(profile_path),
            "--write-profile",
        ],
    )

    assert main() == 0

    from fireclaw_core.agent.robot_profile import load_robot_capability_profile
    profile = load_robot_capability_profile(profile_path)
    assert profile.sensor_discovery.rules[0].sensor == "lidar"
```

- [ ] **Step 2: Run regression and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_robot_profile_discover_write_profile_keeps_existing_discovery_table_parseable -q
```

Expected before fix: FAIL with TOML duplicate table decode error.

- [ ] **Step 3: Split generated TOML into header and rules**

Modify `_handle_robot_profile()` in `src/fireclaw_core/mission/mission_cli.py`.

Replace the current `lines` generation with two separate lists:

```python
        header_lines: list[str] = [
            "# Suggested FireClaw sensor discovery rules.",
            "# Review before copying into the robot profile.",
            "",
            "[robot.sensor_discovery]",
            "enabled = true",
            f"message_timeout_seconds = {profile.sensor_discovery.message_timeout_seconds:.1f}",
            "",
        ]
        rule_lines: list[str] = []
        for finding in report.findings:
            if finding.status not in {"verified", "degraded"}:
                continue
            rule_lines.extend([
                "[[robot.sensor_discovery.rules]]",
                f'topic_pattern = "{_toml_escape(finding.topic)}"',
                f'message_type = "{_toml_escape(finding.message_type)}"',
                f'sensor = "{_toml_escape(finding.sensor)}"',
                f"confidence = {finding.confidence:.2f}",
                f"confirmed = {str(finding.status == 'verified').lower()}",
                "",
            ])
        text = "\n".join(header_lines + rule_lines)
```

- [ ] **Step 4: Append only rules when writing into an existing profile**

Replace the `if args.write_profile:` block with:

```python
        if args.write_profile:
            existing = output.read_text(encoding="utf-8")
            if "[robot.sensor_discovery]" in existing:
                addition = "\n".join(rule_lines)
            else:
                addition = text
            output.write_text(existing.rstrip() + "\n\n" + addition, encoding="utf-8")
        else:
            output.write_text(text, encoding="utf-8")
```

This avoids duplicate `[robot.sensor_discovery]` tables while preserving explicit operator write-back.

- [ ] **Step 5: Run CLI tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_robot_profile_discover_writes_suggested_rules \
  tests/test_mission_cli.py::test_robot_profile_discover_write_profile_appends_rules \
  tests/test_mission_cli.py::test_robot_profile_discover_write_profile_keeps_existing_discovery_table_parseable \
  -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission/mission_cli.py tests/test_mission_cli.py
git commit -m "fix: keep profile discovery write-back parseable"
```

---

### Task 4: Align Serve CLI and Example Config with Profile Runtime

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `fireclaw.example.toml`
- Test: `tests/test_mission_cli.py`

- [ ] **Step 1: Add serve help regression**

Append to `tests/test_mission_cli.py`:

```python
def test_serve_help_exposes_robot_profile_flag():
    completed = subprocess.run(
        [sys.executable, "-m", "fireclaw_core", "serve", "--help"],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert "--robot-profile" in completed.stdout
```

- [ ] **Step 2: Run help regression and verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_cli.py::test_serve_help_exposes_robot_profile_flag -q
```

Expected before fix: FAIL because `--robot-profile` is absent from serve help.

- [ ] **Step 3: Add serve `--robot-profile` parser and merge**

In `src/fireclaw_core/mission/mission_cli.py`, add to the `serve` parser:

```python
    serve.add_argument(
        "--robot-profile",
        action="append",
        default=None,
        help="Path to robot profile TOML. Repeat for multiple robots.",
    )
```

In the `serve` branch merge dict, add:

```python
            "mission_robot_profiles": args.robot_profile,
```

The existing `run_server_blocking(... robot_profiles=...)` call already consumes `mission_robot_profiles`.

- [ ] **Step 4: Restore `fireclaw.example.toml` profile-driven example**

Modify `fireclaw.example.toml` so the relevant sections are:

```toml
[server]
host = "0.0.0.0"
port = 8766
data_dir = "./data/mission"

[mission]
robot_profiles = ["examples/robot_profiles/gazebo_turtlebot3.toml"]

[robot_gateway]
host = "127.0.0.1"
port = 8765
profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"
workspace_skills_dir = "skills"
dry_run = true
default_session_id = "default"
max_active_execution_tasks = 1
```

Remove `robot_id`, `adapter`, `memory_path`, `event_path`, `task_queue_path`, and static `available_sensors` from `[robot_gateway]`.

- [ ] **Step 5: Run CLI help and config-adjacent tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_mission_cli.py::test_serve_help_exposes_robot_profile_flag \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_gateway_serve_profiles.py \
  -q
```

Expected: selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission/mission_cli.py fireclaw.example.toml tests/test_mission_cli.py
git commit -m "fix: align serve cli and example config with profiles"
```

---

### Task 5: Re-verify Serve Failure and Record Remaining Risk

**Files:**
- Modify: `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`

- [ ] **Step 1: Run focused sensor and gateway tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_sensor_discovery.py \
  tests/test_ros1_sensor_discovery.py \
  tests/test_robot_profile.py \
  tests/test_ros1_adapter_state.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_safety.py \
  tests/test_agent.py \
  tests/test_gateway_structured_task.py \
  tests/test_mission_cli.py \
  -q
```

Expected: selected tests pass.

- [ ] **Step 2: Run `tests/test_serve.py` to classify the remaining failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_serve.py -q
```

Expected before separate serve fix: `test_start_server_submit_and_trace` may still fail with `ConnectionRefusedError` from `RobotSubagentClient.submit_task`. If still failing, record it as a separate pre-existing mission serve test gap, not a sensor discovery regression.

- [ ] **Step 3: Run full suite if time allows**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: either full pass, or the same known `tests/test_serve.py` failure remains. Record exact counts.

- [ ] **Step 4: Append fixup verification to memory**

Append to `memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md`:

```markdown
## Fixup Verification

- Runtime verified sensors SafetyGate regression:
- ROS1 discovery cache regression:
- Profile write-back TOML regression:
- Serve `--robot-profile` CLI regression:
- `fireclaw.example.toml` profile-driven status:
- Focused test command:
- `tests/test_serve.py` status:
- Full suite status:
- Remaining risks:
```

Fill each bullet with concrete command outputs and timestamps.

- [ ] **Step 5: Commit**

```bash
git add memory/2026-06-14/fireclaw-sensor-discovery-design-notes.md
git commit -m "docs: record ros1 sensor discovery fixup verification"
```

---

## Plan Self-Review

Spec coverage:

- Runtime verified sensors ignored by SafetyGate: Task 1.
- Slow `/state` and mission timeout: Task 2 plus Task 5 verification.
- Invalid TOML from `--write-profile`: Task 3.
- Missing `serve --robot-profile`: Task 4.
- `fireclaw.example.toml` stale hand-written fields: Task 4.

Placeholder scan:

- This plan uses concrete file paths, tests, code snippets, commands, and expected outputs.
- No implementation step depends on undefined helper functions.

Type consistency:

- `FireClawAgent._safety_available_sensors()` is introduced before all replacements that call it.
- `Ros1SensorDiscovery.cache_ttl_seconds`, `_cached_report`, and `_cached_at` are introduced before tests depend on them.
- `rule_lines` and `header_lines` are defined before `--write-profile` uses them.

