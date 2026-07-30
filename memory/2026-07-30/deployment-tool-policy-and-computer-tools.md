# Deployment Tool Policy And Computer Tools

## 2026-07-30 11:39:03 +08

### Task goal

为 FireClaw 的 `Mission Agent` 和 `Robot Agent` 增加两种明确部署模式：

- `simulation`：允许 LLM 在受约束环境中使用较丰富的计算机工具进行 ReAct
  诊断、读取、写入和进程实验；
- `real`：减少通用工具暴露，所有调用仍由 LLM 输出结构化 Tool Call，可信
  backend 决定阻断、审批或执行；物理 Skill 保持独立安全链。

用户明确要求参考 OpenClaw 的成熟架构，而不是另造一套工具调用模式。

### OpenClaw analogues inspected

- `openclaw/src/agents/agent-tools.policy.ts`
- `openclaw/src/agents/sandbox/config.ts`
- `openclaw/src/agents/agent-tools.before-tool-call.wrapper.ts`
- `openclaw/src/agents/agent-tools.execution-validation.ts`

复用的结构：

- 工具集合在交给模型前按 profile 投影；
- Tool Call 是结构化数据，不让模型直接持有执行对象；
- `before_tool_call` hook 可以阻断、要求审批或调整参数；
- hook 后的最终参数必须重新校验；
- 执行前重新解析工具所有者，拒绝 stale/replaced handler；
- 计算机进程必须在 sandbox 中运行。

FireClaw 的差异：

- 通用 `AgentTool` 禁止声明 `physical` effect；
- 导航、扫描等动作继续通过 Physical Skill、capability policy、
  `SafetyGate`、精确执行授权和 Robot Adapter；
- 通用工具结果只进入 advisory context，不能覆盖权威传感器快照。

### Implementation

新增 `src/fireclaw_core/policy/deployment.py`：

- `DeploymentProfile(mode, role, allow, also_allow, deny, sandbox)`；
- 支持 `mission_agent` 和 `robot_agent` 独立配置；
- effect 分类为 `read`、`bounded_mutation`、`process`、`physical`、
  `host_admin`、`credential_access`、`real_hardware`；
- `simulation` 硬拒绝宿主管理、凭据和真实硬件 effect；
- `real` 硬拒绝进程、宿主管理、凭据和真实硬件 effect；
- `real` 的 bounded mutation 要求精确审批；
- deny 和任何 hard block 优先于 approval；
- simulation profile 不能静默绑定非仿真的 live robot adapter。

新增 `src/fireclaw_core/agent/tool_runtime.py`：

- `AgentTool` 与 `AgentToolRuntime`；
- 从统一 `FireClawPluginHost` 读取 `tool` contribution；
- schema 校验、hook、最终参数复验、exact authorization、owner
  re-resolution、执行与完整审计；
- 精简 `exposure_manifest` 供 LLM 了解可见工具，但不会把完整策略 manifest
  塞爆上下文。

新增 `src/fireclaw_core/agent/computer_tools.py`：

- `computer_list_files`
- `computer_read_file`
- `computer_write_file`
- `computer_exec`

文件工具限制在角色 workspace。覆盖文件必须提交读取时得到的 SHA-256，写入
使用 atomic replace + fsync。进程工具只接收 argv，由 Docker 执行；无宿主
fallback，默认无网络、read-only root、drop all capabilities、
no-new-privileges、非 root UID/GID，并限制时间、输出、内存、CPU 和 PID。

新增 `containers/agent-sandbox/Dockerfile`，提供最小 Python 3.10 仿真镜像。

Mission integration：

- `LLMMissionPlanner` 将 host 已投影的 Agent Tool schemas 与 mission
  deliberation tools 一起交给模型；
- `MissionDeliberationRuntime` 新增 `execute_agent_tool` operation；
- 工具结果返回下一轮，标记 `authoritative=False`；
- 副作用工具崩溃后不自动重放；
- 每次 deliberation 最多执行 3 个通用 Agent Tool。

Robot integration：

- `RobotLLMDeliberationPolicy` 区分 `execute_agent_tool` 与
  `execute_skill`；
- `RobotDeliberationRuntime` 执行通用工具并把结果返回下一轮；
- 副作用调用需要 reconcile，结果未知时 escalate，不盲目重放；
- 每次 deliberation 最多执行 4 个通用 Agent Tool。

Gateway/config integration：

- `gateway.config.load_config` 保留结构化 `[deployment]`；
- Mission server 和 Robot Gateway 分别构造 role-specific profile；
- 只有 sandbox enabled 时注册内置 computer plugin；
- `fireclaw.example.toml` 默认 `real` 且两个 sandbox disabled；
- 启用仿真需要将 mode 改为 `simulation` 并显式启用角色 sandbox。

Documentation：

- `README.md` 新增 Deployment Tool modes；
- `docs/architecture/deployment-tool-policy.md` 记录威胁模型、流程、配置与
  当前边界。

### Commands and results

使用 Python：

```text
/home/lpp/miniconda3/envs/py310/bin/python
```

系统 `/usr/bin/python3` 是 3.8.10，不满足项目运行环境。

已运行：

```text
python -m pytest -q \
  tests/test_deployment_tool_policy.py \
  tests/test_agent_tool_runtime.py \
  tests/test_deployment_agent_tool_integration.py \
  tests/test_gateway_robot_agent_cli.py \
  tests/test_planner_builder.py
```

结果：`29 passed`。

此前针对 Robot deliberation 和相关旧模块的回归：

- Robot 相关聚焦测试：`10 passed`；
- 非网络聚焦回归：`83 passed`；
- 一次未提权的网络测试有 28 个失败，原因均为当前工具 sandbox 禁止监听
  socket (`PermissionError: Operation not permitted`)，不是代码断言失败。

### Observed issue and resolution

Mission 集成测试最初在读取文件后提交计划时被旧 `propose_plan` 的 floor
parser 拒绝。当前空间范围虽是单楼层坐标导航，但 legacy proposal 仍保留
`floor=1` 兼容字段；测试使用该字段，真实目标仍是
`target.frame_id="map" + target.pose`。没有把跨楼层导航加入 Physical Skill。

完整 Agent Tool policy manifest 一度使测试模型的 8192 token context 超限。
现改为把小型 `exposure_manifest` 交给模型，完整策略和每阶段证据只写审计。

### Current conclusion

两种模式的架构主干已经完成。两种模式都不让 LLM 直接执行宿主命令：

- simulation 给模型更多 tool schemas，由 Docker/workspace sandbox 执行；
- real 隐藏 process 等高风险工具，对 bounded mutation 要求最终参数的精确
  backend authorization；
- physical action 仍走独立机器人安全链。

### Remaining uncertainty and next recommended step

1. 运行需要 socket 权限的完整 pytest 回归，确认没有真实网络测试回归。
2. 增加 typed read-only ROS diagnostics tools：
   topic list、有限窗口 message sampling、TF lookup、move_base status 和
   navigation diagnostics；不要直接开放宿主 `rostopic` shell。
3. 把通用 Agent Tool 的 `approval_required` 持久化恢复接入现有 operator
   approval relay/Gateway API。当前循环会安全 escalate，但不会在批准后自动
   续跑该工具。
4. 在实际 Docker 环境构建 `fireclaw-agent-sim:local` 并验证
   `computer_exec` 的隔离、资源限制和无网络行为。
5. 为仿真实验固定 sandbox image digest、依赖清单和 SBOM。

## 2026-07-30 11:44:36 +08 final verification

完整回归第一次在 collection 阶段发现循环导入：

```text
execution.action_runtime
-> agent package __init__
-> agent.tool_runtime
-> policy package __init__
-> capability
-> execution.skills
-> execution.action_runtime
```

原因是 `src/fireclaw_core/agent/__init__.py` 对新 Agent Harness/Tool 类型做了
eager re-export。已改为 PEP 562 风格的 lazy `__getattr__` export：继续兼容
`from fireclaw_core.agent import AgentToolRuntime`，但导入底层
`fireclaw_core.agent.robot` 时不再提前拉入上层 policy/execution 图。

最终验证：

```text
python -m compileall -q src tests
compileall: ok

load_config("fireclaw.example.toml")
deployment_profile_from_config(..., role="mission_agent")
example config: ok

python -m pytest -q
1855 passed, 6 skipped in 156.35s

git diff --check
exit 0
```

完整 pytest 在命令沙箱外运行，仅因为 Gateway/HTTP 测试需要绑定本机临时
socket；没有使用外部网络或修改真实机器人。

更新后的下一步顺序：

1. typed ROS topic/TF/move_base diagnostic Agent Tools；
2. 通用 Agent Tool approval relay 的持久化批准后续跑；
3. 构建并实测 Docker sandbox image，固定 digest/SBOM。

## 2026-07-30 11:47 +08 authorization scope hardening

最终安全复核发现 `AgentToolRuntime` 最初只检查授权有效期和
`tool_name + final arguments`，没有比较 `VerifiedExecutionAuthorization.scope_hash`。
这可能使另一个任务中恰好相同的工具授权被复用。

已新增 `fireclaw.agent-tool-authorization-scope:v1`。审批请求和审计现在包含
scope hash，该 hash 绑定：

- deployment policy/profile、mode 和 role；
- Tool owner plugin ID；
- Tool description、input schema、effect、roles、modes 和 sandbox requirement
  形成的合同哈希；
- hook 调整后的最终 action hash；
- 可用的 mission/task/robot/snapshot/session 身份。

授权必须同时通过 expiry、scope hash 和 exact action hash 三项检查。聚焦回归
结果：`23 passed`。

## 2026-07-30 11:51:47 +08 final result after scope hardening

授权 scope 加固后重新执行完整验证：

```text
python -m pytest -q
1855 passed, 6 skipped in 155.03s

python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

本轮未 commit、未 push。
