# ROS1 Discovery Fingerprint Stale Profile Design

## Goal

Build the next step after the ROS1/Gazebo sensor discovery v1: detect when profile-confirmed sensor discovery rules may be stale because the current ROS1 graph no longer matches the graph that was reviewed when the rules were written.

This phase answers the practical question: if `robot-profile discover --write-profile` writes confirmed discovery rules today, what happens after the robot hardware, Gazebo world, ROS launch file, or topic graph changes tomorrow?

## Scope

In scope:

- ROS1/Gazebo only.
- Runtime fingerprint computation from ROS1 topic names and message types.
- Optional profile-stored discovery fingerprint.
- Runtime comparison between profile fingerprint and current ROS1 graph fingerprint.
- Stale diagnostics in sensor discovery reports and robot state.
- Conservative handling of stale confirmed rules.
- Tests proving stale profiles do not create verified sensors by declaration.

Out of scope for this phase:

- ROS2 fingerprinting.
- Full non-ROS adapter discovery.
- Operator confirmation command such as `robot-profile confirm-discovery`.
- Cryptographic signing of profile confirmations.
- Per-sensor frequency/range/frame health policies beyond the existing live message verification.

## Safety Invariant

The existing invariant remains unchanged:

```text
declared rule != discovered topic != verified sensor
SafetyGate may only use verified sensors.
```

This feature adds another distinction:

```text
confirmed rule != fresh confirmation
```

A `confirmed=true` profile rule means an operator previously confirmed the semantic mapping in some robot/profile context. It does not prove the same physical robot, launch graph, or sensor placement is still present today.

## Fingerprint Model

Add a small immutable model, tentatively named `DiscoveryFingerprint`.

Fields:

- `source`: discovery source, initially `"ros1"`.
- `topics_hash`: deterministic hash of `(topic, message_type)` pairs.
- `nodes_hash`: optional hash of ROS node names when available. First implementation may leave this unset.
- `created_at`: optional timestamp for profile records.
- `confirmed_by`: optional operator or process name for profile records.

The first implementation should compute `topics_hash` from sorted `(topic, message_type)` pairs so topic ordering cannot affect the hash.

The hash input should be stable and explicit, for example:

```text
/camera/image_raw	sensor_msgs/Image
/scan	sensor_msgs/LaserScan
```

The exact hash algorithm can be SHA-256 from the Python standard library.

## Profile Format

Profiles may include:

```toml
[robot.discovery_fingerprint]
source = "ros1"
topics_hash = "sha256:..."
created_at = "2026-06-14T12:00:00+08:00"
confirmed_by = "operator"
```

This table is optional for backward compatibility. Older profiles without the table must still load.

If a profile has sensor discovery rules but no discovery fingerprint, diagnostics should report `profile_fingerprint_missing`. That is a warning, not a load error.

## Runtime Comparison

During ROS1 sensor discovery:

1. Ask the ROS1 graph provider for current topic names and message types.
2. Build the runtime `DiscoveryFingerprint`.
3. Compare it with the profile fingerprint if one exists.
4. Attach the result to `SensorDiscoveryReport`.

Comparison states:

- `fresh`: profile fingerprint exists and matches the current runtime fingerprint.
- `missing`: profile fingerprint is absent.
- `stale`: profile fingerprint exists but does not match.
- `unknown`: runtime fingerprint could not be computed because ROS1 topic discovery failed.

Recommended diagnostics:

```json
{
  "profile_fingerprint_status": "stale",
  "profile_fingerprint_reason": "topics_hash_mismatch",
  "runtime_fingerprint": {"source": "ros1", "topics_hash": "sha256:..."},
  "profile_fingerprint": {"source": "ros1", "topics_hash": "sha256:..."}
}
```

## Rule Semantics Under Stale Fingerprints

Fingerprint state affects trust in `confirmed=true`, not live verification.

When fingerprint is `fresh`:

- Profile rules keep their original `confirmed` value.
- Verified sensors still require a live recent message.

When fingerprint is `missing`:

- Profile rules may still be used as mapping hints.
- Diagnostics must say the profile has no stored fingerprint.
- Verified sensors still require a live recent message.

When fingerprint is `stale`:

- Profile rules may still be used as mapping hints, because topic names may still help interpret runtime data.
- Their confirmation should be treated as stale in diagnostics.
- Findings created from stale profile rules should expose a `confirmation_stale` or equivalent flag.
- Verified sensors still require a live recent message.

This means a stale profile can help identify that `/front_camera/image_raw` is probably an `rgb_camera`, but cannot by itself make the camera available to SafetyGate.

## Safety-Critical Sensor Policy For This Phase

This phase does not implement full per-sensor health checks. It does establish conservative diagnostics for safety-critical sensors.

Initial safety-critical set:

- `gas_detector`
- `thermal_camera`
- `lidar`

For stale or missing fingerprints:

- Findings for these sensors should include a clear warning reason when they depend on a profile rule.
- They must not become available unless live verification succeeds.
- If live verification fails, status remains `degraded`, not `verified`.

This keeps SafetyGate behavior conservative without embedding ROS-specific fingerprint logic inside SafetyGate.

## Data Flow

```text
RobotCapabilityProfile
  -> optional DiscoveryFingerprint
  -> profile SensorMappingRule entries

ROS1 graph provider
  -> topic/type snapshot
  -> runtime DiscoveryFingerprint
  -> fingerprint comparison
  -> Ros1SensorDiscovery.discover()
  -> SensorDiscoveryReport
  -> Ros1RobotAdapter.get_robot_state()
  -> RobotState.sensor_diagnostics
  -> RobotState.available_sensors from verified sensors only
  -> SafetyGate
```

SafetyGate remains intentionally unaware of ROS topics and fingerprints.

## CLI Behavior

`robot-profile discover` should include the runtime fingerprint in generated output.

When writing to a profile with `--write-profile`, it should write or replace `[robot.discovery_fingerprint]` alongside discovery rules. The command is already explicit, so this remains a reviewed write-back path rather than silent mutation.

This phase does not add a separate confirmation command. `--write-profile` means the operator is accepting the current discovered graph as the profile's current discovery baseline.

## Error Handling

- If ROS1 topic discovery fails, return a degraded discovery report with fingerprint status `unknown`.
- If profile fingerprint source is not `"ros1"`, mark comparison as `stale` with reason `source_mismatch`.
- If `topics_hash` is missing from the profile table, treat it as `missing` or invalid-profile warning rather than crashing the runtime.
- If the profile fingerprint table has unsupported extra fields, ignore them for forward compatibility.

## Testing Strategy

Unit tests:

- Same topic/type pairs in different order produce the same `topics_hash`.
- Adding, removing, or changing a topic/type changes `topics_hash`.
- Matching profile/runtime fingerprints produce `fresh`.
- Mismatched hashes produce `stale` with reason `topics_hash_mismatch`.
- Missing profile fingerprint produces `missing`.
- Source mismatch produces `stale` with reason `source_mismatch`.

Integration tests:

- `load_robot_capability_profile()` parses optional `[robot.discovery_fingerprint]`.
- `Ros1SensorDiscovery.discover()` attaches runtime fingerprint diagnostics.
- Stale profile rules still match topics but report stale confirmation.
- A stale or missing fingerprint never puts a sensor in `available_sensors` unless live message verification succeeds.
- `robot-profile discover --write-profile` writes a parseable profile with one `[robot.discovery_fingerprint]` table.

## Research Impact

This strengthens FireClaw's capability grounding story. The research claim is not merely that the robot can auto-discover ROS topics. The stronger claim is that FireClaw separates:

- static profile declarations;
- operator-confirmed semantic mappings;
- current runtime graph identity;
- live verified capability.

That distinction is important for safety-critical embodied agents because it prevents stale configuration from silently becoming physical authority.

## Next Phase After This Spec

After fingerprint/stale detection is working, the next logical phase is per-sensor health policy:

- message frequency;
- timestamp freshness;
- frame id checks;
- numeric/image validity checks;
- frozen-value detection;
- block versus escalate policy per sensor and task risk.
