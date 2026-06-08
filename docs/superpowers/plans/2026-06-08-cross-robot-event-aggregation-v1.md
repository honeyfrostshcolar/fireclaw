# Cross-Robot Event Aggregation v1 实现计划

**Goal:** 实现跨机器人事件聚合，将多个 robot subagent 的事件合并为 mission 级别统一时间线。

**Architecture:** Gateway 新增 `/events` 端点 → SubagentClient 新增 `get_events()` → MissionEventAggregator 聚合 → MissionAgent 集成 → CLI。

---

### Task 1: Gateway `/events` 端点 + SubagentClient `get_events()`

**Files:**
- Modify: `src/fireclaw_core/gateway.py` — 新增 `/events` 路由
- Modify: `src/fireclaw_core/subagent_client.py` — 新增 `get_events()` 方法
- Modify: `src/fireclaw_core/mission_agent.py` — SubagentClient Protocol 新增 `get_events()`
- Modify: `tests/test_gateway.py` — 测试 `/events` 端点
- Modify: `tests/test_subagent_client.py` — 测试 `get_events()`

**Gateway `/events` 端点:**
```
GET /events?task_id=<id>&limit=<N>
```
- 无 task_id: 返回 `events.latest_events(limit=limit)`
- 有 task_id: 返回 `events.events_for_task(task_id)[:limit]`
- 返回格式: `{"events": [...]}`

**SubagentClient.get_events():**
```python
def get_events(self, entry, task_id=None, limit=100):
    params = f"?limit={limit}"
    if task_id:
        params += f"&task_id={task_id}"
    return self._request_json("GET", entry.base_url, f"/events{params}")
```

### Task 2: MissionEventAggregator

**Files:**
- Create: `src/fireclaw_core/mission_event_aggregator.py`
- Create: `tests/test_mission_event_aggregator.py`

**MissionEventAggregator:**
```python
class MissionEventAggregator:
    def __init__(self, registry, subagent_client, mission_registry):
        ...

    def aggregate(self, mission_id, robot_id=None, event_type=None, limit=200):
        # 1. Get mission subtasks from registry
        # 2. For each subtask, fetch events via subagent_client.get_events()
        # 3. Merge, filter, sort by timestamp
        # 4. Return unified event list
```

### Task 3: MissionAgent 集成 + CLI

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py` — 新增 `mission_events()` 方法
- Modify: `src/fireclaw_core/mission_cli.py` — 新增 `events` 子命令
- Modify: `tests/test_mission_agent.py`
- Modify: `tests/test_mission_cli.py`

### Task 4: README 文档

---

## 验证

```bash
.venv/bin/python -m pytest -q
```
