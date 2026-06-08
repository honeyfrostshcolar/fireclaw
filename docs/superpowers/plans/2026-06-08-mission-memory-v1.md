# Mission Memory Records v1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现 mission 级别的结构化记忆系统，记录任务结果、环境观察、操作员纠正和经验教训，支持 Python API、MissionAgent 自动记录和 CLI 子命令。

**Architecture:** 独立 `MissionMemoryStore`（JSONL），四种记录类型（outcome/observation/correction/lesson），与 robot-local `JsonlMemoryStore` 分离。MissionAgent 在 mission 完成/失败时自动写入 outcome，其他类型通过 API/CLI 手动写入。

**Tech Stack:** Python, JSONL, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/fireclaw_core/mission_memory.py` | Create | MissionMemoryStore, 记录类型定义 |
| `tests/test_mission_memory.py` | Create | MissionMemoryStore 单元测试 |
| `src/fireclaw_core/mission_agent.py` | Modify | 集成 mission memory 自动记录 |
| `tests/test_mission_agent.py` | Modify | MissionAgent memory 集成测试 |
| `src/fireclaw_core/mission_cli.py` | Modify | 增加 `memory` CLI 子命令 |
| `tests/test_mission_cli.py` | Modify | CLI memory 测试 |
| `README.md` | Modify | 文档更新 |

---

### Task 1: MissionMemoryStore 核心实现

**Files:**
- Create: `src/fireclaw_core/mission_memory.py`
- Create: `tests/test_mission_memory.py`

**Step 1: 定义记录类型和 MissionMemoryStore**

```python
# src/fireclaw_core/mission_memory.py
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MEMORY_RECORD_TYPES = {"outcome", "observation", "correction", "lesson"}


@dataclass(frozen=True)
class MissionMemoryRecord:
    record_id: str
    mission_id: str
    record_type: str  # outcome | observation | correction | lesson
    content: dict[str, Any]
    robot_id: str | None = None
    subtask_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MissionMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: MissionMemoryRecord) -> None:
        if record.record_type not in MEMORY_RECORD_TYPES:
            raise ValueError(f"Invalid record type: {record.record_type}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    def list_records(self, *, mission_id: str | None = None, record_type: str | None = None) -> list[MissionMemoryRecord]:
        ...

    def search(self, *, mission_id: str | None = None, record_type: str | None = None,
               robot_id: str | None = None, keyword: str | None = None,
               limit: int = 10) -> list[MissionMemoryRecord]:
        ...

    def summary(self, mission_id: str | None = None) -> dict[str, Any]:
        ...
```

**Step 2: 写失败测试**

```python
# tests/test_mission_memory.py
def test_mission_memory_store_appends_and_lists(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    record = MissionMemoryRecord(
        record_id="mem-1", mission_id="m-1", record_type="outcome",
        content={"status": "succeeded", "duration_seconds": 120},
        created_at="2026-06-08T12:00:00Z",
    )
    store.append(record)
    assert len(store.list_records()) == 1
    assert store.list_records()[0].record_type == "outcome"

def test_mission_memory_store_rejects_invalid_type(tmp_path):
    store = MissionMemoryStore(tmp_path / "mission_memory.jsonl")
    with pytest.raises(ValueError, match="Invalid record type"):
        store.append(MissionMemoryRecord(
            record_id="mem-bad", mission_id="m-1", record_type="invalid",
            content={}, created_at="2026-06-08T12:00:00Z",
        ))

def test_mission_memory_store_filters_by_mission_and_type(tmp_path):
    ...

def test_mission_memory_store_search_by_keyword(tmp_path):
    ...

def test_mission_memory_store_summary(tmp_path):
    ...
```

**Step 3: 实现 MissionMemoryStore**

**Step 4: 运行测试验证**

```bash
.venv/bin/python -m pytest tests/test_mission_memory.py -v
```

**Step 5: 提交**

---

### Task 2: MissionAgent 自动记录集成

**Files:**
- Modify: `src/fireclaw_core/mission_agent.py`
- Modify: `tests/test_mission_agent.py`

**Step 1: MissionAgent 接受 MissionMemoryStore**

在 `MissionAgent.__init__` 增加 `mission_memory: MissionMemoryStore | None = None`。

**Step 2: mission 完成/失败时自动写入 outcome**

在 `plan_and_submit()` 和 `cancel_mission()` 中，当 mission 达到终态时，自动调用 `mission_memory.append()` 写入 outcome 记录。

**Step 3: 写失败测试**

```python
def test_mission_agent_records_outcome_on_completion(tmp_path):
    # 模拟 mission 完成，验证 outcome 记录被写入
    ...

def test_mission_agent_records_outcome_on_failure(tmp_path):
    # 模拟 mission 失败，验证 outcome 记录被写入
    ...
```

**Step 4: 实现集成**

**Step 5: 运行测试、提交**

---

### Task 3: CLI `memory` 子命令

**Files:**
- Modify: `src/fireclaw_core/mission_cli.py`
- Modify: `tests/test_mission_cli.py`

**Step 1: 增加 CLI 子命令**

```
python -m fireclaw_core.mission_cli memory list --mission-id <id> [--type outcome|observation|correction|lesson]
python -m fireclaw_core.mission_cli memory add --mission-id <id> --type <type> --content '{"key":"value"}'
python -m fireclaw_core.mission_cli memory summary [--mission-id <id>]
```

**Step 2: 写失败测试**

**Step 3: 实现 CLI**

**Step 4: 运行测试、提交**

---

### Task 4: README 文档更新

**Files:**
- Modify: `README.md`

更新 Mission Memory 章节，包含 Python API、CLI 用法和集成说明。

---

## 验证

全量测试：
```bash
.venv/bin/python -m pytest -q
```

预期：现有测试 + 新增测试全部通过。
