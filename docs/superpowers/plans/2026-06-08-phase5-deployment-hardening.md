# Phase 5: Deployment Hardening v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add authentication, robot enrollment, heartbeat expiration, queue compaction, and log redaction to prepare FireClaw for real deployments.

**Architecture:** Gateway token auth via Bearer header, one-time pairing code enrollment, heartbeat timeout auto-offline, JSONL queue compaction, and regex-based sensitive field redaction.

**Tech Stack:** Python 3.10+, existing httpx/threading patterns

---

## Task 1: Gateway API Token Authentication

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `src/fireclaw_core/subagent_client.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_subagent_client.py`

**Steps:**
1. Add `api_token: str | None = None` to `GatewayConfig`
2. In `Gateway` request handler, check `Authorization: Bearer <token>` header when `api_token` is set
3. Return 401 `{"error": "Unauthorized"}` if token missing or mismatched
4. Skip auth for health check endpoint (`GET /`)
5. Add `api_token` to `RobotSubagentClient.__init__` and send in all requests
6. Tests: verify 401 without token, 200 with correct token, skip on health check
7. Commit

---

## Task 2: Robot Pairing/Enrollment

**Files:**
- Create: `src/fireclaw_core/robot_enrollment.py`
- Modify: `src/fireclaw_core/robot_registry.py`
- Create: `tests/test_robot_enrollment.py`
- Modify: `tests/test_robot_registry.py`

**Steps:**
1. Create `EnrollmentRequest` dataclass: `robot_id, pairing_code, created_at, expires_at, status`
2. Create `JsonlEnrollmentStore` with `create(code)`, `approve(code, robot_id)`, `reject(code)`, `list_pending()`
3. Pairing codes: 6-char alphanumeric, one-time use, 5-minute expiry
4. Add `enrollment_path` to `RobotRegistry` and `enroll(robot_id, pairing_code)` method
5. CLI: add `robot-enroll` subcommand
6. Tests: create/approve/reject/expiry, duplicate code rejection
7. Commit

---

## Task 3: Heartbeat Expiration + Degraded Policy

**Files:**
- Modify: `src/fireclaw_core/robot_registry.py`
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_robot_registry.py`
- Modify: `tests/test_mission_agent.py`

**Steps:**
1. Add `heartbeat_timeout_seconds: float = 30.0` to `RobotRegistry`
2. Add `is_stale(entry)` method: `True` if `last_seen_at` older than timeout
3. Modify `enabled_entries()`: exclude stale entries by default, add `include_stale=False` param
4. Add `stale_entries()` method for monitoring
5. `check_fleet_presence()` returns `"stale"` status for timed-out robots
6. `plan_and_submit()` skips stale robots in planning
7. Tests: stale detection, stale exclusion from planning
8. Commit

---

## Task 4: Queue Compaction + Log Redaction

**Files:**
- Modify: `src/fireclaw_core/task_queue.py` (or create helper)
- Modify: `src/fireclaw_core/llm_trace.py`
- Create: `src/fireclaw_core/log_redaction.py`
- Create: `tests/test_log_redaction.py`

**Steps:**
1. Create `redact_secrets(text: str) -> str`: regex replace `sk-*`, `Bearer *`, `api_key=*`, `password=*` with `***`
2. Create `redact_dict(data: dict) -> dict`: recursively redact string values in dicts
3. Add `compact(keep_terminal: int = 100)` to `JsonlTaskQueue`: keep only last N terminal records
4. Add `redact()` to `LLMTraceStore`: redact all message content and response content
5. Gateway startup calls `task_queue.compact()` if queue path configured
6. Tests: redact patterns, compact behavior, redact dict
7. Commit
