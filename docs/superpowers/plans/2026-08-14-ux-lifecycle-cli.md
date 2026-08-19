# UX-P0 一键启停、控制台探测与 CLI 命令分层实施计划

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现面向普通操作员的一键启停命令（`fireclaw start` / `fireclaw stop`）、控制台探测命令（`fireclaw open`）以及顶层 CLI 帮助菜单的分层收敛（Progressive Disclosure），完成小白首次使用闭环的生命周期与交互通道。

**Architecture:**
通过在 `src/fireclaw_core/infra/daemon_manager.py` 中构建用户级守护进程管理器，管理以独立进程组启动的 Supervisor / Gateway 实例，在 `$FIRECLAW_HOME/state/runtime-daemon.json`（`0600` 权限）记录运行状态并实现僵尸 PID 自愈；在 `mission_cli.py` 中注册 `start`、`stop`、`open` 顶层命令，并重构帮助输出格式，将 20+ 命令分类为“核心日常命令”与“高级开发者工具”。

**Tech Stack:** Python 3.8+, `argparse`, `dataclasses`, `subprocess`, `os`, `signal`, `urllib.request`, `webbrowser`, `pytest`

## Global Constraints

- 严守物理安全隔离：`mode: real` 启动前强制执行 `hardware-safety` 预检，绝不静默致动物理硬件。
- 遵循 OpenClaw-first 架构模式：保持本地文件持久化状态、状态自愈与清晰的四段式错误提示。
- 权限与路径安全：守护进程状态文件权限 `0600`，目录权限 `0700`，所有路径进行越界与规整校验。
- CLI 完全向后兼容：保留所有既有子命令与参数，不破坏 CI/CD 与自动化测试。

---

### Task 1: 守护进程生命周期管理器 (`DaemonRuntimeManager`)

**Files:**
- Create: `src/fireclaw_core/infra/daemon_manager.py`
- Test: `tests/test_daemon_manager.py`

**Interfaces:**
- Consumes:
  - `fireclaw_core.infra.runtime_paths.resolve_fireclaw_runtime_root`
  - `fireclaw_core.infra.user_setup.resolve_active_profile_path`
  - `fireclaw_core.agent.robot_profile.load_robot_capability_profile`
  - `fireclaw_core.deployment.load_runtime_deployment_profile`
- Produces:
  - `DaemonState` (dataclass)
  - `DaemonRuntimeManager`
    - `start_daemon(profile_path: Path | None, *, foreground: bool = False, timeout: float = 15.0) -> dict[str, Any]`
    - `stop_daemon(profile_path: Path | None, *, timeout: float = 10.0, force: bool = False) -> dict[str, Any]`
    - `get_daemon_status(profile_path: Path | None) -> dict[str, Any]`
    - `open_console(profile_path: Path | None, *, browser: bool = True) -> dict[str, Any]`

- [ ] **Step 1: 编写测试用例 `tests/test_daemon_manager.py`**
  测试包括：未 setup 时抛出稳定错误码、正常启动写入状态文件、重复启动幂等性检测、正常 stop 逆序杀死进程组并清理状态文件、陈旧 PID（死进程）自动清理自愈。

- [ ] **Step 2: 运行测试验证失败**
  Run: `pytest -q tests/test_daemon_manager.py`
  Expected: FAIL (ModuleNotFoundError: No module named 'fireclaw_core.infra.daemon_manager')

- [ ] **Step 3: 实现 `src/fireclaw_core/infra/daemon_manager.py`**
  实现 `DaemonState`、PID 存活检查、独立会话（`start_new_session=True`）子进程派生、日志输出重定向、Gateway 就绪轮询、SIGTERM/SIGKILL 优雅关停与状态落盘。

- [ ] **Step 4: 运行测试验证通过**
  Run: `pytest -q tests/test_daemon_manager.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 2: CLI 集成与命令注册（`start` / `stop` / `open`）

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Modify: `src/fireclaw_core/__main__.py`
- Test: `tests/test_user_lifecycle_cli.py`

**Interfaces:**
- Consumes:
  - `DaemonRuntimeManager` from `fireclaw_core.infra.daemon_manager`
- Produces:
  - `handle_start(args) -> int`
  - `handle_stop(args) -> int`
  - `handle_open(args) -> int`
  - CLI parser 支持 `fireclaw start`, `fireclaw stop`, `fireclaw open`

- [ ] **Step 1: 编写 CLI 行为测试 `tests/test_user_lifecycle_cli.py`**
  测试包括：`fireclaw start --json` / `--profile`、`fireclaw stop`、`fireclaw open --no-browser`，以及在终端中的人类可读输出与退出码。

- [ ] **Step 2: 运行测试验证失败**
  Run: `pytest -q tests/test_user_lifecycle_cli.py`
  Expected: FAIL (argument error or unrecognized subcommand)

- [ ] **Step 3: 修改 `src/fireclaw_core/mission/mission_cli.py`**
  添加 `start`、`stop`、`open` 子解析器，实现相应的 handler 函数，并对接 `DaemonRuntimeManager`。

- [ ] **Step 4: 运行测试验证通过**
  Run: `pytest -q tests/test_user_lifecycle_cli.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 3: 顶层 CLI 帮助分层收敛（Progressive Disclosure）

**Files:**
- Modify: `src/fireclaw_core/mission/mission_cli.py`
- Test: `tests/test_cli_help_grouping.py`

**Interfaces:**
- Consumes:
  - `argparse.HelpFormatter` / `argparse.ArgumentParser`
- Produces:
  - 分类格式化的 `fireclaw --help`（突出 Core Commands，折叠 Advanced Commands）
  - 全量格式化的 `fireclaw --help --all`

- [ ] **Step 1: 编写帮助分层测试 `tests/test_cli_help_grouping.py`**
  测试包括：默认 `fireclaw --help` 输出包含 `Core Commands` 且仅突出 5 个核心命令（`setup`, `start`, `open`, `status`, `stop`），同时标明 `--all`；`fireclaw --help --all` 输出全部 20+ 子命令。

- [ ] **Step 2: 运行测试验证失败**
  Run: `pytest -q tests/test_cli_help_grouping.py`
  Expected: FAIL

- [ ] **Step 3: 实现自定义 HelpFormatter 或分类 Action**
  在 `src/fireclaw_core/mission/mission_cli.py` 中重构 ArgumentParser 的 help 输出逻辑，实现清晰的分组展示与 `--all` 支持。

- [ ] **Step 4: 运行测试验证通过**
  Run: `pytest -q tests/test_cli_help_grouping.py`
  Expected: PASS

- [ ] **Step 5: 暂存或提交改动**

---

### Task 4: 端到端验证、全量回归与文档更新

**Files:**
- Modify: `README.md`
- Modify: `docs/getting-started/first-run-setup.md`
- Test: 全量 pytest 与 fault-test

- [ ] **Step 1: 更新文档**
  在 `README.md` 与 `first-run-setup.md` 中更新极简小白路径：`fireclaw setup -> fireclaw start -> fireclaw status -> fireclaw open -> fireclaw stop`。

- [ ] **Step 2: 运行全量测试回归**
  Run: `pytest -q`
  Expected: 全量用例通过（>2150 passed）

- [ ] **Step 3: 运行 P1 故障矩阵回归**
  Run: `fireclaw fault-test run --live-ros`
  Expected: 7/7 passed

- [ ] **Step 4: E2E 隔离验证**
  在隔离临时目录中执行完整流水线：
  ```bash
  FIRECLAW_HOME=/tmp/fireclaw-e2e fireclaw setup --mode simulation --json
  FIRECLAW_HOME=/tmp/fireclaw-e2e fireclaw start --json
  FIRECLAW_HOME=/tmp/fireclaw-e2e fireclaw status --json
  FIRECLAW_HOME=/tmp/fireclaw-e2e fireclaw open --no-browser --json
  FIRECLAW_HOME=/tmp/fireclaw-e2e fireclaw stop --json
  ```
