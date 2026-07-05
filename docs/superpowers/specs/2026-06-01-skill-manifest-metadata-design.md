# Skill Manifest Metadata Design

## Goal

Extend FireClaw workspace skill manifests so external algorithm skills can declare execution and robotics-safety metadata that the agent can list, log, and later enforce.

## Scope

This phase keeps execution in dry-run mode. It does not add ROS2, real hardware execution, CUDA environment management, or semantic skill selection. The change is focused on skill metadata contracts.

## Manifest Fields

Existing fields remain supported:

- `name`
- `description`
- `runtime`
- `command`
- `timeout_seconds`
- `dry_run_only`

New optional fields:

- `max_attempts`: positive integer, default `1`.
- `idempotent`: boolean, default `false`.
- `required_sensors`: list of non-empty strings, default `[]`.
- `failure_categories`: list of non-empty strings, default `[]`.
- `allow_real_robot`: boolean, default `false`.

`max_attempts > 1` is only valid when `idempotent=true`. This prevents accidental retries for unsafe robot-affecting skills.

`allow_real_robot=true` is only valid when `dry_run_only=false`. This makes the manifest's execution intent explicit.

## Data Flow

`load_subprocess_skill_from_manifest()` validates the manifest and passes metadata into `create_subprocess_skill()`. `Skill` stores the metadata. `SkillRegistry.list_metadata()` exposes the fields so CLI skill listing and future planners can inspect them.

## Testing

Add tests for:

- loading a rich manifest and exposing metadata on the `Skill`;
- rejecting retryable manifests that are not idempotent;
- rejecting malformed `required_sensors`;
- skill listing includes the metadata.

## Research Impact

This starts turning skills into explicit experimental contracts. Later ablations can compare planner behavior with and without sensor requirements, idempotency constraints, retry policies, and real-robot eligibility.
