# Sensor Capability Grounding Final Design

## Goal

Turn the current ROS1/Gazebo sensor discovery work into FireClaw's final sensor capability grounding layer: a safety-critical mechanism that converts robot runtime signals into verified, auditable embodied-agent capabilities.

The final design must prevent static or stale configuration from silently becoming physical authority. A firefighting robot may only execute sensor-dependent skills when the required sensor capability is currently verified and its failure policy allows the task to proceed.

## Current Baseline

Implemented baseline:

- ROS1/Gazebo topic discovery.
- Default and profile-configurable topic mapping rules.
- `discovered`, `verified`, `degraded`, and `rejected` findings.
- SafetyGate uses only `RobotState.available_sensors`, which contains verified sensors.
- Profile/runtime discovery fingerprints with `fresh`, `missing`, `stale`, and `unknown` status.
- Stale or missing fingerprint downgrades trust in `confirmed=true` profile rules.
- `robot-profile discover` can write suggested rules and a runtime fingerprint through explicit user action.

Current full-suite status after cleanup:

- `.venv/bin/python -m pytest -q` -> `1199 passed, 6 skipped`

## Final Safety Invariants

These invariants must hold across all phases:

```text
declared sensor != discovered sensor != healthy sensor != verified capability
```

```text
confirmed rule != fresh confirmation != live verification
```

```text
SafetyGate may only trust live verified capabilities, never static declarations.
```

Definitions:

- `declared`: a profile or adapter says a sensor or capability is expected.
- `discovered`: runtime found a topic, device, SDK endpoint, or simulator signal.
- `mapped`: discovery rules assign semantic meaning, such as `rgb_camera`.
- `healthy`: data stream passes sensor-specific health checks.
- `verified`: mapping is acceptable and current health checks pass.
- `confirmed`: an operator previously confirmed a semantic mapping for a fingerprinted robot context.
- `stale`: profile confirmation no longer matches the current runtime identity or cannot be trusted as fresh.

## Phase 1: Discovery Fingerprint And Stale Profile Detection

Status: completed.

Purpose:

- Detect whether profile-confirmed discovery rules still match the current ROS1 graph.
- Prevent old `confirmed=true` mappings from being treated as fresh after hardware, launch, or topic graph changes.

Delivered behavior:

- Runtime ROS1 topic/type snapshots produce deterministic SHA-256 fingerprints.
- Robot profiles may store `[robot.discovery_fingerprint]`.
- Discovery reports compare runtime and profile fingerprints.
- Stale, missing, and unknown fingerprint states downgrade profile rule confirmation diagnostics.
- SafetyGate remains ROS-agnostic.

Acceptance:

- Matching profile/runtime fingerprint reports `fresh`.
- Mismatch reports `stale`.
- Missing profile fingerprint reports `missing`.
- Runtime graph failure reports `unknown`.
- Confirmed profile rules under stale, missing, or unknown fingerprint expose stale confirmation diagnostics.

## Phase 2: Per-Sensor Health Checks

Status: next implementation phase.

Purpose:

- Replace the current coarse "recent message exists" verification with sensor-specific health checks.
- Make `verified` mean that the sensor is not only present, but currently usable for its task.

Core model:

Introduce a health layer that can evaluate a discovered sensor stream and return:

- `healthy`: sensor data currently satisfies policy.
- `degraded`: sensor exists but does not satisfy health policy.
- `stale`: no sufficiently recent data.
- `invalid`: data shape or value is not usable.
- `unknown`: backend cannot inspect enough data to decide.

Recommended model names:

- `SensorHealthPolicy`
- `SensorHealthCheck`
- `SensorHealthResult`
- `SensorHealthStatus`

Minimum first health policies:

- `rgb_camera`
  - data observed within freshness window;
  - image payload is non-empty when inspectable;
  - timestamp is fresh when available;
  - frame id is present when available.
- `thermal_camera`
  - data observed within freshness window;
  - payload is non-empty;
  - image or numeric values are not obviously invalid when inspectable;
  - stricter stale handling than RGB because it supports victim/fire assessment.
- `gas_detector`
  - recent numeric value exists;
  - value is finite;
  - value is within configured physical range.
- `lidar`
  - recent scan exists;
  - ranges are present;
  - at least one finite range is available;
  - frame id is present when available.
- `imu`
  - recent message exists;
  - orientation/angular velocity fields are present when inspectable;
  - values are finite when inspectable.

First implementation can keep ROS1 CLI probing simple and deterministic in tests. It does not need to parse every ROS message type fully from CLI text. The important architectural requirement is that the model and adapter boundary can represent richer checks.

Integration:

```text
ROS1 graph discovery
  -> mapping rule match
  -> health check for candidate sensor
  -> SensorFinding(status = verified/degraded)
  -> SensorDiscoveryReport
  -> RobotState.available_sensors
  -> SafetyGate
```

Acceptance:

- A topic with matching type but no healthy data is not verified.
- A camera with recent empty data is degraded.
- A gas detector with out-of-range or non-finite value is degraded.
- A lidar scan with no valid ranges is degraded.
- Existing freshness-only behavior remains as fallback for sensors whose payload cannot yet be inspected.

## Phase 3: Safety-Critical Sensor Policy

Status: planned after Phase 2.

Purpose:

- Make failure behavior depend on sensor criticality and task risk.
- Avoid treating all degraded sensors the same.

Policy dimensions:

- sensor name;
- skill or capability requiring it;
- robot runtime mode, such as `gazebo`, `real`, or `dry-run`;
- health status;
- task risk level;
- operator confirmation availability.

Recommended policy actions:

- `allow`: proceed.
- `warn`: proceed and surface diagnostic warning.
- `degrade`: proceed with reduced capability or lower confidence.
- `escalate`: require operator decision.
- `block`: prevent execution.

Initial criticality:

- `gas_detector`: critical for hazardous atmosphere decisions.
- `thermal_camera`: critical for victim/fire assessment.
- `lidar`: critical for navigation and obstacle avoidance.
- `rgb_camera`: important for visual search, but not always globally critical.
- `imu`: important for navigation stability, often degrade/escalate depending on motion task.

Acceptance:

- `search_for_victims` blocks when `rgb_camera` is degraded and no alternative victim-search sensor is verified.
- gas-related skills block or escalate when `gas_detector` is stale/degraded.
- navigation skills block or escalate when `lidar` is degraded in real mode.
- dry-run and simulator behavior is explicit and cannot silently apply to real mode.

## Phase 4: Operator Confirmation Loop

Status: planned after Phase 1 and Phase 2 primitives are stable.

Purpose:

- Make profile rule confirmation an auditable operator action rather than an implicit side effect.
- Support safe onboarding of a new robot, changed Gazebo world, or changed hardware configuration.

Commands:

- `robot-profile discover`
  - discovers candidates;
  - emits runtime fingerprint;
  - does not imply semantic confirmation unless `--write-profile` is explicitly used.
- `robot-profile confirm-discovery`
  - records that an operator reviewed the discovered mapping;
  - writes or updates `[robot.discovery_fingerprint]`;
  - marks selected rules `confirmed=true`;
  - records `confirmed_by` and `confirmed_at`.
- `robot-profile diff-discovery`
  - compares profile fingerprint/rules against runtime discovery;
  - shows added, removed, changed, stale, and unconfirmed mappings.

Acceptance:

- Confirmation requires explicit operator command.
- Confirmed profile rules are bound to a runtime fingerprint.
- A changed fingerprint makes previous confirmation stale.
- The confirmation event is auditable from profile and command output.

## Phase 5: Adapter-Agnostic Discovery Backend

Status: planned after ROS1 health and policy boundaries are stable.

Purpose:

- Stop hard-coding discovery as a ROS1-only concept.
- Let ROS2, simulator, dry-run, mock, and non-ROS SDK adapters produce the same `SensorDiscoveryReport` shape.

Backend interface:

```python
class SensorDiscoveryBackend:
    def discover(self) -> SensorDiscoveryReport:
        ...
```

Backend responsibilities:

- collect runtime capability signals;
- map raw signals to sensor semantics;
- compute runtime identity/fingerprint when possible;
- run health checks or report why health is unknown;
- emit diagnostics in the common report format.

Backend examples:

- `Ros1SensorDiscoveryBackend`
  - ROS1 topic/type/message inspection.
- `Ros2SensorDiscoveryBackend`
  - ROS2 node/topic/type graph and message inspection.
- `SimulatorSensorDiscoveryBackend`
  - simulator-provided sensors and synthetic stream health.
- `SdkSensorDiscoveryBackend`
  - vendor SDK capability enumeration and device health APIs.
- `StaticDeclaredDiscoveryBackend`
  - test-only or dry-run backend; never valid for real mode without explicit labeling.

Acceptance:

- SafetyGate and skills consume common verified capabilities, not backend-specific details.
- Real mode rejects static-only discovery for safety-critical sensors.
- Simulator/dry-run discovery is explicitly labeled and cannot silently masquerade as real verification.

## Cross-Phase Data Model

Final report should support:

```text
SensorDiscoveryReport
  source
  runtime_fingerprint
  profile_fingerprint
  fingerprint_comparison
  findings[]
  verified_sensors[]
```

Each finding should support:

```text
SensorFinding
  sensor
  raw_signal/topic/device
  message_type or signal_type
  mapping_source
  confirmed
  confirmation_stale
  health_status
  status
  confidence
  reason
  policy_action
```

Do not require all fields to be populated in every backend. Missing backend-specific details should be explicit, not silently guessed.

## Runtime Modes

The final system should distinguish:

- `dry-run`: testing and local development; may use declared/static sensors only when explicitly marked dry-run.
- `gazebo`: simulator runtime; may use simulator topics as verified if live health checks pass.
- `real`: physical robot runtime; must use runtime discovery and health checks for safety-critical sensors.

Real mode must not trust:

- static `available_sensors`;
- simulator-only declarations;
- stale profile confirmations;
- dry-run discovery backends.

## Testing Strategy

Each phase must include:

- unit tests for core data models;
- adapter/backend tests with deterministic fakes;
- SafetyGate policy regressions;
- CLI tests for profile discover/confirm flows;
- one integration test proving verified sensors flow into SafetyGate;
- one negative test proving stale/degraded sensors do not satisfy a required capability.

Full-suite green is required before declaring a phase complete.

## Research Impact

This final design frames FireClaw's sensor discovery as capability grounding, not convenience auto-configuration.

The research contribution is the explicit separation between:

- profile declarations;
- runtime graph discovery;
- semantic confirmation;
- health verification;
- safety policy;
- executable embodied capability.

That separation is important for firefighting robots because sensor uncertainty is not a UI problem; it is part of physical safety and experimental validity.

## Recommended Execution Order

1. Keep Phase 1 as completed baseline.
2. Implement Phase 2 next with the smallest useful health model.
3. Implement Phase 3 once health statuses are expressive enough.
4. Implement Phase 4 after confirmation metadata and health diagnostics are stable.
5. Implement Phase 5 after ROS1 boundaries stop changing.
