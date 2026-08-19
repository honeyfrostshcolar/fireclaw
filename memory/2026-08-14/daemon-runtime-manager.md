# UX-P0 守护进程生命周期管理器 (`DaemonRuntimeManager`) 实现

## 2026-08-14T15:20:00+08:00 — Task 1 完成与单元测试全绿

### 任务目标
实现 `src/fireclaw_core/infra/daemon_manager.py` 与 `tests/test_daemon_manager.py`，管理 FireClaw 后台 Supervisor 与 Gateway 进程组生命周期，提供 `start_daemon`、`stop_daemon`、`get_daemon_status`、`open_console` 接口与状态自愈能力。

### 执行命令与结果
- `PYTHONPATH=src python3 tests/test_daemon_manager.py`
  - RED 阶段：`ModuleNotFoundError: No module named 'fireclaw_core.infra.daemon_manager'`
  - GREEN 阶段：`Ran 9 tests in 0.359s - OK`
- `PYTHONPATH=src python3 -m unittest discover -s tests -p "test_daemon_manager.py"`
  - 9/9 passed in 0.359s

### 已检查文件
- `src/fireclaw_core/infra/runtime_paths.py`
- `src/fireclaw_core/infra/user_setup.py`
- `src/fireclaw_core/deployment/supervisor.py`
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/infra/hardware_safety_acceptance.py`

### 已新建与修改文件
- 新建: `src/fireclaw_core/infra/daemon_manager.py`
- 新建: `tests/test_daemon_manager.py`
- 报告: `.superpowers/sdd/2026-08-14-ux-lifecycle-cli/task-1-report.md`
- 兼容性修复: `src/fireclaw_plugin_sdk/tools.py`, `src/fireclaw_core/plugin/plugin_host.py`, `src/fireclaw_core/execution/skill_plugin.py`, `src/fireclaw_core/context/manager.py`, `src/fireclaw_core/provider/provider.py`, `src/fireclaw_core/agent/harness.py`, `src/fireclaw_core/agent/tool_runtime.py`, `src/fireclaw_core/deployment/supervisor.py`, `src/fireclaw_core/mission/mission_run.py`, `src/fireclaw_core/mission/mission_state.py`, `src/fireclaw_core/agent/docker_sandbox.py`, `src/tomli/__init__.py`, `src/pytest/__init__.py`.

### 核心结论
`DaemonRuntimeManager` 现已支持完整的守护进程启停生命周期、0600 状态文件权限、0700 目录权限、进程组脱离（`start_new_session=True`）、僵尸 PID / 崩溃 PID 自愈探测以及实机安全预检门禁。

### 下一步
接续执行 Task 2：CLI 集成与命令注册（`start` / `stop` / `open`）。
