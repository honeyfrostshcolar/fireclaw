# Incident Replay v1 实现计划

**Goal:** 从持久化数据重建 mission 时间线，用于事后分析。

**Architecture:** `IncidentReplay` 读取 `JsonlMissionRegistry` + `MissionMemoryStore`，合并为统一时间线。

---

### Task 1: IncidentReplay 核心实现

**Files:**
- Create: `src/fireclaw_core/incident_replay.py`
- Create: `tests/test_incident_replay.py`

**实现:**
- `IncidentReplay` 类接受 `mission_registry` 和 `mission_memory`
- `replay(mission_id)` 返回：
  - mission 基本信息
  - timeline（按时间排序的事件列表）
  - summary（subtask 计数、memory 计数、duration）
- timeline 事件来源：
  - mission subtask 状态变化（从 mission_trace）
  - memory records（outcome/observation/correction/lesson）
- 6+ 测试

### Task 2: MissionAgent 集成 + CLI

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_cli.py`

**实现:**
- `MissionAgent.replay_incident(mission_id)` — 委托给 IncidentReplay
- CLI: `replay <mission_id> [--memory-path PATH]`

### Task 3: README 文档

---

## 验证

```bash
.venv/bin/python -m pytest -q
```
