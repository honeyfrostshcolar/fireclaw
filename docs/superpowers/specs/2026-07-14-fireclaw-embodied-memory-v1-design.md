# FireClaw Embodied Memory v1 设计

## 目标

在现有 `MissionMemoryStore` append-only JSONL 与 `SqliteMemoryIndex` FTS5 检索之上，增加一层适用于消防机器人的具身记忆契约，使任务、观测、技能调用、安全决策、机器人本体状态、操作员纠正及其关系能够：

- 作为不可变证据持久化；
- 按运行模式、坐标系、空间范围和时间范围查询；
- 通过有向关系还原“观测 -> 决策 -> 动作 -> 结果”；
- 在删除或损坏 SQLite 派生索引后，从 JSONL 证据重建；
- 默认避免将敏感本体状态和操作员纠正暴露给全文检索。

## 参考与约束

本设计参考了本地 `emem-main/` 的 `ObservationNode`、`EpisodeNode`、`GistNode`、graph edge、空间/时间查询和 consolidation 分层，但没有直接复制其实现。

本轮未能完成 OpenClaw memory analogue 核对：仓库中没有 `openclaw/`，当前会话也没有暴露 CodeGraph MCP 工具。因此本设计沿用 FireClaw 已有 `MissionMemoryStore`/`SqliteMemoryIndex` 边界，并在此基础上增量扩展。后续恢复 OpenClaw 源码与 CodeGraph 后，应专门复核 session memory、memory retrieval 和 local persistence 的模块形状。

## 架构

```text
RobotAgent / MissionAgent / SafetyGate / SkillRuntime
                         |
                         v
                 EmbodiedMemoryStore
                  /               \
                 v                 v
    append-only JSONL evidence   SQLite projection
    MissionMemoryStore           FTS5 + metadata + edges
                 ^                 |
                 |_________________|
                    rebuild_index
```

JSONL 是事实来源。SQLite 只承担检索与图投影，不能成为事故审计的唯一数据源。

写入顺序固定为：

1. 验证类型、安全域和关系端点；
2. append JSONL 证据；
3. 更新 SQLite 派生索引。

若第 3 步失败，原始证据仍然保留，调用方应报告 memory index degraded，并运行 `rebuild_index()`。不能因为索引失败而删除已写入的事件。

## 数据模型

### EmbodiedMemoryEvent

必需字段：

| 字段 | 含义 |
|---|---|
| `event_id` | 全局唯一、append-only 的事件标识 |
| `mission_id` | mission 审计作用域 |
| `event_type` | 类型化事件，如 `observation`、`skill_invocation`、`safety_decision` |
| `payload` | 事件业务数据，不允许覆盖保留键 |
| `runtime_mode` | `real`、`simulation` 或 `replay` |
| `source_type` | `operator`、`planner`、sensor、skill、safety gate 等来源 |
| `observed_at` | 带时区的实际观测时间 |

可选字段包括 `robot_id`、`subtask_id`、`episode_id`、`pose`、`confidence`、`sensitivity` 和 `derived_from`。

首版事件类型：

- `mission`
- `command`
- `plan`
- `subtask`
- `skill_invocation`
- `observation`
- `safety_decision`
- `body_state`
- `outcome`
- `correction`
- `lesson`
- `gist`

`gist` 是派生事件，必须通过 `derived_from` 指向原始证据。删除 gist 不能影响原始事件。

### SpatialMemoryContext

空间上下文包含：

- `frame_id`
- `x`, `y`, optional `z`
- optional `floor`
- `uncertainty_radius_m`

`frame_id` 强制必填。查询不得隐式比较不同 map、building 或 robot frame 下的坐标。

空间查询使用“查询圆/球与事件不确定性区域相交”的条件：

```text
distance(query_center, event_center) <= query_radius + event_uncertainty_radius
```

当前实现使用 SQLite 数值过滤和确定性距离排序，不引入 `Rtree` 运行时依赖。数据规模增大后可将同一 API 后端替换为 SQLite R*Tree，但结果语义必须保持一致。

### EmbodiedMemoryRelation

关系方向统一为：

```text
source_record_id --relation_type--> target_record_id
```

首版关系类型：`belongs_to`、`follows`、`subtask_of`、`summarizes`、`observed_in`、`caused_by`、`corrects`、`supports`。

首版只允许同一 `mission_id`、同一 `runtime_mode` 的事件建立关系。跨 mission 的长期 lesson/entity 关系需要单独的 fleet-memory 权限与 provenance 设计，不能通过放宽当前校验偷偷实现。

运行时关系不通过“查询最新事件”推断。MissionAgent、SafetyGate 和
PlanExecutor 在当前任务调用链中显式传递事件 ID；跨 mission-control 与
robot-gateway 存储时，`StructuredRobotTask.memory_lineage` 仅传递外部谱系，
待聚合/对账时使用，不创建端点不存在的本地关系。

## 安全不变量

1. `runtime_mode` 必须显式存在，禁止 `unknown` 默认值。
2. embodied query 必须显式传入 `runtime_mode`，避免 simulator 经验污染 real-robot 决策。
3. 空间查询必须显式传入 `frame_id`。
4. event/relation ID 不允许在 JSONL 中重复。
5. relation 端点必须已经存在，且 mission/runtime mode 一致。
6. `observed_at` 与 `created_at` 必须为带时区 ISO-8601 时间。
7. `confidence` 范围为 `[0, 1]`；坐标与不确定性必须为有限数。
8. `body_state`、`correction` 及 `restricted` 事件默认不进入 FTS5，但仍可用于有权限的结构化查询。
9. SQLite 索引可清空重建；JSONL 证据不可由重建流程修改。

## SQLite 派生投影

`memory_records` 在原字段基础上增加：

- `runtime_mode`, `source_type`, `episode_id`
- `observed_at`, `observed_at_epoch`
- `frame_id`, `position_x`, `position_y`, `position_z`
- `uncertainty_radius_m`, `confidence`, `sensitivity`

`memory_relations` 保存关系投影。旧版 SQLite 文件通过 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 原位迁移，不要求删除已有索引。

FTS5 仍只用于文本召回。空间、时间和 relation 查询直接访问结构化表，不依赖 payload 是否允许全文索引。

## 公共 API

`EmbodiedMemoryStore` 提供：

- `record_event(...)`
- `append_event(event)`
- `add_relation(...)`
- `append_relation(relation)`
- `list_events(...)`
- `list_relations(...)`
- `search_text(query, runtime_mode=...)`
- `query_spatial(runtime_mode=..., frame_id=..., ...)`
- `query_temporal(runtime_mode=..., start_at=..., end_at=...)`
- `neighbors(record_id, runtime_mode=...)`
- `rebuild_index()`

首版没有直接接入 `MissionAgent`、`SkillRuntime` 或 `SafetyGate`。下一阶段应在这些边界处写入事件，而不是让 planner 自行拼接 memory JSON。

运行时事件生产契约现已定义在
`docs/architecture/embodied-memory-event-production.md`。新接入必须使用
`EmbodiedMemoryProducer`，并持久化 producer authority、evidence kind 和来源
provenance；底层 `EmbodiedMemoryStore.record_event()` 仅保留给兼容导入、重建和
受控迁移使用。

## 测试契约

聚焦测试覆盖：

- JSONL 证据与 SQLite 文本投影同步；
- runtime mode 与 coordinate frame 隔离；
- 带不确定性的空间半径查询；
- inclusive 时间区间及结构化过滤；
- 敏感事件不进入 FTS、但可结构化检索；
- relation 写入、邻接查询和索引重建；
- 跨 runtime relation 在写证据前被拒绝；
- 重复 ID、无时区时间和保留键被拒绝；
- 旧 SQLite schema 原位迁移。

## 研究影响

### 工程正确性

本版本补齐了 FireClaw 具身记忆的可审计数据契约，并为之后接入机器人运行时提供确定性基线。其核心价值是安全域隔离、provenance 和可重建性。

### 研究有效性

当前实现本身不是强研究创新：typed events、时空检索和 graph relations 都有成熟先例。它适合作为后续方法研究的基础设施与 deterministic baseline。

更有研究价值的方向包括：

- 在 sensor uncertainty、通信退化和地图漂移下进行可信 memory retrieval；
- 用 safety outcome 约束 consolidation，避免危险经验被压缩成错误 lesson；
- 多机器人之间带 provenance、权限和冲突消解的共享记忆；
- 对 retrieval 是否改善任务成功率、规划安全性和故障恢复进行系统消融。

### 发表级贡献

仅凭本版本不应声称 top-tier novelty。要形成论文贡献，至少需要提出明确的新方法，和标准 RAG/embodied memory/episodic graph baseline 对比，并提供仿真与真实机器人上的任务成功率、安全违规率、错误检索率、延迟、内存规模和 incident reconstruction 完整度等证据。

## 后续阶段

1. 接入 `MissionAgent`：记录 command、plan、subtask 与 outcome。
2. 接入 `SafetyGate`：记录 allow/block/escalate、规则 ID、输入状态和 operator confirmation。
3. 接入 `SkillRuntime`：记录 invocation、precondition、progress、result、timeout 与 cancellation。
4. 接入 robot adapter：记录带 frame、uncertainty 和 sensor provenance 的 observation/body state。
5. 增加 episode/gist consolidation，但保留 raw evidence，并测试 hallucinated summary 与 unsafe lesson rejection。
6. 在 OpenClaw reference 与 CodeGraph 可用后复核 upstream memory/session/persistence 模块形状。
