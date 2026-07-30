# ROS Diagnostics And Mission Observability Plan

## 2026-07-30 16:41:34 +08

### Task goal

Record the agreed direction for giving the Robot Agent useful ROS
observability while allowing the Mission Agent to obtain bounded diagnostic
evidence when it needs to coordinate or replan.

No implementation was requested in this turn. This record is the handoff for
the next session.

### Current progress

- Commit `3daac5b` has been pushed to `origin/master`.
- FireClaw now has `simulation` and `real` deployment tool profiles.
- Mission Agent and Robot Agent can use the shared `AgentToolRuntime`.
- The built-in computer plugin currently contributes:
  - `computer_list_files`;
  - `computer_read_file`;
  - `computer_write_file`;
  - `computer_exec`.
- In `simulation`, all four can be projected when the role sandbox is
  explicitly enabled and permitted by the role allowlist.
- In `real`, process tools are hard-blocked, bounded mutation requires exact
  backend authorization, and read tools require an explicitly enabled role
  workspace.
- Generic Agent Tool results are advisory and cannot override current robot
  state, emergency-stop state, or physical-action evidence.

### Problem identified

`computer_exec` is not a live ROS diagnostic interface.

The current Docker sandbox:

- uses a minimal Python image without ROS Noetic;
- defaults to `network="none"`;
- does not source the Navigation or robot catkin overlays;
- does not receive a trusted `ROS_MASTER_URI`, `ROS_IP`, or `ROS_HOSTNAME`;
- does not share the ROS1 XMLRPC/TCPROS network topology;
- does not mount host credentials, devices, or ROS runtime state.

Therefore neither Agent can currently use `computer_exec` to perform live:

- `rostopic list`;
- `rostopic echo /scan`;
- `rostopic hz`;
- TF lookup;
- `/move_base/status` inspection;
- navigation diagnostics.

This is an intentional computer-sandbox boundary, but it leaves Robot Agent
ROS observability incomplete. Offline logs and files copied into the sandbox
can be analyzed; live ROS cannot.

### User decisions and architectural conclusion

1. Robot Agent should have local, typed, read-only ROS diagnostic Tools.
2. Mission Agent also needs relevant robot diagnostic information for
   coordination, replanning, incident reconstruction, and operator reporting.
3. Mission Agent should not directly connect to every robot's ROS master or
   continuously subscribe to raw topics.
4. Mission Agent should normally receive structured summaries and evidence
   references, then request a bounded raw sample from a specific Robot Agent
   only when necessary.
5. Important raw evidence should be archived, but a full ROS topic firehose
   must not be inserted into the LLM context or continuously copied to the
   central control plane.
6. The existing HTTPS Mission-to-Robot Gateway, `request_observation`,
   snapshot, evidence ID, memory, and audit boundaries should be reused.
7. Physical publication, service invocation, parameter mutation, navigation
   cancellation, and recovery are separate future Tools and must not be
   smuggled into read-only diagnostics.

### Proposed Robot Agent ROS Tools

Initial read-only Tool family:

```text
ros_topic_list()
ros_topic_info(topic)
ros_topic_sample(topic, message_count, timeout_seconds)
ros_topic_rate(topic, window_seconds)
tf_lookup(source_frame, target_frame, timeout_seconds)
move_base_status()
navigation_diagnostics()
```

The Robot Agent calls these Tools locally through a trusted ROS1 backend. It
does not compose an arbitrary `rostopic`, `rosservice`, `rosparam`, or shell
command.

Each Tool should return a structured envelope containing at least:

```text
robot_id
tool_name
observed_at
source
topic_or_frames
ros_message_type
sampling_window
message_count
freshness_seconds
truncated
payload_summary
evidence_ids
requires_current_state_revalidation
```

Large arrays such as laser scans and costmaps should be summarized by default.
A bounded raw sample may be stored as evidence without being copied wholesale
into the model prompt.

### Proposed Mission Agent Tools

Mission Agent should use remote diagnostic and evidence Tools rather than a
direct ROS connection:

```text
get_robot_health(robot_id)
get_navigation_diagnostics(robot_id)
request_robot_diagnostic(robot_id, diagnostic_type, reason)
request_topic_sample(robot_id, topic, message_count, timeout_seconds)
get_robot_evidence(evidence_id)
```

Expected flow:

```text
Mission Agent emits a structured diagnostic request
-> Mission Gateway validates mission, robot, role, and rate scope
-> request is sent over HTTPS to the named Robot Agent
-> Robot Agent executes one typed local ROS read
-> Robot Agent stores bounded raw evidence locally
-> Robot Agent returns a structured result plus evidence IDs
-> Mission Agent receives advisory diagnostic context
-> current state or physical action is revalidated separately
```

Mission Agent must not receive a generic remote `rostopic` shell Tool.

### Three-layer information model

#### 1. Current operational snapshot

The normal Mission Agent planning input should contain compact, fresh state:

```text
robot online state
battery
current map-frame pose
navigation lifecycle state
sensor health and freshness
emergency-stop state
current task and capacity
latest bounded error summary
snapshot timestamp and ID
```

This remains the main authoritative planning input.

#### 2. Structured diagnostic report

Robot Agent should convert local ROS evidence into a bounded report, for
example:

```json
{
  "type": "navigation_blocked",
  "robot_id": "robot-a",
  "goal_id": "goal-17",
  "observed_at": "2026-07-30T12:00:00Z",
  "symptoms": [
    "cmd_vel was generated while odom displacement stayed below 0.02 m",
    "the local planner reported oscillation"
  ],
  "suspected_causes": [
    "configured footprint or inflation may reject the doorway",
    "the robot may be physically immobilized"
  ],
  "evidence_ids": [
    "ros-sample-101",
    "tf-check-32",
    "move-base-status-18"
  ],
  "freshness_seconds": 1.2
}
```

Symptoms tied to sampled evidence are observations. Suspected causes produced
by rules or an LLM remain advisory hypotheses.

#### 3. Raw evidence archive

Raw samples should be stored outside the prompt and referenced by metadata:

```text
evidence_id
mission_id
task_id
robot_id
topic
message_type
sample_start_at
sample_end_at
message_count
encoding or bag format
content_hash
size_bytes
local storage reference
central object reference, if synchronized
retention class
access classification
```

Historical evidence must never override a fresh current snapshot.

### Storage and retention direction

Use a robot-local rolling evidence buffer for high-rate operational topics.
An initial policy to test experimentally is:

- normal rolling window: approximately 60 seconds;
- anomaly freeze: approximately 30 seconds before and 10 seconds after the
  trigger;
- always retain structured task, action, safety, and diagnostic events;
- retain raw topic windows only for failures, safety events, explicit
  diagnostic requests, or experiment configurations;
- use optional complete rosbag recording only for controlled experiments.

Candidate anomaly triggers:

- navigation timeout or abort;
- no displacement despite repeated motion commands;
- TF discontinuity or staleness;
- topic frequency below threshold;
- sensor stream loss;
- emergency stop;
- SafetyGate rejection;
- unknown outcome after restart;
- operator diagnostic capture.

Central storage should normally keep the structured report, metadata, hashes,
and references. Large raw bags should remain local until requested or be
uploaded to an object store with an explicit retention policy.

### Safety requirements

Typed ROS diagnostics must enforce:

- Robot Agent role and robot identity binding;
- mission/task scope when a task is active;
- topic and frame allowlists;
- explicit denial for `/cmd_vel` publication and all other publication;
- no arbitrary ROS service calls;
- no arbitrary ROS parameter writes;
- bounded message count, duration, frequency, and serialized byte count;
- timeout and cancellation;
- message-type validation;
- truncation of large arrays and nested messages;
- source timestamp, receipt timestamp, and freshness;
- topic namespace normalization for multi-robot deployments;
- audit event and evidence hash generation;
- redaction/access controls for cameras, maps, victim data, and private site
  information;
- fail-closed behavior on ROS master loss, type mismatch, malformed messages,
  stale TF, or serialization failure.

Read-only diagnostic output remains advisory to the LLM. If selected fields
need to become authoritative state, a separate trusted observation-ingestion
path must validate and commit them into a new snapshot or belief record.

### Real and simulation behavior

- The same typed read-only ROS Tool contracts should work in both deployment
  modes.
- In simulation, the backend connects to the simulator ROS graph.
- In real deployment, the backend connects locally to the robot ROS graph.
- The Tools should run beside the Robot Gateway/ROS adapter where ROS is
  actually available, not inside the generic computer Docker sandbox.
- Mission Agent accesses them only through a remote diagnostic request to a
  selected Robot Agent.
- Any future publish, cancel, clear-costmap, dynamic-reconfigure, or parameter
  mutation Tool must use a different effect class and a stronger policy,
  approval, and physical-safety path.

### Narrow-door example

```text
1. navigate_to_point stops making progress.
2. Robot Agent requests move_base status, odom progress, cmd_vel activity,
   laser summary, and TF health through typed local Tools.
3. The evidence buffer freezes the incident window.
4. Robot Agent reports observed symptoms, advisory cause hypotheses, and
   evidence IDs.
5. Mission Agent receives the compact report.
6. Mission Agent either replans immediately or requests one additional
   bounded diagnostic sample.
7. Robot Agent returns that sample and archives the raw evidence.
8. Mission Agent decides whether to continue local recovery, assign another
   robot, revise the target, hold position, or escalate to the operator.
```

### OpenClaw-first implementation note

There is no direct OpenClaw analogue for ROS topics. Before implementation,
reuse the already inspected OpenClaw-derived FireClaw boundaries:

- unified Tool contribution through `FireClawPluginHost`;
- pre-model allow/deny projection;
- shared `AgentHarness` structured Tool Call validation;
- `before_tool_call` interception and final-argument revalidation;
- `BoundedAgentLoop` limits, cancellation, checkpoint, and unknown-outcome
  handling;
- audit and result-authority metadata.

Do not add a second ROS-specific registry or a direct LLM shell path. The
ROS-specific part should be a backend/adapter implementation behind normal
Agent Tool contracts.

### Recommended implementation order

1. Define typed ROS diagnostic request/result contracts and limits.
2. Implement a Robot-local ROS1 diagnostic backend with mockable protocol
   boundaries.
3. Register read-only Tools through the existing Plugin Host.
4. Project the Tools only to eligible Robot Agents through deployment and
   capability policy.
5. Persist structured diagnostic events and bounded raw evidence metadata.
6. Add the Mission-to-Robot remote diagnostic request Tool over the existing
   HTTPS Gateway.
7. Add Mission Agent evidence lookup and on-demand bounded sample retrieval.
8. Add anomaly-triggered local rolling capture.
9. Validate with TurtleBot3 Gazebo before enabling the same read-only
   contracts on real hardware.

### Acceptance tests to add

- list and sample allowlisted topics in Gazebo;
- reject non-allowlisted and control topics;
- reject publish, service, and parameter mutation attempts;
- bound message count, bytes, time, and output size;
- handle ROS master unavailable and topic type changes;
- validate TF freshness and frame errors;
- distinguish move_base pending, active, succeeded, aborted, and preempted;
- preserve source and receipt timestamps;
- produce evidence hashes and stable IDs;
- prevent Mission Agent from addressing a robot outside its mission scope;
- handle robot communication loss without treating stale evidence as current;
- verify raw evidence is omitted from normal LLM context;
- verify incident reports remain reconstructable after process restart;
- test camera/map redaction and access policy;
- test simulation/real separation.

### Research-level impact

This is necessary engineering for a credible embodied Agent, but the API alone
is not a publication contribution. A research claim could emerge only with
experiments comparing:

- unrestricted raw ROS access;
- structured summaries only;
- hierarchical summary plus on-demand evidence;
- different rolling-window and trigger policies;
- central-only versus robot-local diagnosis.

Useful outcomes include task success, diagnostic accuracy, recovery latency,
unsafe action proposal rate, bandwidth, storage, model tokens, stale-state
errors, and incident reconstructability.

### Remaining uncertainties

- Exact initial topic allowlist for TurtleBot3 and future firefighting robots.
- Whether raw evidence should use rosbag1, MCAP, or a small typed record store.
- Retention time and upload policy under degraded communication.
- Access classification for camera, thermal, map, and victim observations.
- How structured diagnostic observations should enter the existing belief
  reconciliation pipeline.
- Which navigation recovery and parameter actions should later become
  separately authorized Tools.
- Clock-skew and time-synchronization requirements across multiple robots.

### Files inspected during this discussion

- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/agent/tool_runtime.py`
- `src/fireclaw_core/policy/deployment.py`
- `src/fireclaw_core/planner/planner_builder.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `src/fireclaw_core/mission/mission_deliberation.py`
- `src/fireclaw_core/agent/robot_deliberation.py`
- `src/fireclaw_core/gateway/gateway.py`
- `src/fireclaw_core/execution/skill_plugin.py`
- `src/fireclaw_core/execution/builtin_physical_skills.py`
- `docs/architecture/deployment-tool-policy.md`
- `extensions/navigation-move-base/README.md`
- `extensions/navigation-move-base/skills/navigation/SKILL.md`
- `robots/turtlebot3_burger/README.md`
- the relevant OpenClaw tool policy, sandbox, hook, execution validation,
  plugin registry, approval, session admission, and command queue analogues
  recorded in the July 29 and July 30 memory files.

### Validation state

- No code behavior changed in this planning-only update.
- No tests were required or run for this record.
- The latest committed full-suite baseline remains:
  `1855 passed, 6 skipped`.

### Next recommended step

Start with the Robot-local typed ROS read-only Tool contracts and a mocked
backend test suite. Do not begin with arbitrary shell access, ROS publishing,
navigation parameter mutation, or Mission Agent direct ROS connectivity.

## 2026-07-30 17:04:53 +08

### Implementation update

The first Robot-local ROS diagnostics phase is implemented.

#### OpenClaw-first structure reused

Before editing, the following upstream-derived boundaries were rechecked with
CodeGraph:

- OpenClaw Tool policy filtering and bounded audit logging;
- final-argument execution validation;
- `before_tool_call` hooks;
- plugin-owned Tool registration and effective Tool inventory.

FireClaw reuses its existing OpenClaw-derived equivalents:

```text
FireClawPluginHost
-> AgentToolRuntime role/mode/allow-deny projection
-> before_tool_call final-argument validation
-> typed backend handler
-> agent_tool.execution audit event
-> advisory observation returned to the next Robot Agent turn
```

OpenClaw has no ROS graph analogue. The FireClaw-specific adaptation is the
robot-local, read-only, bounded ROS1 backend and the physical-state authority
boundary.

#### Files added

- `src/fireclaw_core/ros/ros1_diagnostics.py`
  - `SubprocessRos1CommandRunner` uses backend-owned argv, never a shell;
  - hard timeout, process-group termination, output byte cap and UTF-8
    replacement decoding;
  - `PYTHONUNBUFFERED=1` preserves bounded `rostopic hz` output before timeout;
  - topic, frame and action allowlists;
  - structured parsers for topic graph/info/sample/rate, TF and actionlib
    status;
  - YAML safe loading with aliases disabled, plus depth, key, list and string
    truncation;
  - parallel `navigation_diagnostics` over move_base status, scan, odom,
    cmd_vel and TF;
  - robot-identity-bound evidence IDs, timestamps, bound metadata and
    advisory authority.
- `src/fireclaw_core/agent/ros_diagnostic_tools.py`
  - one atomic plugin contribution with seven Robot Agent Tools;
  - Robot Agent only, both `simulation` and `real`, `effect="read"`,
    `requires_sandbox=False`;
  - one final-argument policy hook per Tool so malformed or non-allowlisted
    names are blocked before a process starts.
- `tests/test_ros1_diagnostic_tools.py`
  - Tool projection, role isolation, no-sandbox Gateway integration, ReAct
    observation return, audit, parsing, limits, failure behavior and stall
    inference.
- `docs/architecture/ros-diagnostic-tools.md`
  - contracts, threat boundary, configuration, authority and non-goals.

#### Files modified

- `src/fireclaw_core/ros/ros1_config.py`
  - added `Ros1DiagnosticsConfig`;
  - configurable topic/frame/action allowlists and count/time/byte limits;
  - `diagnostics.enabled=false` removes the Tool family.
- `src/fireclaw_core/gateway/gateway.py`
  - constructs the backend beside the ROS1 Robot Gateway;
  - registers diagnostics even when the computer Docker sandbox is disabled;
  - continues to use the same `AgentToolRuntime` as other non-physical Tools.
- `tests/test_ros1_smoke.py`
  - added live Noetic `roscore + turtlesim` coverage for topic list, sample and
    rate.
- `pyproject.toml`
  - declared `PyYAML>=6.0` for structured ROS message parsing.
- `README.md`
  - documented the Tool family, role boundary and adapter configuration.

### Behavior now available

The Robot Agent can call:

```text
ros_topic_list
ros_topic_info
ros_topic_sample
ros_topic_rate
tf_lookup
move_base_status
navigation_diagnostics
```

The Mission Agent cannot project these Tools because every Tool declares
`roles=("robot_agent",)`.

The LLM can choose a policy-allowed topic/frame/action and a small count or
timeout. It cannot choose the executable, ROS subcommand, shell syntax,
publish payload, service request or parameter mutation.

`navigation_diagnostics` can currently report:

- missing laser, odometry, velocity command, TF or move_base status;
- terminal navigation action failure;
- an active planner producing near-zero velocity;
- a low-confidence possible stall when velocity is commanded but bounded
  odometry samples show negligible translation.

All results are advisory. They do not mutate the robot snapshot, belief store,
SafetyGate input or Mission task graph.

### Commands and results

Focused unit/integration suite:

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_ros1_diagnostic_tools.py \
  tests/test_ros1_transport.py \
  tests/test_ros1_sensor_discovery.py \
  tests/test_agent_tool_runtime.py \
  tests/test_deployment_agent_tool_integration.py \
  tests/test_gateway_robot_profile_config.py \
  tests/test_robot_agent_deliberation.py \
  tests/test_gateway_structured_task.py
```

Result: `89 passed in 9.13s`.

The first sandboxed run failed only because nine Gateway tests could not bind
local sockets (`PermissionError: Operation not permitted`). Re-running outside
the socket sandbox passed.

After making `robot_id` mandatory for enabled diagnostic backends and binding
it into every evidence envelope, the affected focused suite was rerun:
`27 passed in 1.23s`.

Live ROS1 smoke:

```text
FIRECLAW_RUN_ROS1_SMOKE=1 \
  /home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_ros1_smoke.py::test_ros1_diagnostic_tools_read_turtlesim -s
```

Result: `1 passed in 6.45s`. The test started and cleaned up a real Noetic
`roscore` and `turtlesim`, listed `/turtle1/pose`, parsed a bounded structured
sample and measured its publication rate.

Full suite:

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
```

Final result after the robot identity binding:
`1869 passed, 7 skipped in 157.65s`.

Compilation:

```text
/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q \
  src/fireclaw_core tests/test_ros1_diagnostic_tools.py
```

Result: passed with no output.

`ruff` and `black` are not installed in the current Python environment, so
those optional checks could not be run. New files were manually checked for
lines over 100 characters.

### Current conclusion

Robot Agent now has an actual ROS observation step in its ReAct loop. It is
no longer limited to accepting a navigation timeout and forwarding it. It can
collect bounded local evidence, reason over the returned observation and make
another allowed Tool or Skill decision.

This is engineering correctness, not yet a research contribution. The design
creates the controlled observation interface required for later experiments
on diagnosis quality, recovery success, token cost and unsafe proposal rate.

### Remaining work

- Run `tf_lookup`, `move_base_status` and `navigation_diagnostics` against the
  TurtleBot3 Gazebo/move_base stack, not only parsers and test doubles.
- Add namespace-aware defaults for multi-robot ROS graphs.
- Add trusted source-time/freshness evaluation; current generic sample
  freshness is based on local receipt, while raw ROS header stamps are
  preserved separately.
- Add robot-local rolling raw evidence storage and anomaly freeze.
- Add the HTTPS Mission-to-Robot diagnostic request and evidence lookup
  boundary planned above.
- Add redaction/access classes for camera, thermal, map and victim data.
- Add a separate authoritative observation-ingestion path for any diagnostic
  field that should affect snapshots, beliefs or SafetyGate.
- Design separately authorized recovery/mutation Tools such as clear costmap,
  cancel navigation or dynamic reconfigure. They must not be added to this
  read-only plugin.

### Next recommended step

Use the downloaded TurtleBot3 Gazebo and move_base stack to validate the
complete narrow-door failure flow:

```text
navigate_to_point times out
-> Robot Agent calls navigation_diagnostics
-> structured move_base/scan/odom/cmd_vel/TF evidence returns
-> Robot Agent explains the observed symptom
-> Robot Agent chooses another safe read or requests a separately authorized
   recovery action
```
