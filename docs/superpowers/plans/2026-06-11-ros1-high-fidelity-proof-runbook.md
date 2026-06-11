# ROS1 High-Fidelity Proof Runbook

Required artifacts:

- `doctor-report.json` from the deployment environment;
- `ros1-smoke-summary.json` proving topic, service, and action transport;
- `mission-trace.json` from a real `MissionGateway`;
- `mission-events.json` from the same mission;
- `task-flow.json`;
- `session-lineage.json`;
- `memory-eval.json`;
- operator notes with secrets redacted;
- simulator or hardware environment description.

Pass criteria:

- no real hardware command is sent unless `adapter=ros1` is explicit;
- ROS1 smoke tests are skipped by default and run only with `FIRECLAW_RUN_ROS1_SMOKE=1`;
- at least one rescue command reaches a mission terminal status;
- proof bundle creation exits `0`;
- artifacts are stored outside git unless explicitly approved.
