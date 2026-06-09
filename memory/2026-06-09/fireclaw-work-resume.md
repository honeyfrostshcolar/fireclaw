# FireClaw Work Resume

## 2026-06-09 10:30 CST

### Task Goal

Continue FireClaw development from Phase 4 (Model Provider Runtime) through Phase 5 (Deployment Hardening).

### Current Git State

- Branch: `master`, ahead of `origin/master` by ~30 commits
- Latest commit: `6c25bd7 docs: update alignment doc with Phase 5 partial completion`
- Verification: `469 passed` in full test suite

### Phase 4: Model Provider Runtime v1 — COMPLETED

Implemented via subagent-driven-development with two-stage review per task.

**Components:**
- `src/fireclaw_core/provider.py` — `ModelProvider` protocol, `OpenAICompatProvider` (httpx-based), `ChatCompletion`/`ToolCall`/`TokenUsage` data types, error hierarchy (`ProviderError` → `ProviderTimeoutError`/`ProviderAPIError` → `ProviderAuthError`)
- `src/fireclaw_core/model_catalog.py` — `ModelDescriptor` frozen dataclass, `ModelCatalog` (JSON config loading, resolve by id, default_model_id)
- `src/fireclaw_core/llm_planner.py` — `LLMMissionPlanner` with tool calling (`MISSION_PLAN_TOOL` schema), system prompt builder, robot_id validation, trace recording
- `src/fireclaw_core/llm_trace.py` — `LLMTraceRecord` frozen dataclass, `LLMTraceStore` (JSONL append-only, corrupt line resilience)

**CLI integration:**
```bash
python -m fireclaw_core.mission_cli plan-mission \
  --command "去二楼搜索受困人员" \
  --planner llm \
  --provider-base-url https://api.deepseek.com \
  --provider-api-key sk-xxx \
  --model deepseek-chat \
  --llm-trace-path logs/llm-traces.jsonl
```

**Tests:** +34 tests (388 → 422)

### Phase 5: Deployment Hardening — PARTIAL COMPLETED (4/6 tasks)

**Task 1: Gateway API Token Authentication — COMPLETED**
- `GatewayConfig.api_token: str | None` — Bearer token auth on all endpoints
- `RobotSubagentClient.api_token` — sends token in requests
- Health check (`GET /health`) bypasses auth
- +4 tests

**Task 2: Robot Pairing/Enrollment — COMPLETED**
- `src/fireclaw_core/robot_enrollment.py` — `EnrollmentRequest`, `JsonlEnrollmentStore`
- 6-char uppercase alphanumeric pairing codes, one-time use, 5-min expiry
- Statuses: pending, approved, rejected, expired
- `cleanup_expired()` marks expired requests
- +22 tests

**Task 3: Heartbeat Expiration + Degraded Policy — COMPLETED**
- `RobotRegistry.heartbeat_timeout_seconds` (default -1.0 = disabled)
- `is_stale(entry)` — True if last_seen_at older than timeout
- `enabled_entries(include_stale=False)` — excludes stale by default
- `check_fleet_presence()` marks stale robots
- `plan_and_submit()` auto-excludes stale robots
- +10 tests

**Task 4: Queue Compaction + Log Redaction — COMPLETED**
- `src/fireclaw_core/log_redaction.py` — `redact_secrets(text)`, `redact_dict(data)`
- Patterns: `sk-*`, `Bearer *`, `api_key=*`, `password=*`, `token=*`
- `JsonlTaskQueue.compact(keep_terminal=100)` — keeps last N terminal records
- `LLMTraceStore.redact_all()` — redacts all trace content
- +12 tests

**Remaining Phase 5 tasks:**
- deployment config examples
- security review for robot control endpoints

**Tests:** +47 tests (422 → 469)

### Current Conclusion

FireClaw now has:
- Full LLM provider runtime with OpenAI-compatible API
- Tool calling for structured mission plan output
- Replayable LLM traces
- Gateway token authentication
- Robot enrollment with pairing codes
- Heartbeat-based stale robot exclusion
- Queue compaction and log redaction

The framework is significantly closer to deployment readiness. The remaining Phase 5 tasks (deployment config examples, security review) are lower priority for the research phase.

### Next Recommended Steps

1. **Real LLM testing** — Test `LLMMissionPlanner` with actual DeepSeek/Qwen API calls
2. **Deployment config examples** — Example configs for common deployment scenarios
3. **Security review** — Audit robot control endpoints for vulnerabilities
4. **ROS2 adapter** — Upgrade from mock to real ROS2 integration

### Key Design Decisions Made

1. **OpenAI-compatible API only** — Covers DeepSeek, Qwen, GLM, Moonshot, Ollama; no need for provider plugin architecture yet
2. **LLM fully replaces deterministic planner** — User choice; no fallback to regex planner
3. **No fallback when LLM unavailable** — Direct error; system depends on LLM availability
4. **Tool calling for structured output** — More reliable than free-text parsing
5. **Heartbeat disabled by default** — `heartbeat_timeout_seconds=-1.0` sentinel; only active when explicitly configured
