# UX-P0 一键启停、控制台探测与 CLI 命令分层设计方案

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

日期：2026-08-14
状态：Approved
主题：FireClaw 小白用户体验闭环之一键启停（start/stop）、控制台探测（open）与命令分层收敛

## 1. 背景与目标

在 FireClaw 此前的开发中，P0 运行时受管部署、操作员就绪判定、P1 软件/仿真故障注入矩阵以及第一项 `fireclaw setup` + Active Profile 已相继落地并推送到 `master`。
然而，当前新手操作路径仍存在以下摩擦：
1. `setup` 完成后，用户仍需理解并调用底层的 `fireclaw deploy run` 来启动服务，缺少对小白友好的 `fireclaw start` 与 `fireclaw stop`。
2. 顶层 `fireclaw --help` 依然直接暴露了 20+ 个工程命令（如 `submit-subtask`、`trace`、`events`、`lifecycle-check`、`robot-gateway` 等），使得日常操作员无所适从。
3. 缺少 `fireclaw open` 命令用于直接探测并定位操作控制台。

本设计旨在实现面向小白用户的命令收敛与极简生命周期管理，使普通用户默认仅需使用 5 个主命令：
`setup` -> `start` -> `open` -> `status` -> `stop`。

---

## 2. 核心架构与命令设计

### 2.1 `fireclaw start`

- **功能**：基于 Active Profile 一键启动后台守护进程（受管 Supervisor、ROS/仿真 Runtime、Gateway）。
- **参数**：
  - `--profile <path>`（可选，默认读取 Active Profile）
  - `--foreground`（可选，以阻塞前台运行模式启动，用于调试）
  - `--timeout <seconds>`（可选，等待 Gateway 就绪的最大超时，默认 15 秒）
  - `--json`（可选，以结构化 JSON 输出结果）
- **执行流程**：
  1. 解析 Active Profile 路径；若未初始化，直接返回稳定错误码 `active_profile_missing`，提示“请先运行 fireclaw setup”。
  2. 探测 `~/.fireclaw/state/runtime-daemon.json` 中的 PID：
     - 若进程活跃，直接输出“服务已在运行”并展示当前 Gateway 地址与状态，不重复启动。
     - 若 PID 已死，自动清理陈旧状态文件并继续。
  3. 后台模式下派生子进程组（Process Group），启动受管 Supervisor / Gateway，并将日志重定向至 `~/.fireclaw/logs/runtime-daemon.log`。
  4. 轮询 Gateway `/health` 接口直至就绪（或超时）。
  5. 写入受保护的 `runtime-daemon.json`（权限 `0600`，目录 `0700`）。
  6. 打印四段式就绪卡片与下一步建议（`fireclaw open`）。

### 2.2 `fireclaw stop`

- **功能**：一键优雅关停当前运行中的后台服务并确认安全终态。
- **参数**：
  - `--profile <path>`（可选，默认停止当前 Active Profile 绑定的服务）
  - `--timeout <seconds>`（可选，等待安全停机的超时，默认 10 秒）
  - `--force`（可选，超时后强制发送 SIGKILL）
  - `--json`（可选）
- **执行流程**：
  1. 读取 `runtime-daemon.json`。若未运行，提示“当前无正在运行的后台服务”。
  2. 向目标进程组发送 `SIGTERM` 信号。
  3. 轮询等待进程组安全退出；若超时且允许 force 则发送 `SIGKILL`。
  4. 删除 `runtime-daemon.json` 状态文件。
  5. 明确输出安全确认信息：“后台服务已终止”、“机器人动作已安全停止”、“资源已释放”。

### 2.3 `fireclaw open`

- **功能**：探测并打开操作控制台。
- **参数**：
  - `--browser / --no-browser`（可选，是否自动调起系统浏览器）
  - `--json`（可选）
- **执行流程**：
  1. 读取 `runtime-daemon.json` 确认服务运行状态（若未运行提示先执行 `start`）。
  2. 提取 Gateway URL（如 `http://127.0.0.1:8080`）。
  3. 若允许且存在桌面环境，调用 `webbrowser.open(url)` 尝试在浏览器中打开。
  4. 输出控制台访问链接与状态提示。

---

## 3. 顶层 CLI 帮助分层（Progressive Disclosure）

修改顶层 ArgumentParser 帮助格式化器（Custom Help Formatter）：

1. **默认 `fireclaw --help` 输出**：
   - 突出展示 **Core Commands (日常操作)**：
     - `setup`：首次配置向导 (仿真环境/实机预检)
     - `start`：一键启动当前机器人运行环境 (后台守护进程)
     - `open`：打开操作员控制台 / Gateway 界面
     - `status`：检查当前机器人与服务就绪状态
     - `stop`：一键安全关停当前运行环境
   - 清晰折叠/分组展示 **Advanced & Developer Tools**（包含 `recover`、`hardware-safety`、`fault-test`、`deploy`、`mission`、`doctor` 等），并提示使用 `fireclaw --help --all` 查看全量参数。

2. **全量 `fireclaw --help --all` 输出**：
   - 完整列出所有子命令和详细说明，保持既有自动化脚本与 CI 完全兼容。

---

## 4. 状态 Schema 与安全边界

### 4.1 守护进程状态文件
- 路径：`$FIRECLAW_HOME/state/runtime-daemon.json`
- 权限：文件 `0600`，目录 `0700`
- 字段内容：
  ```json
  {
    "schema_version": 1,
    "pid": 12345,
    "pgid": 12345,
    "profile_path": "/home/lpp/.fireclaw/profiles/gazebo-turtlebot3-burger.toml",
    "mode": "simulation",
    "gateway_url": "http://127.0.0.1:8080",
    "started_at": "2026-08-14T07:02:00Z",
    "log_path": "/home/lpp/.fireclaw/logs/runtime-daemon.log"
  }
  ```

### 4.2 安全与失败处理原则
1. **实机安全不可绕过**：若配置为 `mode: real`，`start` 必须执行 `hardware-safety` 预检，若前置安全条件不满足，立即拒绝启动。
2. **幂等性与自愈**：检测陈旧 PID（死进程）并自动清理，不产生僵死状态。
3. **Fail-Closed 停机**：`stop` 在关停 Gateway 时确保权威数据库终态落盘，不遗留悬挂后台任务。

---

## 5. 测试与验证策略

1. **单元测试 (`tests/test_user_lifecycle_cli.py`)**：
   - `test_start_without_setup`：未 setup 时友好拦截；
   - `test_start_background_and_status`：后台正常启动、生成状态文件、轮询就绪；
   - `test_start_idempotent`：重复 start 不产生多重进程；
   - `test_stop_graceful`：正常停止并清理状态；
   - `test_stale_pid_cleanup`：模拟崩溃后 PID 自愈清理；
   - `test_cli_help_grouping`：验证默认 help 输出仅突出 5 个核心命令，`--all` 输出全量。
2. **全量回归与故障矩阵**：
   - `pytest -q` 全量通过；
   - `fireclaw fault-test run --live-ros` 验证既有故障注入闭环不受影响。
3. **E2E 隔离验证**：
   - 在独立临时 `FIRECLAW_HOME` 中完整跑通 `setup -> start -> status -> open -> stop` 真实流水线。
