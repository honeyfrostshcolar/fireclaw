# FireClaw 全功能测试策略盘点

## 时间

- 2026-08-26T11:07:34+08:00

## 任务目标

- 基于当前仓库实际实现与既有测试资产，回答“为了验证此前创建的所有功能是否达到预期，接下来应进行哪些测试”。
- 将测试拆成工程正确性、机器人安全、研究有效性和 publication-level 证据，而不是把单元测试通过等同于系统可信。

## 当前进展

- 已读取最近两个日期目录 `memory/2026-08-24/` 与 `memory/2026-08-23/` 的相关记录。
- 已检查当前 Git 状态：分支 `agent/embodied-evaluation-collision-calibration` 领先远端 7 个提交，工作区存在大量用户/Antigravity 已有修改和未跟踪文件；本轮未修改任何产品代码，也未覆盖这些改动。
- 已通过 CodeGraph 检查 FireClaw 的 Mission scheduler、审计和现有测试关系，并检查 OpenClaw 的 session/tool/gateway 测试组织方式。可复用的原则是：围绕真实边界建立 harness，验证 owner/session 隔离、一次性授权、持久化、并发和流式恢复；FireClaw 额外加入 ROS、物理停止证据、碰撞、状态不确定性与仿真/真机隔离。
- 当前 `tests/` 下有 225 个 `test_*.py` 文件。
- 当前执行 `/home/lpp/miniconda3/envs/py310/bin/python -m pytest --collect-only -q` 成功，收集到 2474 项测试，用时 1.45 秒；本轮没有执行完整测试套件。
- 最近一次持久化记录中的完整回归结果为 2026-08-24 的 `2466 passed, 8 skipped`，但不能把该历史结果直接当作当前工作区的新鲜通过证明。

## 已检查文件

- `README.md`
- `docs/evaluation/embodied-evaluation.md`
- `extensions/navigation-move-base/config/acceptance/frozen-suite.yaml`
- `tests/fixtures/embodied_eval/rescue_scenarios.json`
- `tests/fixtures/embodied_eval/planning_scenarios.json`
- `tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json`
- `memory/2026-08-24/mission-planning-live-stream-tui-fix.md`
- `memory/2026-08-24/mission-tui-ros-log-observability-fix.md`
- `memory/2026-08-23/antigravity-change-audit-and-safety-hardening.md`
- `memory/2026-08-23/advanced-user-chaos-acceptance-suite.md`
- `memory/2026-08-23/user-dialogue-acceptance-script.md`
- `memory/2026-08-23/repeated-state-read-relative-pose-diagnosis.md`
- `memory/2026-08-23/demo-slow-blocked-latency-diagnosis.md`

## 关键结论

1. 当前已经有较强的自动化工程测试基础，下一阶段不应继续零散增加测试，而应建立可重复的分层验收矩阵和发布门禁。
2. 三条评测 lane 必须保持分离：
   - `deterministic_integration`：Mission/Gateway/scheduler/terminal/report/data contract；
   - `llm_planning`：冻结状态下的真实模型规划质量，严格 no-dispatch；
   - `ros_gazebo_system`：冻结计划/策略下的 ROS/Gazebo 执行与恢复。
3. 当前明显覆盖缺口：
   - deterministic fixture 只有 2 条 point-navigation happy path；
   - LLM development fixture 只有 point/area/entity 3 种场景，五个 seed 共 15 cases，且不是 held-out test；
   - Gazebo frozen suite 有 success/cancel/timeout/abort/stall-recover/stall-escalate 六条 lane 和碰撞 positive control，但 validation/test 当前每场景只冻结 repeat index 0；
   - 多机器人、真实 provider、大规模并发/耐久、实机 stop/estop、操作员用户研究仍需系统化证据。
4. 最高优先级不是自然语言花样，而是安全不变量：确认前零副作用、artifact 至多消费一次、唯一终态、无完成证据不得成功、通信中断不得伪装停止、readiness/runtime/identity 漂移使旧预览失效、仿真配置绝不能驱动真机。

## 推荐测试矩阵与顺序

### P0：当前工作区自动化基线

- 完整 `pytest`、Python compile、`git diff --check`。
- 按模块记录失败归属：planner、safety/authorization、mission runtime、gateway/SSE、memory/RAG、plugin/tool、ROS adapter、web console、deployment/release。

### P1：操作员黑盒仿真验收

- 精确坐标：拒绝预览时零动作；确认后到达并有真实终态与位姿证据。
- 模糊目标：必须追问；`去二楼救人` 不得编造楼层/坐标。
- 相对返航：有 TF 时绑定同一机器人实时 pose；无 TF 时澄清且不 dispatch。
- 多点巡检：顺序、plan digest、每步上下文、失败后的 policy 均正确。
- `Ctrl+C` 只脱离观察，`follow` 用 SSE cursor 恢复；`cancel` 只有在停止证据后才能声称 cancelled。
- TUI 实时显示 Mission Agent、Robot Agent、Tool、ROS WARN/ERROR 和真实耗时；背压不得阻塞模型规划。
- preview-confirm 期间注入 readiness/runtime drift，旧 token 必须失效。

### P2：受控 Gazebo 系统验收

- 执行冻结六条 lane：success、cancel、timeout、abort、stall-recover、stall-escalate。
- 单独执行 collision positive control，先证明观测链能检测已知碰撞；否则零碰撞结果无效。
- 保存 proof bundle、contact stream、Mission/Robot trace、Plugin/Tool schema 与资源哈希。

### P3：混沌、竞态与恢复

- 双客户端同时确认、确认响应丢失、重复命令/idempotency。
- cancel-complete race、Gateway/roscore 重启、SSE 断线和慢消费者、SQLite lock/full、传感器失效、队列饱和。
- 验证唯一终态、无重复物理动作、ambiguous outcome 的诚实表达和完整 audit lineage。

### P4：多机器人

- 同一 execution group 并行、跨 group 顺序、资源租约冲突、单机器人部分失败、重试/重分配、通信分区、robot identity/runtime epoch 漂移。
- 检查一个机器人失败时其他机器人是否严格遵循显式 failure policy。

### P5：Memory、RAG 与安全攻击

- 任务/纠正/结果持久化、重启恢复、session/operator 隔离、检索 relevance、过期与冲突事实。
- memory、RAG、Tool output 中的 prompt injection 只能作为 advisory evidence，不能获得授权或伪造成功。
- 权限 scope、路径穿越、日志/凭据脱敏、Plugin owner 冲突、未知 Tool fail closed、sim/real 配置隔离。

### P6：真实模型规划评测

- 扩展冻结场景：明确任务、缺参任务、矛盾状态、陈旧/不确定 belief、不可用能力、风险目标、多步任务、恢复决策与对抗输入。
- 多模型、多 seed 重复；分别报告 `contract_pass_rate`、`first_try_clean_rate`、`tool_protocol_valid_first_try_rate`、`planning_recovery_rate`、正确澄清率、确定性拒绝数、calls/tokens/cost 和 p50/p95 latency。
- 不把 scripted provider、development split 或 runtime repair 后成功冒充模型原始能力。

### P7：性能、耐久与实机分阶段验收

- 4--8 小时仿真 soak、连续任务、数据库增长、事件丢弃率、time-to-first-progress、dispatch latency、cancel-to-stop latency、恢复耗时。
- 实机先做非致动 preflight，再做封闭低速导航、厂商 stop、watchdog、物理急停、driver disable、odometry 静止证据、断网/传感器掉线；任何故障默认 hold/stop/escalate。

## 研究与 publication-level 评价

- 工程正确性：现有 2474 项测试和三 lane 基础较强，但仍需当前工作区新鲜全量回归与黑盒验收。
- 研究有效性：需比较规则/确定性 planner、LLM planner、无 memory、无 active observation、无 recovery、不同 diagnosis 位置等 baseline/ablation；危险 ablation 仅限离线或仿真。
- Publication-level：需要冻结 held-out test split、重复实验与置信区间、独立 unsafe 标签、匹配 scorer、干净 commit provenance、重复 Gazebo 和少量实机验证。TUI/SSE 或单次 demo 本身不足以构成论文贡献。

## 下一步建议

1. 先建立一张“功能 -> 场景 -> oracle -> 指标 -> 自动化入口 -> 证据路径”的验收表。
2. 运行 P0，修复所有新鲜回归。
3. 手工跑 P1 的 8--10 条黑盒场景并保留 mission id、plan digest、事件序列、前后位姿和终态。
4. 将 P1 稳定场景固化成自动化 acceptance harness。
5. 再执行 P2/P3；完成工程验收后才进入真实模型统计实验和实机验收。

## 建议命令

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
/home/lpp/miniconda3/envs/py310/bin/python -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/<unique-run-id> \
  --adapter simulator
FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development \
FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX=0 \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
fireclaw fault-test run --live-ros
```

## 本轮修改

- 仅新增本执行记录：`memory/2026-08-26/fireclaw-test-strategy.md`。
- 未修改产品代码，未运行物理动作，未创建 commit。
