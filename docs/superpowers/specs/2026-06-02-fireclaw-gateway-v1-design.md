# FireClaw Gateway v1 Design

## Goal

Add a local-first HTTP control plane for FireClaw so a robot can run one resident agent service and receive natural-language tasks over HTTP.

The direction is:

```text
HTTP in -> FireClawAgent -> SkillExecutor -> RobotAdapter -> simulator / mock ROS2 / future real ROS2
```

## Scope

- Add a dependency-free local HTTP server using Python standard library.
- Keep existing CLI behavior unchanged.
- Reuse existing `FireClawAgent`, memory, skills, safety, confirmation, and adapter logic.
- Add shared adapter factory so CLI and Gateway use the same adapter selection.
- Support a single resident robot adapter per Gateway process.
- Support per-request `session_id` by constructing a request-scoped `FireClawAgent` over the shared robot and memory.

## Non-Goals

- No real ROS2 implementation yet.
- No frontend UI.
- No terminal REPL.
- No authentication yet.
- No multi-robot fleet controller yet.
- No streaming events yet.

## HTTP API

### `GET /health`

Returns service health and robot identity.

### `GET /state`

Returns `robot_state` and `environment_state`.

### `GET /skills`

Returns registered skill metadata.

### `GET /memory/recent?session_id=<id>&limit=<n>`

Returns recent JSONL memory records.

### `POST /tasks`

Body:

```json
{
  "command": "去二楼救人",
  "session_id": "operator-a"
}
```

Runs `FireClawAgent.run(command)`.

### `POST /confirm`

Body:

```json
{
  "session_id": "operator-a"
}
```

Runs `FireClawAgent.run("确认执行")`.

### `POST /cancel`

Body:

```json
{
  "session_id": "operator-a"
}
```

Runs `FireClawAgent.run("取消")`.

## Safety

Gateway v1 binds to `127.0.0.1` by default.

This is not safe for public exposure. Later versions should add:

- auth tokens;
- operator identity;
- request audit ids;
- TLS / reverse proxy guidance;
- remote exposure runbook.

## Verification

- Unit/integration tests start Gateway on port `0`.
- Tests cover health, state, skills, task execution, memory recent, and confirmation flow.
- CLI tests ensure existing command-line behavior still works.
