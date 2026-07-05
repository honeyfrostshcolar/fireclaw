# Phase 8: Realtime Event Stream and Telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Status: IMPLEMENTED** (2026-06-09)

**Goal:** Let operator and main agent observe robot subagent and mission events in real time via SSE, with a unified event schema and telemetry metrics.

**Architecture:** Add a unified `StreamEvent` schema that wraps all gateway/mission/action events into one envelope. Add SSE endpoints to both gateways (`/events/stream`, `/missions/{id}/events/stream`) using Python's stdlib `http.server` chunked transfer. Add a `TelemetryTracker` that computes derived metrics (heartbeat age, task latency, action duration, cancel latency, failure reason) from the unified event stream. Extend `IncidentReplay` to optionally pull action-level events from `EventLedger` for complete timelines.

**Tech Stack:** Python stdlib (`http.server`, `json`, `threading`, `queue`), existing `EventLedger`, existing `MissionEventAggregator`.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/fireclaw_core/stream_events.py` | CREATE | Unified `StreamEvent` schema, `EventBus` pub/sub, telemetry tracker |
| `src/fireclaw_core/gateway.py` | MODIFY | Add `GET /events/stream` SSE endpoint |
| `src/fireclaw_core/mission_gateway.py` | MODIFY | Add `GET /missions/{id}/events/stream` SSE endpoint |
| `src/fireclaw_core/incident_replay.py` | MODIFY | Optionally include action-level events from EventLedger |
| `src/fireclaw_core/method_scopes.py` | MODIFY | Register new SSE endpoints with scopes |
| `tests/test_stream_events.py` | CREATE | Unit tests for StreamEvent, EventBus, TelemetryTracker |
| `tests/test_gateway.py` | MODIFY | Add SSE endpoint tests |
| `tests/test_mission_gateway.py` | MODIFY | Add SSE endpoint tests |
| `tests/test_incident_replay.py` | MODIFY | Add action-level event inclusion tests |

---

### Task 1: Unified Event Schema and EventBus

**Files:**
- Create: `src/fireclaw_core/stream_events.py`
- Create: `tests/test_stream_events.py`

- [x] **Step 1: Write failing tests for StreamEvent and EventBus**
- [x] **Step 2: Run tests to verify they fail**
- [x] **Step 3: Implement stream_events.py**
- [x] **Step 4: Run tests to verify they pass**
- [x] **Step 5: Commit**

---

### Task 2: SSE Endpoints for Both Gateways

**Files:**
- Modify: `src/fireclaw_core/gateway.py`
- Modify: `src/fireclaw_core/mission_gateway.py`
- Modify: `src/fireclaw_core/method_scopes.py`
- Modify: `tests/test_gateway.py`
- Modify: `tests/test_mission_gateway.py`

- [x] **Step 1: Register new SSE endpoints in method_scopes.py**
- [x] **Step 2: Write failing SSE tests for robot-local gateway**
- [x] **Step 3: Write failing SSE tests for mission gateway**
- [x] **Step 4: Implement SSE endpoint in gateway.py**
- [x] **Step 5: Implement SSE endpoint in mission_gateway.py**
- [x] **Step 6: Run all tests**
- [x] **Step 7: Commit**

---

### Task 3: Incident Replay Uses Unified Event Source

**Files:**
- Modify: `src/fireclaw_core/incident_replay.py`
- Modify: `tests/test_incident_replay.py`

- [x] **Step 1: Write failing test for action-level event inclusion**
- [x] **Step 2: Run test to verify it fails**
- [x] **Step 3: Modify IncidentReplay to accept optional EventLedger**
- [x] **Step 4: Run test to verify it passes**
- [x] **Step 5: Commit**

---

### Task 4: Gateway Emits to EventBus + Telemetry Integration

**Files:**
- Modify: `src/fireclaw_core/gateway.py`

- [x] **Step 1: Bridge EventLedger appends to EventBus publishes**
- [x] **Step 2: Run full test suite**
- [x] **Step 3: Commit**

---

### Task 5: Full Integration Verification

- [x] **Step 1: Run full test suite**
- [x] **Step 2: Verify SSE wire format manually**
- [x] **Step 3: Verify method_scopes coverage**

---

## Verification Evidence

- Full test suite: `566 passed` at time of implementation
- SSE wire format verified manually via curl
- Method scopes: `GET /events/stream -> state.read`, `GET /missions/{id}/events/stream -> state.read`
