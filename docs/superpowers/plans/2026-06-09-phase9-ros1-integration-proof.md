# Phase 9: ROS1 Integration Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Status: IMPLEMENTED** (2026-06-09, local smoke proof)

**Goal:** Prove FireClaw's ROS1 transport works against a real ROS1 master, add config examples, define ROS2 protocol boundary, enhance message introspection, and write deployment docs.

**Architecture:** Session-scoped pytest fixtures manage roscore/turtlesim/fibonacci_server lifecycles. Smoke tests are gated by `@pytest.mark.ros1_smoke` and skipped by default. ROS2 adapter is protocol-only (no implementation).

**Tech Stack:** Python, pytest, rospy, actionlib, turtlesim (ROS1 Noetic), actionlib_tutorials

---

## File Structure

```
tests/test_ros1_smoke.py          — NEW: all ROS1 smoke tests + fixtures + helpers
tests/test_ros2_adapter.py        — NEW: ROS2 protocol tests
tests/test_ros1_transport.py      — MODIFY: add __slots__ + error message tests
src/fireclaw_core/ros1_transport.py — MODIFY: enhance _resolve_ros_type, _response_to_data, add validate_payload_against_type, add _build_ros_message
src/fireclaw_core/ros2_adapter.py   — NEW: Ros2AdapterProtocol
examples/ros1_configs/turtlesim_teleop.yaml — NEW
examples/ros1_configs/fibonacci_action.yaml — NEW
examples/ros1_configs/fireclaw_robot.yaml   — NEW
docs/deployment/ros1-deployment-guide.md    — NEW
pyproject.toml                    — MODIFY: register ros1_smoke marker
```

---

### Task 1: Register pytest marker and create smoke test infrastructure

**Files:**
- Modify: `pyproject.toml`
- Create: `tests/test_ros1_smoke.py`

- [x] **Step 1: Register the `ros1_smoke` marker in pyproject.toml**
- [x] **Step 2: Create smoke test file with helpers and fixtures**
- [x] **Step 3: Verify the marker is registered and file parses**
- [x] **Step 4: Commit**

---

### Task 2: ROS1 Topic Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [x] **Step 1: Add topic smoke test**
- [x] **Step 2: Run topic smoke test**
- [x] **Step 3: Commit**

---

### Task 3: ROS1 Service Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [x] **Step 1: Add service smoke test**
- [x] **Step 2: Run service smoke test**
- [x] **Step 3: Commit**

---

### Task 4: ROS1 Action Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [x] **Step 1: Add action smoke test**
- [x] **Step 2: Run action smoke test**
- [x] **Step 3: Commit**

---

### Task 5: ROS1 Cancel/Timeout Smoke Test

**Files:**
- Modify: `tests/test_ros1_smoke.py`

- [x] **Step 1: Add cancel smoke test**
- [x] **Step 2: Run cancel and timeout smoke tests**
- [x] **Step 3: Commit**

---

### Task 6: YAML Config Examples

**Files:**
- Create: `examples/ros1_configs/turtlesim_teleop.yaml`
- Create: `examples/ros1_configs/fibonacci_action.yaml`
- Create: `examples/ros1_configs/fireclaw_robot.yaml`

- [x] **Step 1: Create turtlesim config**
- [x] **Step 2: Create fibonacci config**
- [x] **Step 3: Create fireclaw robot config**
- [x] **Step 4: Verify configs parse correctly**
- [x] **Step 5: Commit**

---

### Task 7: ROS2 Adapter Protocol

**Files:**
- Create: `src/fireclaw_core/ros2_adapter.py`
- Create: `tests/test_ros2_adapter.py`

- [x] **Step 1: Create ROS2 adapter protocol**
- [x] **Step 2: Create ROS2 protocol tests**
- [x] **Step 3: Run ROS2 protocol tests**
- [x] **Step 4: Commit**

---

### Task 8: Message Introspection Enhancement

**Files:**
- Modify: `src/fireclaw_core/ros1_transport.py`
- Modify: `tests/test_ros1_transport.py`

- [x] **Step 1: Enhance _resolve_ros_type with better error messages**
- [x] **Step 2: Enhance _response_to_data with __slots__ support**
- [x] **Step 3: Add validate_payload_against_type function**
- [x] **Step 4: Add tests for enhanced error messages and __slots__**
- [x] **Step 5: Run all tests**
- [x] **Step 6: Commit**

---

### Task 9: Deployment Docs

**Files:**
- Create: `docs/deployment/ros1-deployment-guide.md`

- [x] **Step 1: Create deployment guide**
- [x] **Step 2: Commit**

---

### Task 10: Full Verification

- [x] **Step 1: Run all unit tests (exclude smoke)** — 575 passed
- [x] **Step 2: Run smoke tests (requires ROS1 environment)** — 6/6 passed
- [x] **Step 3: Run ROS2 protocol tests** — 3 passed
- [x] **Step 4: Verify YAML configs parse** — 3/3 OK

---

## Verification Evidence

- Unit tests (exclude smoke): `575 passed`
- ROS1 smoke tests: `6 passed` (infrastructure, topic, service, action, cancel, timeout)
- ROS2 protocol tests: `3 passed`
- YAML configs: all 3 parse correctly
- Action result timeout returns `status="timeout"` (not `status="failed"`)
- Dict-to-message conversion: `_build_ros_message` supports recursive construction

## Known Limitations

- Smoke tests require a running ROS1 environment (roscore + turtlesim + actionlib_tutorials)
- ROS2 adapter is protocol-only; no rclpy implementation
- Real robot hardware proof remains future work (current proof is local ROS1 master + tutorials)
