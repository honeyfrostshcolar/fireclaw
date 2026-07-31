# Mission 后台运行、运行中控制与最终报告

## 时间

2026-07-31（本次实现完成；全量测试结束于本日）

## 任务目标

继续完成 Mission/Robot 任务闭环的三项能力：

1. 用户提交 Mission 后立即返回，由后台 Mission Run 持续执行；
2. 执行期间支持查询、操作员纠正、暂停、恢复和取消；
3. Mission 收集所有 Robot 终态后，再调用 LLM 生成最终报告并持久化。

## 设计依据

参考 OpenClaw 的 Agent Harness/后台 run 与 session 控制思路，但 FireClaw 保留机器人安全边界：后台管理器只负责生命周期协调，LLM 最终报告调用不暴露物理工具，暂停/取消/纠正都写入可审计事件。

## 已修改的主要文件

- `src/fireclaw_core/mission/mission_run.py`
  - 新增线程安全的 `MissionRunControl` 和 `MissionRunManager`；
  - Run 状态：`queued`、`planning`、`running`、`paused`、`cancel_requested`、`reporting`，以及 `completed`、`blocked`、`escalated`、`failed`、`timed_out`、`cancelled`、`lost`；
  - 提交时创建 Mission Registry 占位记录，后台 daemon worker 执行计划、调度、终态收集和报告；
  - 取消会通知 `MissionAgent.cancel_mission`，纠正会写入 Mission Agent 的操作员纠正记录；
  - 运行事件通过事件总线发布。

- `src/fireclaw_core/mission/mission_gateway.py`
  - 默认 scheduler-backed Mission 使用后台 Run；显式 `background=false` 仍保留同步兼容路径；
  - 新增 Run、报告、暂停、恢复、纠正接口，并让取消路由到后台 Run；
  - Trace 返回当前 Run 状态和最终报告。

- `src/fireclaw_core/mission/mission_gateway_client.py`
  - 增加对应客户端方法。

- `src/fireclaw_core/gateway/method_scopes.py`
  - 为 Run 控制和报告读取增加方法权限范围。

- `src/fireclaw_core/mission/mission_agent.py`、`mission_scheduler.py`
  - 接收 Run 控制；规划/分组轮询期间响应取消和暂停；
  - 记录操作员纠正、最终报告；
  - 轮询结果缺失或仍非终态时标记 `timed_out`，但存在计划失效证据时交给 revision dispatcher 处理，避免把修订误判成超时。

- `src/fireclaw_core/planner/llm_planner.py`
  - 新增 `generate_final_report`；使用共享 `ProviderAgentHarness`，不提供物理工具，只能根据权威 Mission trace 生成结构化总结；
  - 调用失败时使用确定性 fallback，并保留 `llm_error`。

- `src/fireclaw_core/mission/mission_report.py`、`mission_registry.py`
  - 统一报告归一化、Robot 结果计数、需关注项和后续操作员动作；
  - 报告事件和报告内容写入 Registry/trace。

- `tests/test_mission_run.py`
  - 覆盖后台立即返回、暂停/恢复/纠正、取消、报告生成和 Registry 持久化。

## 对外流程示例

1. `POST /missions` 提交 `use_scheduler=true`：立即返回 `accepted`、`mission_id`、`run_id`，不等待机器人完成。
2. `GET /missions/{id}/run` 查看 `queued/planning/running/paused/reporting/...`。
3. 执行中可 `POST /missions/{id}/pause`、`resume`、`corrections`、`cancel`。
4. Robot 终态全部收齐后，Run 进入 `reporting`，调用无工具的报告 LLM；完成后写入 `mission.report`，Run 进入统一终态。
5. `GET /missions/{id}/report` 或 Mission trace 可读取报告和各 Robot 结果。

## 安全与已知边界

- 暂停只阻止后续规划/调度推进，不伪造机器人已经停止；取消仍通过现有 Mission Agent/Robot 取消链路执行。
- 操作员纠正是高优先级约束并可审计，但不会绕过安全门，也不会未经证据自动改写正在执行的物理动作。
- 最终报告阶段没有 ROS、shell 或其他物理 Tool，且只能使用权威 trace；模型失败时回退到确定性报告。
- 活跃 Run 的线程控制状态目前是进程内对象；Mission Registry、事件、终态和报告已持久化。进程重启后的继续执行仍由既有 checkpoint/recovery 机制承担，Run manager 的自动重建可作为后续工作。
- 直接指定 `background=true,use_scheduler=false` 的兼容路径不承担 scheduler 轮询；默认 gateway 调度任务走完整后台流程。

## 验证

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
1997 passed, 7 skipped in 175.17s
```

此前聚焦测试和修复：Mission Run/Registry 8 passed；Mission scheduler/agent/planner 122 passed；Mission Gateway/client/scopes 116 passed。

## 下一步建议

把活跃 Run 的状态快照和控制意图接入持久化 checkpoint，使进程重启后能自动恢复/标记 `lost`；然后补充真实 Gateway 长连接、权限拒绝和多 Robot 并发下的集成测试。
