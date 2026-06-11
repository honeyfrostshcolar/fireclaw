# FireClaw Deployment Checklist

Use this checklist before deploying FireClaw to a real robot or fleet environment.

## 1. API Token Configuration

- [ ] Generate a unique API token per robot gateway
- [ ] Set `GatewayConfig.api_token` in robot config (not hardcoded in source)
- [ ] Set mission gateway token separately from robot gateway token
- [ ] Verify health endpoint (`GET /health`) bypasses auth (intended)
- [ ] Verify all other endpoints require valid Bearer token

```python
from fireclaw_core.gateway import FireClawGateway, GatewayConfig
config = GatewayConfig(adapter="dry-run", api_token="your-secret-token")
```

## 2. Operator Scopes

- [ ] Assign minimum required scopes to each operator role:
  - `observer`: `state.read` (read-only fleet/mission state)
  - `operator`: `state.read`, `task.submit`, `task.cancel`
  - `supervisor`: `state.read`, `task.submit`, `task.cancel`, `approvals.decide`
  - `admin`: `admin` (bypasses all scope checks)
- [ ] Verify `X-Operator-Scopes` header is sent by operator clients
- [ ] Confirm default-deny blocks unclassified endpoints
- [ ] Review scope matrix: `docs/security/gateway-endpoint-security-review.md`

Scope constants defined in `src/fireclaw_core/method_scopes.py`:
- `ADMIN_SCOPE = "admin"`
- `READ_SCOPE = "state.read"`
- `WRITE_SCOPE = "task.submit"`
- `APPROVALS_SCOPE = "approvals.decide"`
- `PAIRING_SCOPE = "pairing.manage"`
- `EMERGENCY_SCOPE = "emergency.stop"`

## 3. Network Binding

- [ ] Bind robot gateway to `0.0.0.0` only if remote access needed; prefer `127.0.0.1` for local-only
- [ ] Bind mission gateway to operator network interface only
- [ ] Use firewall rules to restrict gateway ports
- [ ] Do not expose gateway ports to public internet without TLS proxy

```python
GatewayConfig(adapter="ros1", host="127.0.0.1", port=18080)
MissionGatewayConfig(host="10.0.1.100", port=18090)
```

## 4. ROS Mode Separation

- [ ] Verify adapter mode matches deployment target:
  - `dry-run`: no ROS dependency, logs actions only
  - `simulator`: connects to ROS simulator (Gazebo, etc.)
  - `ros1`: connects to real ROS1 master
- [ ] Never run `dry-run` mode on a robot expecting real actuation
- [ ] Never run `ros1` mode without a running roscore
- [ ] Verify `ros1_transport.py` `enabled` flag in config matches mode

Adapter config example:
```yaml
# examples/ros1_configs/fireclaw_robot.yaml
robot_id: "firebot_01"
transport:
  enabled: true  # MUST be true for ros1 mode
  wait_for_server_seconds: 10.0
  wait_for_result_seconds: 60.0
```

## 5. ROS Smoke Test Command

Run smoke tests to verify ROS1 integration before deployment:

```bash
# Default test suite skips ROS1 smoke tests.
.venv/bin/python -m pytest -q

# Explicit ROS1 smoke proof.
# Requires ROS1 commands on PATH: roscore, rosrun, turtlesim, actionlib_tutorials.
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q

# Specific test:
FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py::test_ros1_action_fibonacci_goal -q
```

Expected: all 6 tests pass (infrastructure, topic, service, action, cancel, timeout).

## 6. Log Redaction

- [ ] Verify `redact_secrets()` is applied to all log outputs
- [ ] Patterns redacted: `sk-*`, `Bearer *`, `api_key=*`, `password=*`, `token=*`
- [ ] LLM trace store uses `redact_all()` before persisting
- [ ] No raw API keys in gateway request/response logs

```python
from fireclaw_core.log_redaction import redact_secrets, redact_dict
safe_text = redact_secrets(raw_log_text)
safe_data = redact_dict(raw_dict_data)
```

## 7. Queue Compaction / Retention

- [ ] Configure task queue compaction: `JsonlTaskQueue.compact(keep_terminal=100)`
- [ ] Schedule periodic compaction (e.g., every 6 hours or per N tasks)
- [ ] Verify terminal records (succeeded/failed/cancelled) are retained per policy
- [ ] Verify non-terminal records (queued/running) are always kept
- [ ] LLM trace retention: rotate or compact trace files periodically

## 8. Memory Index Storage

- [ ] Choose index storage path with adequate disk space
- [ ] SQLite FTS index is created on first use; no migration needed
- [ ] If no index path configured, system falls back to JSONL keyword search
- [ ] Index rebuild: `SqliteMemoryIndex.rebuild(records)` from JSONL store
- [ ] Verify index path is writable by gateway process

```python
from fireclaw_core.memory_index import SqliteMemoryIndex
index = SqliteMemoryIndex("/var/lib/fireclaw/memory_index.db")
```

## 9. Memory Indexing and Evaluation CLI

Use the memory CLI to index mission memory JSONL into SQLite FTS5 and evaluate retrieval quality.

### Index memory records

```bash
.venv/bin/python -m fireclaw_core.memory_cli index \
  --memory-path /var/lib/fireclaw/mission_memory.jsonl \
  --index-path /var/lib/fireclaw/memory_index.sqlite
```

Expected output: `{"status": "indexed", "record_count": N, "index_path": "..."}`

### Evaluate retrieval quality

```bash
.venv/bin/python -m fireclaw_core.memory_cli eval \
  --index-path /var/lib/fireclaw/memory_index.sqlite \
  --fixture tests/fixtures/memory_eval_cases.json \
  --threshold 0.8 \
  --limit 5
```

- `--threshold`: minimum hit_rate to pass (default: 1.0)
- `--limit`: max results per query (default: 5)
- Exit code 0: threshold met; exit code 2: below threshold

### Deployment gate

- [ ] Run `memory_cli index` after initial memory data import
- [ ] Run `memory_cli eval` with a fixture covering key queries
- [ ] Verify hit_rate meets operational threshold before deploying memory-dependent features

## 10. Embodied Scenario Evaluation

Run the scenario-level evaluation harness to verify the full mission/gateway
chain before deploying to a real robot or presenting demo results.

### Run eval harness

```bash
.venv/bin/python -m fireclaw_core.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/local-sim \
  --adapter simulator
```

- `--adapter`: `dry-run`, `simulator`, or `ros1` (default: `simulator`)
- `--poll-timeout`: seconds per scenario to wait for terminal status (default: 15)

Exit codes: 0 = all pass, 2 = warnings, 1 = error.

### Output files

- `summary.json`: overall status, scenario count, aggregated metrics
- `scenarios.jsonl`: one JSON object per scenario with per-scenario metrics

### Metrics reported

| Metric | Meaning |
|--------|---------|
| `plan_success_rate` | Fraction of scenarios that produced a plan |
| `dispatch_success_rate` | Fraction that dispatched subtasks to robots |
| `terminal_event_rate` | Fraction that reached a terminal mission status |
| `memory_record_rate` | Fraction that produced at least one memory record |
| `average_latency_ms` | Mean end-to-end mission completion time |

### Deployment gate

- [ ] Run eval harness with simulator adapter before demo/paper experiments
- [ ] Verify `plan_success_rate >= 0.5` (planner resolves commands correctly)
- [ ] Verify `terminal_event_rate` matches expectations for scenario set
- [ ] Run with `--adapter ros1` on target robot for real-hardware validation

## 11. Emergency Stop Verification

- [ ] Test emergency stop endpoint: `POST /tasks/{id}/emergency-stop`
- [ ] Verify emergency stop propagates to ROS layer (if using ros1 adapter)
- [ ] Verify emergency stop bypasses normal authorization (EMERGENCY_SCOPE)
- [ ] Verify robot enters safe state (stop motion, hold position)
- [ ] Log emergency stop events with full context (reason, timestamp, robot state)
- [ ] Test emergency stop from both robot-local gateway and mission gateway

```bash
# Test emergency stop via curl
curl -X POST http://localhost:18080/tasks/{task_id}/emergency-stop \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Operator-Scopes: emergency.stop" \
  -H "Content-Type: application/json" \
  -d '{"reason": "pre-deployment safety test"}'
```

## 12. Pre-Deployment Verification

- [ ] Full test suite passes: `.venv/bin/python -m pytest -q` (ROS1 smoke skipped by default)
- [ ] ROS smoke tests pass (if deploying with ROS): `FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q`
- [ ] Gateway starts without errors on target machine
- [ ] Robot adapter connects to ROS master (if applicable)
- [ ] Mission gateway reachable from operator console
- [ ] SSE event stream delivers real-time events: `curl -N http://localhost:18080/events/stream`
- [ ] Fleet doctor reports no critical issues: `GET /fleet/doctor`
