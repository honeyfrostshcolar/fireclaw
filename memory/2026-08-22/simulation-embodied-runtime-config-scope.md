# 仿真 Robot Gateway embodied runtime 配置作用域

## 2026-08-22

### 任务目标

回答用户关于在根 `fireclaw.toml` 的 `[robot_gateway]` 中持久加入
`embodied_runtime_mode="simulation"`、evidence JSONL 和 SQLite index 路径是否只对仿真有效、是否产生其他影响。

### 恢复与检查

- 继续使用 `memory/2026-08-21/minimal-navigation-demo-readiness-audit.md` 的现场启动诊断；工作树仍是预期的多文件
  dirty 状态，没有覆盖或整理用户改动。
- 使用 CodeGraph 复核 `validate_robot_deployment_binding()`、`GatewayConfig`、`FireClawGateway` embodied memory
  初始化和 runtime-mode scoped retrieval。

### 结论

- 这些字段不是根据 Gazebo 自动生效；任何加载该 TOML 的 Robot Gateway 都会信任并采用
  `embodied_runtime_mode="simulation"`。因此该文件必须被视为专用 simulation Profile，不能原样用于实机。
- 当前组合 `[deployment].mode="simulation"`、`dry_run=false`、`embodied_runtime_mode="simulation"` 会允许 ROS1
  live adapter 调用，同时把 embodied evidence 固定标记为 simulation。校验无法证明 `ROS_MASTER_URI` 实际指向
  Gazebo；演示应显式使用本机 ROS Master，不能把 mode 字段当成硬件隔离屏障。
- 配置会创建/追加 `embodied-memory.jsonl`，打开/维护 `embodied-memory-index.sqlite3`，并启用 robot adapter、
  safety gate、physical Tool 等 embodied producers、entity memory 和 replication exporter。会增加少量磁盘 I/O、
  SQLite 和内存开销；日志会持续增长。
- 它不改变 Mission/Robot LLM planner 选择、Provider、端口、navigation timeout、目标坐标、move_base 算法或 ROS
  Topic bindings。`dry_run=false` 仍决定该路径实际调用 ROS backend。
- 每台机器人、每个 runtime domain 应使用独立 evidence/index 路径；不要让两个 Robot Gateway process 同时拥有
  同一组存储文件。
- 实机应使用独立、审查过的 real Profile、real runtime memory namespace、硬件安全接口和网络/TLS 身份；不要通过
  在同一文件中临时把 `simulation` 改成 `real` 来部署。

### 建议

- 当前面试 demo 可持久加入这些字段，但应将文件明确命名/管理为 simulation-only，例如后续拆为
  `fireclaw.sim.toml`；当前根文件已经声明 simulation，因此本次演示范围内一致。
- Robot Gateway 演示终端显式设置 `ROS_MASTER_URI=http://127.0.0.1:11311`，并检查 `/move_base` 来自本机仿真。

### 状态

- 未修改产品代码或用户配置；仅新增本记录。
- 未启动 ROS/Gateway，未 stage/commit/push。
