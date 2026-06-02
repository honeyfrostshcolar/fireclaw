# CLI Runtime Context Design

## Goal

Expose FireClaw runtime context through the CLI so command-line runs can configure robot identity, available sensors, and dry-run mode explicitly.

## Scope

This phase only changes the Python CLI and documentation. It does not add config files, ROS2 adapters, real hardware execution, or interactive confirmations.

## CLI Options

Add:

- `--robot-id`: sets the dry-run robot adapter id. Default: `fireclaw-dry-run`.
- `--available-sensor`: repeatable option passed into `FireClawAgent(available_sensors=...)`.
- `--real-run`: sets `dry_run=False` for safety-gate testing. This does not create a real robot adapter; it only lets safety evaluate real-robot eligibility.

Existing options stay:

- `--memory-path`
- `--skills-dir`
- `--no-workspace-skills`

## Behavior

The CLI constructs `DryRunRobotAdapter(robot_id=args.robot_id)` and passes `available_sensors=set(args.available_sensor)` into the agent. Skill listing should still succeed without writing memory. Commands blocked by safety return nonzero exit status.

## Testing

Add CLI tests for:

- `--robot-id` appears in execution output;
- `--available-sensor` allows a workspace skill requiring that sensor;
- missing `--available-sensor` blocks that workspace skill;
- `--real-run` blocks default rescue skills because they are not real-robot allowed.

## Research Impact

This makes experiments reproducible from command lines. Sensor availability and dry-run/real-run intent become explicit independent variables instead of hidden Python-only state.
