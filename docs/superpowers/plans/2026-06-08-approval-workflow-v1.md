# Approval Workflow v1 实现计划

**Goal:** 实现可配置风险等级的 mission 操作审批流程。

**Architecture:** `JsonlApprovalStore` 存储审批请求 → `ControlPolicy.evaluate_risk()` 评估风险 → MissionAgent 高风险操作暂停等待审批 → 操作员 approve/deny → CLI 管理。

**OpenClaw 对应:** `ExecApprovalsFile` + `exec.approvals.get/set` RPC，但 FireClaw 使用 per-operation 而非 per-agent 模式。

---

### Task 1: JsonlApprovalStore 核心实现

**Files:**
- Create: `src/fireclaw_core/approval_store.py`
- Create: `tests/test_approval_store.py`

**实现:**
- `ApprovalRequest` frozen dataclass：request_id, mission_id, action, risk_level, command, requested_by, status(pending/approved/denied/expired), decided_by, decided_at, reason, created_at
- `JsonlApprovalStore`：create, approve, deny, get, list_requests, pending_requests
- 8+ 测试

### Task 2: MissionAgent 集成

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

**实现:**
- `MissionAgent.approval_store` 参数
- `request_approval(mission_id, action, risk_level, command)` — 创建审批请求
- `decide_approval(request_id, decision, reason)` — 批准/拒绝（需 mission.approve scope）
- 4+ 测试

### Task 3: CLI 子命令 + scope

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `src/fireclaw_core/control.py`
- Modify: `tests/test_mission_cli.py`

**实现:**
- `approval list [--mission-id X] [--status pending]`
- `approval request --mission-id X --action Y --risk-level Z --command W`
- `approval decide <request_id> --decision approve|deny [--reason R]`
- `mission.approve` scope 加入 supervisor/admin

### Task 4: README 文档

---

## 验证

```bash
.venv/bin/python -m pytest -q
```
