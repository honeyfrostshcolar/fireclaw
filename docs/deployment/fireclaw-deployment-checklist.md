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

For the full ROS1 high-fidelity proof gate criteria and required artifacts, see `docs/superpowers/plans/2026-06-11-ros1-high-fidelity-proof-runbook.md`.

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

## 10. Deterministic Integration Evaluation

Run the deterministic integration lane to verify the Mission/Robot Gateway
contract. This lane deliberately excludes LLM quality and ROS/Gazebo system
performance.

### Run eval harness

```bash
.venv/bin/python -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/<unique-run-id> \
  --adapter simulator
```

- `--output-dir` must be new or empty; an existing run is never overwritten.
- `--run-id` optionally fixes the run identity; otherwise one is generated.
- `--adapter`: `dry-run` or `simulator` (default: `simulator`). `ros1` and
  `--ros1-config` are rejected and belong to the separate system lane.
- `--poll-timeout`: seconds per scenario to wait for terminal status (default: 15)

Exit codes: 0 = all pass, 2 = warnings, 1 = error.

### Output files

- `run-manifest.json`: lane, run identity, selection/retention rule, suite hash
- `scenario-suite.json`: normalized point/area/entity cases, seeds, repeats, split
- `scenarios.jsonl`: every attempted case, including failures and runner errors
- `summary.json`, `metric-definitions.json`, `paper-summary.json`: exact metrics,
  denominators, sample standard deviations, confidence intervals, and outcomes
- `plugin-inventory.json`, `tool-inventory.json`, `provenance.json`: versions,
  source digests, Tool schemas, Git commit/dirty hashes, Python/platform details
- `cases/<case-id>/`: Mission Run, trace, events, Robot task traces, memory,
  final report, and structured case record
- `artifact-manifest.json`: size and SHA-256 for every artifact in the bundle

### Metrics reported

| Metric | Meaning |
|--------|---------|
| `contract_pass_rate` | Fraction matching the declared scenario contract |
| `task_success_rate` | Fraction with canonical `completed` and successful Robot subtasks |
| `plan_success_rate` | Fraction of scenarios that produced a plan |
| `dispatch_success_rate` | Fraction whose Robot subtasks all completed |
| `terminal_event_rate` | Fraction with a canonical Mission Run terminal outcome |
| `final_report_rate` | Fraction with a persisted Mission final report |
| `memory_record_rate` | Fraction that produced at least one memory record |
| `average_latency_ms` | Mean end-to-end mission completion time |

### Deployment gate

- [ ] Run eval harness with simulator adapter before demo/paper experiments
- [ ] Verify `contract_pass_rate == 1.0` for the deterministic regression suite
- [ ] Inspect `outcome_counts`, `errors.jsonl`, and missing-data fields; do not
  select only successful cases
- [ ] Verify Plugin/Tool inventory hashes are stable within the run
- [ ] Use the dedicated ROS/Gazebo system lane for physical validation

## 10.1 Offline LLM Planning Evaluation

Run this lane against immutable point/area/entity fixtures. It uses the
production Mission planner and deterministic graph compiler but has no
Gateway, scheduler, Robot Adapter, ROS, or physical dispatch surface.

```bash
export FIRECLAW_PROVIDER_API_KEY='<secret>'
.venv/bin/python -m fireclaw_core.devtools.llm_planning_eval \
  --scenarios tests/fixtures/embodied_eval/planning_scenarios.json \
  --output-dir results/embodied-eval/<unique-llm-run-id> \
  --provider-base-url https://<provider>/v1 \
  --provider-name <provider-name> \
  --model <model-id> \
  --temperature 0 \
  --model-catalog <optional-model-catalog.json>
```

- The API key is read from `--api-key-env` (default:
  `FIRECLAW_PROVIDER_API_KEY`) and is never stored.
- Every provider request records the scenario seed. Seed support is forwarded,
  not assumed to guarantee deterministic provider output.
- A model catalog supplies optional input/output USD prices per million tokens.
- `unsafe_proposal_proxy_rate` counts deterministic runtime rejections; it is
  not an independently annotated unsafe-plan metric.
- Exit codes are 0 = all contracts pass, 2 = retained warnings/failures, and
  1 = configuration error.

### Planning evaluation gate

- [ ] Use a new output directory and a clean Git commit for paper runs
- [ ] Freeze provider name, requested model, actual response model,
  temperature, seeds, Tool inventory hash, and fixture hash
- [ ] Confirm `no_dispatch_rate == 1.0` and inspect every retained failure
- [ ] Confirm area cases contain `inspect_state` before accepted proposal
- [ ] Use repeated seeds and a held-out test split; the included development
  fixture is not a paper benchmark
- [ ] Add an independent annotation protocol before claiming an unsafe-plan
  rate

## 10.2 ROS/Gazebo System Evaluation

The trusted Gazebo acceptance runner now invokes the system evaluator after
pytest and writes an immutable common-schema bundle to
`results/gazebo-acceptance/<run-id>/evaluation/`. Select one of the six fixed
scenario YAML files through `FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO`; the default
is `success.yaml`.

```bash
FIRECLAW_PYTHON=.venv/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development \
FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX=0 \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

Existing source proofs can be collected without restarting ROS/Gazebo. Repeat
`--source-proof` and supply aligned `--repeat-index` values when combining
cases or repetitions:

```bash
.venv/bin/python -m fireclaw_core.devtools.ros_gazebo_system_eval \
  --source-proof results/gazebo-acceptance/<run-id> \
  --repeat-index 0 \
  --split development \
  --output-dir results/embodied-eval/<unique-system-run-id>
```

### System evaluation gate

- [ ] Keep `contract_pass_rate` separate from `task_success_rate`; an expected
  cancel, timeout, failure, or escalation is not a completed task
- [ ] Verify scheduler evidence, same-task resume, canonical terminal/report,
  Plugin owner/backend, and zero Adapter fallback calls
- [ ] Inspect conditional safe-stop, diagnostics, recovery, and escalation
  numerators and denominators
- [ ] Verify maps, worlds, configs, source proofs, Plugin manifests, Tool
  schemas, system versions, and navigation parameters are content-addressed
- [ ] Build and load `fireclaw_gazebo_contact_monitor`; require its publisher
  before the first goal and through terminal stop
- [ ] Verify the raw contact stream, Robot scope, fixed support-contact filter,
  episode counts, source asset, and loaded shared-library SHA-256
- [ ] Treat missing, late, disconnected, truncated, or inconsistent collision
  instrumentation as missing data, never as zero collisions
- [ ] For paper runs, use a clean commit, frozen `validation`/`test` split,
  explicit collision evidence, unique output directory, and repeated trials
- [ ] Do not use `--reference-only` for an archival paper bundle

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

## 12. Legacy Embodied Experiment Proof Repackager

New deterministic evaluation runs already emit a complete content-addressed
bundle and should not need this extra step. The command below remains for
repackaging older or externally collected artifacts.

### Create proof bundle

```bash
.venv/bin/python -m fireclaw_core.embodied_proof_bundle \
  --output-dir results/proof-bundles/local-sim-001 \
  --run-id local-sim-001 \
  --mission-trace results/mission-trace.json \
  --mission-events results/mission-events.json \
  --task-flow results/task-flow.json \
  --session-lineage results/session-lineage.json \
  --memory-eval results/memory-eval.json \
  --doctor-report results/doctor-report.json \
  --notes "Simulated rescue scenario, 3 robots"
```

All flags except `--output-dir` and `--run-id` are optional. Each takes a path to a JSON file.

### Bundle contents

| File | Always present | Description |
|------|---------------|-------------|
| `summary.json` | yes | Redacted bundle metadata, run_id, presence flags |
| `mission-trace.json` | if provided | Mission trace (mission_id, status, subtasks) |
| `mission-events.json` | if provided | Mission event replay |
| `task-flow.json` | if provided | Task-flow graph |
| `session-lineage.json` | if provided | Session lineage |
| `memory-eval.json` | if provided | Memory retrieval evaluation results |
| `doctor-report.json` | if provided | Fleet doctor check results |
| `README.md` | yes | Human-readable bundle manifest |

All dict payloads are redacted via `redact_dict()` before writing (sk-*, Bearer, api_key, password, token patterns).

### Deployment gate

- [ ] Create proof bundle after each successful experiment run
- [ ] Include `--doctor-report` to capture fleet health at experiment time
- [ ] Include `--memory-eval` to document retrieval quality baseline
- [ ] Archive bundle with paper submission or demo artifacts

## 13. Pre-Deployment Verification

- [ ] Full test suite passes: `.venv/bin/python -m pytest -q` (ROS1 smoke skipped by default)
- [ ] ROS smoke tests pass (if deploying with ROS): `FIRECLAW_RUN_ROS1_SMOKE=1 .venv/bin/python -m pytest tests/test_ros1_smoke.py -q`
- [ ] Gateway starts without errors on target machine
- [ ] Robot adapter connects to ROS master (if applicable)
- [ ] Mission gateway reachable from operator console
- [ ] SSE event stream delivers real-time events: `curl -N http://localhost:18080/events/stream`
- [ ] Fleet doctor reports no critical issues: `GET /fleet/doctor`
