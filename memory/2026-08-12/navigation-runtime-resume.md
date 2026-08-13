# Navigation Runtime 部署续接核对

## 2026-08-12T15:39:12+08:00 — 恢复上下文并确定下一切片

### 任务目标

续接 2026-08-11 的 Navigation Plugin Runtime 统一部署与 TurtleBot3 Burger Gazebo
演示工作，确认当前代码/工作树状态，并判断今天下一步应优先完成什么。本次只做恢复、
只读核对与决策记录，没有修改业务代码、启动 ROS/Gazebo、执行部署 apply、调用 Provider、
commit 或 push。

### 已读取与检查

- 最近两个日期目录：`memory/2026-08-12/`、`memory/2026-08-11/`；
- `memory/2026-08-11/embodied-evaluation-handoff.md`；
- `memory/2026-08-11/navigation-runtime-lifecycle.md`；
- Git branch/status/log 与 `git diff --stat`、`git diff --check`；
- 通过 CodeGraph 检查 `src/fireclaw_core/__main__.py::main`、
  `load_robot_capability_profile`、Runtime deployment Profile 解析及生成 Gateway 配置相关流；
- 定向核对 `pyproject.toml`、`src/fireclaw_core/deployment/deployer.py` 和相关测试。

### 当前 Git 状态

```text
branch  agent/embodied-evaluation-collision-calibration
HEAD    2eff71c docs(eval): record clean planning baseline
remote  origin/agent/embodied-evaluation-collision-calibration
```

Runtime deployment、Navigation Plugin、TurtleBot3 robot package、文档、样板和测试仍是本地未提交
改动；`git diff --check` 通过。不要覆盖或拆散这些现有改动，也不要在未经用户明确要求时
commit/push。

### 本次实证结果

1. 安装环境中的 console script 仍失败：

   ```text
   /home/lpp/miniconda3/envs/py310/bin/fireclaw --help
   ModuleNotFoundError: No module named 'fireclaw_core.mission_cli'
   ```

   原因仍是 `pyproject.toml` 的 `[project.scripts]` 指向已经不存在的
   `fireclaw_core.mission_cli:main`，而当前统一分发入口是
   `fireclaw_core.__main__:main`。

2. 源码入口和只读 deployment plan 正常：

   ```text
   PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python \
     -m fireclaw_core deploy plan --profile fireclaw.example.toml
   ```

   结果 `status=ready`，选择 `system_ros1`，fingerprint 为
   `a1b5ed133d0825995f6f6c4bb52666595266dbcbef00b282c644bc0da8d74691`。
   `plan` 是只读操作，本次没有创建/切换 release。

3. Robot Profile 相对路径缺口仍存在：

   - `load_runtime_deployment_profile` 已按 Profile 文件目录解析 deployment paths；
   - `load_robot_capability_profile` 仍直接执行
     `Path(robot.data_dir)`，并原样保留 `robot.ros1_config`；
   - 根 `fireclaw.example.toml` 中两者均为相对路径；
   - 生成的 `fireclaw-gateway` 传入绝对 Profile 路径，但 Gateway 进程若从 deployment state
     或其他 cwd 启动，仍可能错误解析这两个相对路径。

4. 现有 deployment test 只断言 wrapper 存在、执行 live status gate；尚未覆盖：
   - console script 指向统一入口；
   - 生成 Gateway wrapper 从任意 cwd 启动时，Robot Profile 资源路径仍正确；
   - 生成配置与原始 Profile 的路径语义一致。

### 当前结论

Navigation Runtime/Plugin/robot composition 不需要重新设计。下一切片应先修复产品启动包装，
因为这两个确定性缺口会阻止用户按最终产品方式完成演示；现在直接启动 ROS/Gazebo 只会继续
依赖已知绕行方式，不能验证发布体验。

### 下一步优先级

1. 将 console script 改为 `fireclaw_core.__main__:main`，增加通过 distribution metadata 或
   安装后脚本入口执行的回归，确保 `fireclaw deploy ...`、`fireclaw robot-gateway ...` 和
   Agent 默认路由均保持兼容。
2. 明确并实现 Robot Profile 路径合同：`ros1_config`、`data_dir` 等文件系统路径相对于
   Profile TOML 所在目录解析；绝对路径保持不变。更新现有测试中仍期待 CWD-relative Path 的
   断言，并覆盖任意 cwd。
3. 增加生成 wrapper/config 的回归：部署 release 后，从非仓库 cwd 调用 Gateway wrapper；
   可在不启动真实网络/ROS 的测试替身中检查最终解析的绝对路径与 argv。
4. 运行聚焦测试、`git diff --check`，再运行完整 suite；Gateway loopback tests 若在沙箱中因
   `PermissionError` 失败，按此前流程在获批环境重跑。
5. 修复验证通过后，再执行 TurtleBot3 产品式 live demo：`fireclaw-bringup`、
   `fireclaw-gateway`、`POST /tasks` 到 `(-1.0, -0.5, yaw=0)`，检查 task trace/events 与
   `/move_base` `SUCCEEDED(3)`，最后按安全顺序停止进程。

### 建议先运行的命令

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_robot_profile.py \
  tests/test_plugin_runtime_deployment.py \
  tests/test_mission_cli.py
git diff --check
```

### 不应在本切片扩张的范围

- 不重做 Navigation Plugin/Tool/Runtime 边界；
- 不新增 managed Plugin Service lifecycle、prebuilt/OCI provider 或 area patrol 能力；
- 不重跑付费 Provider evaluation；
- 不把部署工程描述为论文算法创新。

## 2026-08-12T16:31:17+08:00 — 产品入口、路径合同与 TurtleBot3 现场闭环完成

### 本次目标与结果

用户授权继续执行上一节建议。本切片已完成 console entrypoint 修复、Robot Profile 路径合同、
生成 wrapper/config 的任意 cwd 验证、ROS1 navigation runtime 的现场缺陷修复、完整回归和
TurtleBot3 Burger Gazebo 产品式端到端验证。没有 commit 或 push。

### OpenClaw analogue 与设计依据

- 修改前通过 CodeGraph 检查 OpenClaw package/bin 单一正式入口，以及配置加载边界的路径
  normalization 形状；FireClaw 复用“单一入口 + 在配置加载边界规范化路径”的结构。
- ROS1 节点初始化和 action 握手没有 OpenClaw 对应物；这里沿用 FireClaw 已有
  `Ros1RuntimeModule.load()` 的 `fireclaw_gateway`、`anonymous=True`、
  `disable_signals=True` 约定，并增加 embodied runtime 专属适配。

### 已实现

1. **正式 CLI 入口**
   - `pyproject.toml` 的 `fireclaw` 从已不存在的
     `fireclaw_core.mission_cli:main` 改为 `fireclaw_core.__main__:main`；
   - 增加 TOML 级回归，导入并验证目标 callable；
   - 执行 editable install 后，真实
     `/home/lpp/miniconda3/envs/py310/bin/fireclaw --help` 与
     `fireclaw deploy --help` 均可从 `/tmp` 运行。

2. **Robot Profile 路径合同**
   - `load_robot_capability_profile()` 先严格解析 Profile TOML 本身，再将
     `robot.ros1_config`、`robot.data_dir` 相对 Profile 所在目录解析；
   - 绝对路径保持绝对并 canonicalize；解析结果不再依赖 process cwd；
   - 更新 `examples/robot_profiles/gazebo_turtlebot3.toml` 的嵌套相对路径、文档和测试；
   - `tests/test_gateway_dry_run_profile.py` 的临时 Profile fixture 改用绝对 ROS1 config，避免
     fixture 无意依赖仓库 cwd。

3. **生成 deployment wrapper/config**
   - 生成 wrapper 不再调用 PATH 中的裸 `fireclaw`，而是固定使用 apply 时解析得到的
     Python interpreter 加 `-m fireclaw_core`；
   - Python interpreter 与 `DEPLOYMENT_GENERATOR_VERSION = "3"` 进入 fingerprint、inventory
     和 receipt，确保生成逻辑改变不会错误复用旧 release；
   - 生成 Gateway config 不再把 deployment `simulation/real` mode 错写成 embodied-memory
     runtime mode；
   - Gateway CLI 对显式 `None` 使用正式默认值，修复 `int(None)`；
   - wrapper 从 `/tmp` 运行并完成 9/9 ROS readiness checks。

4. **现场发现并修复的 ROS1 runtime 缺陷**
   - 首次任务 `task-67499d0598de4b2ea0f44fb104e277c2` 进入
     `action.started` 后失败，错误为
     `time is not initialized. Have you called init_node()?`；
   - `Ros1MoveBaseBackend._rospy()` 现在按核心 transport 同一约定初始化一次
     `fireclaw_gateway` ROS node，并为 Gateway worker thread 禁用 signal handlers；
   - action 与 dynamic-reconfigure client 的共享构造边界也会先完成该初始化，覆盖
     `get_status`、参数读取/修改和取消路径；
   - 第二次任务 `task-2ebb389110dd492280a26dd1adaa617d` 不再发生初始化错误，但 goal 在
     action server 握手前发送，120 秒后正确超时为 `lost`，并以
     `physical_runtime_stop_unconfirmed` 关闭资源准入；
   - `navigate_to_point()` 现在必须先 `wait_for_server()` 再 `send_goal()`；server 不可用时返回
     `move_base_unavailable`，同时明确 `runtime_stopped=true`、
     `resource_release_safe=true`，因为尚未发送物理 goal；
   - 新增节点只初始化一次、status 路径初始化顺序、握手顺序、server unavailable 不发送
     goal 的回归。

### 现场验证证据

- 原正式部署根：
  `/home/lpp/.fireclaw/deployments/gazebo-turtlebot3-burger`；最终 generator v3 release
  fingerprint：
  `5f4888b6bd0a8190dc7221374711a90b3eed20e6498ed626041fbfe95e1f9790`。
- 最终再次执行默认部署的 `status --no-runtime-check`，结果仍为 `installed`，
  `static_checks.ok=true`，无 missing/mismatched artifact。
- 原 Robot Profile 的持久化 `data_dir` 仍保留第二次 `lost` 任务触发的资源冻结；重启或换
  deployment output root 不会绕过该状态。这是故障安全行为，本次没有手工篡改/删除审计
  状态。
- 为继续验收，在 `/tmp` 创建隔离 deployment/Profile data，fingerprint：
  `57be2808635760e106ccd98e4c4885530ef25e8f4cd88012ebec163ddb103c45`。
- 生成 Gateway wrapper 从 `/tmp` 启动，readiness 9/9：system package、`/map_server`、
  `/amcl`、`/move_base`、`/scan`、`/odom`、`/move_base` action、两条 TF 均通过。
- 最终任务：`task-2b8a75df9b3b4ab39533e7d1735a83ab`，session
  `live-product-demo-20260812-final`，目标 `map (-1.5, -0.5, yaw=0)`；
  - task `completed`；execution `succeeded`；
  - `move_base` goal state `3 / succeeded`，`Goal reached.`；
  - action elapsed `3.878620306000812s`；
  - `runtime_stopped=true`，`resource_release_safe=true`；
  - AMCL 从约 `(-1.9613, -0.4980)` 到 `(-1.5237, -0.5220)`；
  - 审计闭环包含 `action.started`、多条 `action.feedback`、`resource.released`、
    `skill.succeeded`、`task.completed`。
- 结束时先停止 Gateway，再停止 bringup；`roslaunch` 依次关闭 `move_base`、`amcl`、
  `map_server`、Gazebo 和 ROS master，bringup exit code 0，没有遗留 live session。

### 主要命令与验证

```text
/home/lpp/miniconda3/envs/py310/bin/python -m pip install --no-deps --no-build-isolation -e .
/home/lpp/miniconda3/envs/py310/bin/fireclaw deploy apply \
  --profile /home/lpp/fireclaw-master/fireclaw.example.toml \
  --output-root /tmp/fireclaw-live-20260812
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_move_base_navigation_plugin.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_plugin_runtime_deployment.py
# 40 passed
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
# 2033 passed, 7 skipped in 169.49s
git diff --check
# passed
python -m py_compile <all changed Python modules/tests>
# passed
```

完整 suite 在增加最终 ROS 回归后通过；此前中间 full run 的一次 emergency-stop `lost` 是时序
flaky，隔离连续重跑通过，最终 full run 也通过，不需要无关代码修改。

### 修改文件（本切片）

- `pyproject.toml`
- `src/fireclaw_core/agent/robot_profile.py`
- `src/fireclaw_core/deployment/deployer.py`
- `src/fireclaw_core/gateway/gateway.py`
- `extensions/navigation-move-base/plugin/move_base.py`
- `examples/robot_profiles/gazebo_turtlebot3.toml`
- `fireclaw.example.toml`
- `docs/architecture/runtime-workspace-path-security.md`
- `docs/deployment/plugin-runtime-deployment.md`
- `tests/test_robot_profile.py`
- `tests/test_mission_cli.py`
- `tests/test_plugin_runtime_deployment.py`
- `tests/test_gateway_dry_run_profile.py`
- `tests/test_gateway_robot_agent_cli.py`
- `tests/test_move_base_navigation_plugin.py`

这些文件和工作树中的其他 Plugin Runtime/TurtleBot3 改动仍未提交；继续工作时先查看
`git status`，不要覆盖现有本地改动。

### 当前结论与下一步

- 工程正确性：本切片目标已完成，正式 CLI、cwd-independent Profile、生成 wrapper、ROS
  action 连接和审计闭环均有单测及现场证据。
- 研究有效性：这是部署可靠性与 safety/audit 基础设施，不构成算法新颖性；它使后续实验
  能以可复现实验入口采集可信执行证据。
- 下一步建议：先设计并实现**显式、经操作员确认且要求现场停止证据**的资源冻结恢复流程；
  不能以重启或删除 state 作为默认恢复。随后再把同一产品式路径纳入自动化 Gazebo
  acceptance test，并测量 readiness latency、goal success rate、timeout/recovery behavior。
