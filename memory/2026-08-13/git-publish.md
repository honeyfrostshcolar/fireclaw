# FireClaw 提交与 master 发布记录

## 2026-08-13T13:29:15+08:00 — 历史未提交改动拆分并推送完成

### 任务目标

用户要求把此前完成的 FireClaw 改动按功能分别 commit，并推送到远端 `master`。本次没有强制推送，
没有删除安全冻结、运行时数据或本地配置。

### 提交范围

按功能边界创建了以下提交：

- `8531e1c` `feat: add managed plugin runtime deployment`
  - Plugin Runtime deployment、systemd/supervisor、Navigation move_base 运行时、TurtleBot3 资产、
    Profile 路径合同与相关测试。
- `8d0344f` `feat: add operator readiness and recovery control`
  - 统一 operator readiness、Fleet Doctor/status/recover、SSE cursor 续播、资源冻结恢复和相关测试。
- `3fad1b9` `feat: prepare real robot hardware safety acceptance`
  - ROS1 硬件安全插件、实机 Profile 契约、非致动预检、停止证据 schema、验收工具和测试。
- `91dacc3` `test: add fault injection acceptance matrix`
  - 七类软件/仿真故障注入 runner、证据校验、验收文档和测试。
- `ee8b2be` `feat: add guided first-run simulation setup`
  - `fireclaw setup`、active Profile、无凭据 TurtleBot3 仿真模板、首次使用文档和测试。

### 推送证据

推送前通过 `git fetch origin master` 确认远端 `master` 为本地提交链祖先；随后执行非强制：

```text
git push origin HEAD:master
41bcb30..ee8b2be  HEAD -> master
```

推送后再次 fetch 验证：`origin/master` 与 `HEAD` 均为
`ee8b2be1763c7d4e8f46915da51318c81d0107e5`。

### 未纳入提交的本地文件

- `data/robots/gazebo_turtlebot3/memory-runtime.sqlite3`：本地运行数据库，不应进入仓库；
- `memory/2026-08-12/rate-limit-reset-credits.md`：与本次 FireClaw 功能提交无关的个人查询记录。

两者均未删除或修改。当前工作分支仍为
`agent/embodied-evaluation-collision-calibration`；本地工作树只显示上述两个未跟踪文件，远端
`master` 已包含本次五个功能提交及此前该分支已有提交。

### 后续

后续如继续开发，先从 `origin/master` 或明确的新分支开始；不要为了清理上述本地文件而直接删除，
需由用户单独决定其保留或清理方式。
