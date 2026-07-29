# FireClaw Agent 术语统一

更新时间：2026-07-29T13:06:48+08:00

## 任务目标

统一 FireClaw 中央端与机器人端 Agent 的架构称谓，停止把常驻机器人端
Agent 称为中央 Agent 的 subagent，同时保持现有 Python API、配置和持久化
数据兼容。

## 用户决策

- 机器人端 Agent 正式称为 `Robot Agent`（机器人智能体）。
- 中央端正式称为 `Mission Coordinator`（中央任务协调器）。
- 两者属于同一个 FireClaw 架构，在同一仓库中联合开发。
- Robot Agent 受 Mission Coordinator 的任务级协调和任务合同约束，但拥有
  本机安全拒绝权和具身执行权。
- `subagent` 只表示未来按需派生、完成认知任务后退出的
  `Delegated Worker Subagent`，不再表示物理机器人。

## 当前结论

FireClaw 是包含两种常驻 Agent 角色的分层具身智能体框架，不是
main-agent/subagent 复制关系：

```text
operator
  -> Mission Coordinator
       -> StructuredRobotTask
            -> registered Robot Agent
                 -> local planner / SafetyGate / skills / robot adapter
```

共享基础设施可以包括 provider、上下文管理、tool-call 协议、记忆事件、
审计和取消机制；角色特有的工具目录、权限、状态来源、安全策略和生命周期
保持独立。

## 兼容性边界

本次不进行破坏性代码重命名。以下标识符和数据名称暂时保留：

- `RobotSubagentClient`
- `SubagentRunRecord`
- `JsonlSubagentRegistry`
- `subagent_client`
- `subagent_registry`
- `subagents.jsonl`

这些名称当前被明确标注为 legacy-named identifiers。后续如需迁移，应单独
设计导入别名、弃用周期、配置兼容和持久化迁移。

## 文件修改

- 新增 `docs/architecture/fireclaw-agent-terminology.md` 作为正式术语规范。
- 更新 `README.md` 的总体定位、架构图、任务合同、fleet、事件和执行权表述。
- 更新中英文 OpenClaw 对齐文档及现行 memory/deployment 文档。
- 在两份 2026-06 历史架构文档顶部增加术语说明，保留历史正文。
- 更新 CLI 帮助、用户可见错误、模块与类注释。
- 同步更新受错误消息影响的测试期望和测试说明。

## OpenClaw 对照

FireClaw 的 Robot Agent 不对应 OpenClaw 的临时 `sessions_spawn` worker。
Robot Agent 是预先部署、注册并长期运行的具身 Agent；OpenClaw 式
subagent 仍可作为未来中央端或机器人端的临时认知 worker。

## 验证

执行：

```bash
git diff --check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest -q \
  tests/test_subagent_client.py \
  tests/test_subagent_registry.py \
  tests/test_mission_agent.py \
  tests/test_mission_event_aggregator.py \
  tests/test_mission_cli.py \
  tests/test_lifecycle_reconciler.py \
  tests/test_lifecycle_maintenance.py \
  tests/test_embodied_mission_e2e.py
```

结果：`179 passed in 26.09s`。

首次在受限沙箱内运行时有 14 项测试因禁止创建回环 socket 而失败；允许本地
测试 Gateway 绑定临时端口后全部通过，确认不是代码回归。

## 工作区说明

开始本次任务前，工作区已有 active-observation/context-evaluation 相关未提交
修改，以及不相关的
`memory/2026-07-28/codex-usage-reset-skill-check.md`。本次没有还原或覆盖这些
已有工作。

## 下一步建议

继续开发时，在正式设计和新 API 中只使用 `Mission Coordinator` 与
`Robot Agent`。若下一阶段开始强化机器人端能力，应先明确共享 Agent 内核
和 Robot Agent 专属 runtime 的边界，不要因旧 `subagent_*` 文件名重新引入
架构混淆。
