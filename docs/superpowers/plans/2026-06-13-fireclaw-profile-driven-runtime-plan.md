# FireClaw Profile-Driven Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make FireClaw work like the desired OpenClaw-style flow: users maintain robot profiles, and gateway/mission startup automatically derives robot identity, adapter settings, storage paths, registry entries, skill chains, and LLM-exposed robot tools from those profiles.

**Architecture:** Keep the existing `RobotCapabilityProfile`, `RobotRegistry`, `StructuredRobotTask`, `LLMRobotAgentPlanner`, and gateway runtime. Add only the missing connection layer: profile normalization/validation before gateway construction, profile-backed mission registry construction, and profile-backed skill-chain lookup during mission dispatch. `robots.json` remains a compatibility input, not the primary source for profile-driven deployments.

**Tech Stack:** Python 3.11+, TOML via `tomllib`, existing JSONL stores, existing FireClaw gateway/mission CLI, existing pytest suite.

---

## Current State To Reuse

- Reuse `src/fireclaw_core/agent/robot_profile.py`.
  - Already loads `RobotCapabilityProfile`.
  - Already derives `memory_path`, `event_path`, and `task_queue_path`.
  - Already validates enabled skills and ROS1 remaps through `validate_robot_capability_profile()`.
- Reuse `src/fireclaw_core/agent/robot_tools.py`.
  - Already converts registered skills into LLM tool schemas.
  - Already converts direct LLM tool calls into `RobotLocalPlan`.
- Reuse `src/fireclaw_core/agent/robot_agent.py`.
  - Already supports wrapper tool calls and direct skill tool calls.
  - Already applies robot-agent policy checks.
- Reuse `src/fireclaw_core/task/task_contract.py`.
  - Already accepts `capability_skill_chains` in `structured_task_from_mission_subtask()`.
- Reuse `src/fireclaw_core/agent/robot_registry.py`.
  - Keep `robots.json` loading for backward compatibility.
  - Add profile-backed construction instead of replacing the registry abstraction.

## Work That Must Not Be Repeated

- Do not create a second robot profile type.
- Do not create another skill registry.
- Do not create another LLM tool schema builder.
- Do not create another robot-local planner.
- Do not replace `RobotRegistry`; extend its inputs.
- Do not make mission code parse profile TOML ad hoc. Centralize profile loading helpers.

---

## File Structure

- Modify `src/fireclaw_core/agent/robot_profile.py`
  - Add `capability_skill_chains` to `RobotCapabilityProfile`.
  - Add loader support for a profile table such as `[capability_skill_chains]`.
  - Add helper to load multiple profiles from paths.
- Modify `src/fireclaw_core/agent/robot_registry.py`
  - Add `robot_registry_from_profiles(profiles)` to derive `RobotRegistryEntry` values.
- Modify `src/fireclaw_core/gateway/gateway.py`
  - Load profile before constructing robot adapter and JSONL stores.
  - Apply profile-derived adapter, robot id, ROS1 config, and per-robot data paths.
  - Validate profile at startup and fail fast with actionable errors.
- Modify `src/fireclaw_core/gateway/config.py`
  - Add config support for profile path lists under mission/runtime config.
  - Keep `[robot_gateway].profile_path` for one robot-local gateway.
- Modify `src/fireclaw_core/mission/mission_runtime.py`
  - Build mission `RobotRegistry` from profile paths when provided.
  - Fall back to `robots.json` only when no profile paths are configured.
- Modify `src/fireclaw_core/mission/mission_agent.py`
  - Store profile-derived capability skill chains by robot id.
  - Pass the selected robot's chains into `structured_task_from_mission_subtask()`.
- Modify `src/fireclaw_core/mission/mission_cli.py`
  - Add config support so `python -m fireclaw_core serve` can load mission robot profiles directly.
  - Add a non-required diagnostic command or option to print the derived registry/profile view.
- Modify `fireclaw.example.toml`
  - Remove duplicated robot identity/path fields when `profile_path` is configured.
  - Add a profile-driven mission section for one or more robot profiles.
- Create or modify tests:
  - `tests/test_robot_profile.py`
  - `tests/test_robot_registry.py`
  - `tests/test_gateway_robot_profile_config.py`
  - `tests/test_mission_runtime_profiles.py`
  - `tests/test_mission_agent_profile_skill_chains.py`
  - Existing gateway/mission CLI tests where needed.

---

## Task 1: Extend Robot Profile With Capability Skill Chains

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Modify: `examples/robot_profiles/gazebo_turtlebot3.toml`
- Test: `tests/test_robot_profile.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove a profile can declare skill chains and that chains are normalized to tuples/lists of non-empty strings.

```python
def test_load_robot_capability_profile_with_skill_chains(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "debug-robot-1"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/debug-robot-1"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]

[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )

    profile = load_robot_capability_profile(profile_path)

    assert profile.capability_skill_chains == {
        "search_for_victims": ("navigate_to_floor", "search_for_victims", "report_status")
    }
```

Add a validation test:

```python
def test_profile_validation_rejects_skill_chain_outside_enabled_skills() -> None:
    profile = RobotCapabilityProfile(
        robot_id="debug-robot-1",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=Path("data/robots/debug-robot-1"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor",),
        llm_exposed_skills=("navigate_to_floor",),
        capability_skill_chains={"search_for_victims": ("search_for_victims",)},
    )

    errors = validate_robot_capability_profile(profile, create_default_skill_registry())

    assert "capability chain for 'search_for_victims' references disabled skill 'search_for_victims'" in errors
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: FAIL because `RobotCapabilityProfile` has no `capability_skill_chains` field yet.

- [ ] **Step 3: Implement minimal profile parsing**

Add a frozen dataclass field:

```python
capability_skill_chains: dict[str, tuple[str, ...]] = field(default_factory=dict)
```

Use `dataclasses.field`; this requires changing the import:

```python
from dataclasses import dataclass, field
```

Parse the optional table:

```python
capability_skill_chains = _skill_chain_map(raw.get("capability_skill_chains", {}))
```

Pass it into `RobotCapabilityProfile(...)`.

Add helper:

```python
def _skill_chain_map(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("capability_skill_chains must be a TOML table.")
    chains: dict[str, tuple[str, ...]] = {}
    for capability, skills in value.items():
        if not isinstance(capability, str) or not capability.strip():
            raise ValueError("capability_skill_chains keys must be non-empty strings.")
        if not isinstance(skills, list) or not skills:
            raise ValueError(f"capability_skill_chains.{capability} must be a non-empty list of strings.")
        normalized = tuple(item.strip() for item in skills if isinstance(item, str) and item.strip())
        if len(normalized) != len(skills):
            raise ValueError(f"capability_skill_chains.{capability} must contain only non-empty strings.")
        chains[capability.strip()] = normalized
    return chains
```

Extend validation:

```python
for capability, chain in profile.capability_skill_chains.items():
    if capability not in profile.capabilities:
        errors.append(f"capability chain {capability!r} is not declared in robot.capabilities")
    for skill_name in chain:
        if skill_name not in profile.enabled_skills:
            errors.append(f"capability chain for {capability!r} references disabled skill {skill_name!r}")
```

- [ ] **Step 4: Update example profile**

Add this table to `examples/robot_profiles/gazebo_turtlebot3.toml`:

```toml
[capability_skill_chains]
search_for_victims = ["navigate_to_floor", "search_for_victims", "report_status"]
```

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_profile.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/agent/robot_profile.py examples/robot_profiles/gazebo_turtlebot3.toml tests/test_robot_profile.py
git commit -m "feat: add profile-backed capability skill chains"
```

---

## Task 2: Derive Robot Registry From Profiles

**Files:**
- Modify: `src/fireclaw_core/agent/robot_profile.py`
- Modify: `src/fireclaw_core/agent/robot_registry.py`
- Test: `tests/test_robot_registry.py`

- [ ] **Step 1: Write failing tests**

```python
def test_robot_registry_from_profiles_uses_profile_registry_entries() -> None:
    profile = RobotCapabilityProfile(
        robot_id="gazebo_turtlebot3",
        base_url="http://127.0.0.1:8765",
        adapter="ros1",
        ros1_config="examples/ros1_configs/gazebo_turtlebot3_move_base.yaml",
        data_dir=Path("data/robots/gazebo_turtlebot3"),
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "search_for_victims", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "search_for_victims", "report_status"),
    )

    registry = robot_registry_from_profiles([profile])

    entry = registry.get("gazebo_turtlebot3")
    assert entry is not None
    assert entry.base_url == "http://127.0.0.1:8765"
    assert entry.capabilities == ("search_for_victims",)
```

```python
def test_load_robot_capability_profiles_preserves_order(tmp_path: Path) -> None:
    first = tmp_path / "r1.toml"
    second = tmp_path / "r2.toml"
    first.write_text(PROFILE_TEMPLATE.format(robot_id="r1", port=8765), encoding="utf-8")
    second.write_text(PROFILE_TEMPLATE.format(robot_id="r2", port=8766), encoding="utf-8")

    profiles = load_robot_capability_profiles([first, second])

    assert [profile.robot_id for profile in profiles] == ["r1", "r2"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_registry.py tests/test_robot_profile.py -q
```

Expected: FAIL because `robot_registry_from_profiles()` and `load_robot_capability_profiles()` do not exist.

- [ ] **Step 3: Implement profile list loading**

In `robot_profile.py`:

```python
def load_robot_capability_profiles(paths: list[str | Path] | tuple[str | Path, ...]) -> list[RobotCapabilityProfile]:
    return [load_robot_capability_profile(path) for path in paths]
```

In `robot_registry.py`:

```python
from fireclaw_core.agent.robot_profile import RobotCapabilityProfile


def robot_registry_from_profiles(profiles: list[RobotCapabilityProfile]) -> RobotRegistry:
    return RobotRegistry([
        RobotRegistryEntry(
            robot_id=profile.robot_id,
            base_url=profile.base_url,
            capabilities=profile.capabilities,
            enabled=profile.enabled,
        )
        for profile in profiles
    ])
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_robot_registry.py tests/test_robot_profile.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/agent/robot_profile.py src/fireclaw_core/agent/robot_registry.py tests/test_robot_registry.py tests/test_robot_profile.py
git commit -m "feat: derive robot registry from profiles"
```

---

## Task 3: Make Robot Gateway Profile-Driven At Startup

**Files:**
- Modify: `src/fireclaw_core/gateway/gateway.py`
- Modify: `src/fireclaw_core/gateway/config.py`
- Test: `tests/test_gateway_robot_profile_config.py`
- Update if needed: `tests/test_gateway_robot_agent_cli.py`

- [ ] **Step 1: Write failing tests**

Add a test proving profile fields are applied before adapter and store construction:

```python
def test_gateway_config_applies_robot_profile_paths(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "profile-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "{data_dir}"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip().format(data_dir=str(tmp_path / "robots" / "profile-robot")),
        encoding="utf-8",
    )

    config = GatewayConfig(
        adapter="dry-run",
        robot_id="cli-robot",
        memory_path=str(tmp_path / "old-memory.jsonl"),
        event_path=str(tmp_path / "old-events.jsonl"),
        task_queue_path=str(tmp_path / "old-tasks.jsonl"),
        robot_profile_path=str(profile_path),
    )

    resolved = resolve_gateway_config_with_profile(config)

    assert resolved.robot_id == "profile-robot"
    assert resolved.adapter == "simulator"
    assert resolved.memory_path.endswith("profile-robot/memory.jsonl")
    assert resolved.event_path.endswith("profile-robot/events.jsonl")
    assert resolved.task_queue_path.endswith("profile-robot/tasks.jsonl")
```

Add a fail-fast validation test:

```python
def test_gateway_rejects_invalid_robot_profile_before_start(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "bad-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/bad-robot"
capabilities = ["search_for_victims"]
enabled_skills = ["missing_skill"]
llm_exposed_skills = ["missing_skill"]
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="enabled skill 'missing_skill' is not registered"):
        FireClawGateway(GatewayConfig(robot_profile_path=str(profile_path)))
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_gateway_robot_agent_cli.py -q
```

Expected: FAIL because profile currently loads after adapter/store construction and validation is not enforced.

- [ ] **Step 3: Implement gateway config resolution**

In `gateway.py`, add a helper near `GatewayConfig`:

```python
def resolve_gateway_config_with_profile(config: GatewayConfig) -> GatewayConfig:
    if not config.robot_profile_path:
        return config
    from dataclasses import replace
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile

    profile = load_robot_capability_profile(config.robot_profile_path)
    return replace(
        config,
        adapter=profile.adapter,
        robot_id=profile.robot_id,
        ros1_config_path=profile.ros1_config,
        memory_path=str(profile.memory_path),
        event_path=str(profile.event_path),
        task_queue_path=str(profile.task_queue_path),
    )
```

In `FireClawGateway.__init__`, resolve before constructing robot/store objects:

```python
resolved_config = resolve_gateway_config_with_profile(config)
self.config = resolved_config
self.robot_profile = None
if resolved_config.robot_profile_path:
    from fireclaw_core.agent.robot_profile import load_robot_capability_profile
    self.robot_profile = load_robot_capability_profile(resolved_config.robot_profile_path)
self._validate_robot_profile()
self.robot = create_robot_adapter(resolved_config.adapter, resolved_config.robot_id, config_path=resolved_config.ros1_config_path)
self.memory = JsonlMemoryStore(resolved_config.memory_path)
self.events = EventLedger(resolved_config.event_path)
self.task_queue = JsonlTaskQueue(resolved_config.task_queue_path)
```

Add `_validate_robot_profile()`:

```python
def _validate_robot_profile(self) -> None:
    if self.robot_profile is None:
        return
    from fireclaw_core.agent.robot_profile import validate_robot_capability_profile
    from fireclaw_core.execution.skills import create_default_skill_registry
    from fireclaw_core.ros.ros1_config import load_ros1_adapter_config

    ros1_config = None
    if self.robot_profile.ros1_config is not None:
        ros1_config = load_ros1_adapter_config(self.robot_profile.ros1_config)
    errors = validate_robot_capability_profile(
        self.robot_profile,
        create_default_skill_registry(),
        ros1_config=ros1_config,
    )
    if errors:
        raise ValueError("Invalid robot profile: " + "; ".join(errors))
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_gateway_robot_profile_config.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fireclaw_core/gateway/gateway.py src/fireclaw_core/gateway/config.py tests/test_gateway_robot_profile_config.py tests/test_gateway_robot_agent_cli.py tests/test_gateway_structured_task.py
git commit -m "feat: make robot gateway derive runtime config from profile"
```

---

## Task 4: Let Mission Runtime Load Profiles Directly

**Files:**
- Modify: `src/fireclaw_core/gateway/config.py`
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_mission_runtime_profiles.py`

- [ ] **Step 1: Write failing tests**

```python
def test_build_mission_agent_from_profile_paths(tmp_path: Path) -> None:
    profile_path = tmp_path / "robot.toml"
    profile_path.write_text(
        """
[robot]
id = "profile-robot"
base_url = "http://127.0.0.1:8765"
adapter = "simulator"
data_dir = "data/robots/profile-robot"
capabilities = ["search_for_victims"]
enabled_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
llm_exposed_skills = ["navigate_to_floor", "search_for_victims", "report_status"]
""".strip(),
        encoding="utf-8",
    )
    paths = MissionRuntimePaths(
        robot_registry=tmp_path / "unused-robots.json",
        robot_profiles=(profile_path,),
        mission_registry=tmp_path / "missions.jsonl",
    )

    agent = build_mission_agent_from_paths(paths, operator_id="op", role="operator")

    assert agent.registry.get("profile-robot") is not None
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime_profiles.py -q
```

Expected: FAIL because `MissionRuntimePaths` has no `robot_profiles` field.

- [ ] **Step 3: Add profile-backed registry path**

Extend `MissionRuntimePaths`:

```python
robot_profiles: tuple[Path, ...] = ()
```

In `build_mission_agent_from_paths()`:

```python
if paths.robot_profiles:
    from fireclaw_core.agent.robot_profile import load_robot_capability_profiles
    from fireclaw_core.agent.robot_registry import robot_registry_from_profiles

    profiles = load_robot_capability_profiles(list(paths.robot_profiles))
    registry = robot_registry_from_profiles(profiles)
else:
    registry = load_robot_registry(paths.robot_registry)
```

Pass `registry=registry` into `MissionAgent`.

- [ ] **Step 4: Wire config parsing**

In `load_config()`, parse a mission profile list. Use this TOML shape:

```toml
[mission]
robot_profiles = ["examples/robot_profiles/gazebo_turtlebot3.toml"]
```

Add:

```python
mission = raw.get("mission", {})
cfg["mission_robot_profiles"] = mission.get("robot_profiles")
```

In `mission_cli.py`, make `_runtime_paths` or the existing path builder pass `robot_profiles=tuple(Path(p) for p in merged_config["mission_robot_profiles"])` when the list exists.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_runtime_profiles.py tests/test_mission_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/gateway/config.py src/fireclaw_core/mission/mission_runtime.py src/fireclaw_core/mission/mission_cli.py tests/test_mission_runtime_profiles.py tests/test_mission_cli.py
git commit -m "feat: load mission robot registry from profiles"
```

---

## Task 5: Pass Profile Skill Chains Into Mission Dispatch

**Files:**
- Modify: `src/fireclaw_core/mission/mission_runtime.py`
- Modify: `src/fireclaw_core/mission/mission_agent.py`
- Test: `tests/test_mission_agent_profile_skill_chains.py`

- [ ] **Step 1: Write failing test**

```python
def test_mission_agent_uses_selected_robot_profile_skill_chain(tmp_path: Path) -> None:
    profile = RobotCapabilityProfile(
        robot_id="profile-robot",
        base_url="http://127.0.0.1:8765",
        adapter="simulator",
        ros1_config=None,
        data_dir=tmp_path / "profile-robot",
        capabilities=("search_for_victims",),
        enabled_skills=("navigate_to_floor", "report_status"),
        llm_exposed_skills=("navigate_to_floor", "report_status"),
        capability_skill_chains={"search_for_victims": ("navigate_to_floor", "report_status")},
    )
    client = CapturingSubagentClient()
    agent = MissionAgent(
        registry=robot_registry_from_profiles([profile]),
        subagent_client=client,
        profile_skill_chains_by_robot={"profile-robot": profile.capability_skill_chains},
    )

    subtask = MissionSubtask(
        robot_id="profile-robot",
        command="去二楼搜索",
        floor=2,
        capability_required="search_for_victims",
        execution_group=0,
    )
    result = agent.submit_subtask(
        "profile-robot",
        "去二楼搜索",
        session_id="m1",
        mission_subtask=subtask,
    )

    assert result["status"] == "accepted"
    assert client.last_structured_task["required_skills"] == ["navigate_to_floor", "report_status"]
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_profile_skill_chains.py -q
```

Expected: FAIL because `MissionAgent` does not accept or use `profile_skill_chains_by_robot`.

- [ ] **Step 3: Add `MissionAgent` constructor field**

In `MissionAgent.__init__`:

```python
profile_skill_chains_by_robot: dict[str, dict[str, tuple[str, ...]]] | None = None,
```

Store:

```python
self.profile_skill_chains_by_robot = profile_skill_chains_by_robot or {}
```

In both calls to `structured_task_from_mission_subtask(...)`, pass:

```python
capability_skill_chains=self.profile_skill_chains_by_robot.get(robot_id),
```

- [ ] **Step 4: Pass chains from mission runtime**

In `build_mission_agent_from_paths()`, when profile paths are present:

```python
profile_skill_chains_by_robot = {
    profile.robot_id: profile.capability_skill_chains
    for profile in profiles
}
```

Pass this into `MissionAgent(...)`.

When no profiles are configured, pass `{}`.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mission_agent_profile_skill_chains.py tests/test_mission_agent_structured_task.py tests/test_task_contract.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fireclaw_core/mission/mission_agent.py src/fireclaw_core/mission/mission_runtime.py tests/test_mission_agent_profile_skill_chains.py tests/test_mission_agent_structured_task.py tests/test_task_contract.py
git commit -m "feat: use profile skill chains during mission dispatch"
```

---

## Task 6: Simplify Config So Users Maintain Profiles Instead Of Duplicated Fields

**Files:**
- Modify: `fireclaw.example.toml`
- Modify: `README.md`
- Modify: `docs/deployment/ros1-gazebo-debugging-guide.md`
- Test: CLI smoke commands

- [ ] **Step 1: Update example config**

Use this shape:

```toml
[server]
host = "0.0.0.0"
port = 8766
data_dir = "./data"

[planner]
type = "llm"

[provider]
base_url = "https://token-plan-cn.xiaomimimo.com/v1"
api_key = "your-api-key"
model = "mimo-v2.5"

[mission]
robot_profiles = ["examples/robot_profiles/gazebo_turtlebot3.toml"]

[robot_gateway]
profile_path = "examples/robot_profiles/gazebo_turtlebot3.toml"
host = "127.0.0.1"
port = 8765
dry_run = true
workspace_skills_dir = "skills"
default_session_id = "default"
max_active_execution_tasks = 1
```

Do not repeat `robot_id`, `adapter`, `ros1_config`, `memory_path`, `event_path`, or `task_queue_path` in `[robot_gateway]` when `profile_path` is present.

- [ ] **Step 2: Update docs with the new startup commands**

Document:

```bash
cp fireclaw.example.toml fireclaw.toml
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
.venv/bin/python -m fireclaw_core mission
```

Also document the explicit compatibility path:

```bash
.venv/bin/python -m fireclaw_core robot-profile export \
  --profile examples/robot_profiles/gazebo_turtlebot3.toml \
  --output data/robots.json
```

Mark this export path as optional and mainly for legacy registry workflows.

- [ ] **Step 3: Run CLI smoke checks**

Run:

```bash
.venv/bin/python -m fireclaw_core --help >/tmp/fireclaw-help.txt
.venv/bin/python -m fireclaw_core robot-gateway --help >/tmp/fireclaw-robot-gateway-help.txt
.venv/bin/python -m fireclaw_core serve --help >/tmp/fireclaw-serve-help.txt
```

Expected: all commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add fireclaw.example.toml README.md docs/deployment/ros1-gazebo-debugging-guide.md
git commit -m "docs: document profile-driven startup"
```

---

## Task 7: End-To-End Regression For Profile-Driven Flow

**Files:**
- Modify or create: `tests/test_profile_driven_runtime_e2e.py`
- Optional modify: `src/fireclaw_core/ros/gazebo_smoke.py`

- [ ] **Step 1: Write an in-process e2e test**

The test should:

1. Create a temporary robot profile.
2. Start a robot-local `FireClawGateway` with only `robot_profile_path`.
3. Build a mission agent from `robot_profiles`.
4. Submit a natural-language mission command.
5. Assert the submitted structured task uses the profile skill chain.
6. Assert events include robot-agent events when robot-agent mode is enabled.

Use simulator adapter and deterministic planner unless the existing test harness already provides an LLM stub.

- [ ] **Step 2: Run e2e test**

Run:

```bash
.venv/bin/python -m pytest tests/test_profile_driven_runtime_e2e.py -q
```

Expected: PASS.

- [ ] **Step 3: Run focused regression suite**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_robot_profile.py \
  tests/test_robot_registry.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_gateway_structured_task.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_mission_runtime_profiles.py \
  tests/test_mission_agent_profile_skill_chains.py \
  tests/test_mission_agent_structured_task.py \
  tests/test_task_contract.py \
  tests/test_mission_cli.py \
  -q
```

Expected: PASS.

- [ ] **Step 4: Run full suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS or only known skipped tests.

- [ ] **Step 5: Commit**

```bash
git add tests/test_profile_driven_runtime_e2e.py src/fireclaw_core/ros/gazebo_smoke.py
git commit -m "test: cover profile-driven runtime flow"
```

---

## Task 8: Clean Up Current Tracking Gap

**Files:**
- Add: `src/fireclaw_core/gateway/__main__.py`
- Add or update: plan/memory documents as needed

- [ ] **Step 1: Inspect untracked gateway module**

Run:

```bash
git status --short
sed -n '1,120p' src/fireclaw_core/gateway/__main__.py
```

Expected: `src/fireclaw_core/gateway/__main__.py` is present and intentionally needed for `python -m fireclaw_core.gateway`.

- [ ] **Step 2: Add a CLI regression if one does not already exist**

Add or keep a test that runs:

```bash
.venv/bin/python -m fireclaw_core.gateway --help
```

Expected: exit 0.

- [ ] **Step 3: Commit the module**

```bash
git add src/fireclaw_core/gateway/__main__.py tests/test_gateway_robot_agent_cli.py
git commit -m "fix: expose gateway package entrypoint"
```

---

## Final Verification

Run:

```bash
rg -n "tp-[A-Za-z0-9]|sk-[A-Za-z0-9]|api_key\\s*=\\s*\"[^\"]+\"|api-key\\s+\"[^\"]+\"|token-plan" \
  fireclaw.example.toml README.md docs examples src tests \
  -g '!openclaw/**'
```

Expected: no real API keys. Placeholder values such as `your-api-key`, `sk-test`, and redaction tests are acceptable.

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full test suite passes.

## Expected User Experience After This Plan

Robot-local gateway:

```bash
.venv/bin/python -m fireclaw_core robot-gateway --config fireclaw.toml
```

Mission gateway:

```bash
.venv/bin/python -m fireclaw_core serve --config fireclaw.toml
```

Interactive mission console:

```bash
.venv/bin/python -m fireclaw_core mission
```

The operator maintains:

- `fireclaw.toml` for provider and global runtime choices.
- `examples/robot_profiles/<robot>.toml` or private `profiles/<robot>.toml` files for robot identity, adapter, ROS config, data directory, capabilities, enabled skills, LLM-exposed skills, and capability-to-skill chains.

The operator no longer needs to manually keep `robots.json`, robot gateway `robot_id`, adapter paths, JSONL storage paths, and mission skill chains synchronized.

## Self-Review

- Spec coverage: the plan covers profile-as-source-of-truth for gateway startup, mission registry, mission dispatch skill chains, config simplification, tests, and docs.
- Duplication check: the plan explicitly reuses existing profile, registry, LLM tool, robot-agent planner, task contract, and gateway runtime code.
- Risk check: the plan keeps `robots.json` as a compatibility fallback so existing tests and workflows do not break.
- Security check: the plan keeps real provider keys out of example files and includes a final secret scan.
