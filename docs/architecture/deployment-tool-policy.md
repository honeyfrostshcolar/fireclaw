# FireClaw Deployment Tool Policy

本文定义 FireClaw 主控 `Mission Agent` 和机器人端 `Robot Agent` 的两种 LLM
工具部署模式。它借鉴 OpenClaw 的分层工具投影、调用前 hook、最终参数复验和
sandbox 边界，但把物理动作留在 FireClaw 独立的 Skill 安全链中。

参考过的 OpenClaw 实现：

- `openclaw/src/agents/agent-tools.policy.ts`
- `openclaw/src/agents/sandbox/config.ts`
- `openclaw/src/agents/agent-tools.before-tool-call.wrapper.ts`
- `openclaw/src/agents/agent-tools.execution-validation.ts`

## 不变原则

LLM 在两种模式下都不直接执行 shell、Python、ROS 或机器人命令。一次调用始终
是：

```text
LLM emits structured Tool Call
-> Agent Harness validates one model turn
-> Plugin Host resolves the current Tool owner
-> DeploymentProfile projects or blocks the Tool
-> input schema validation
-> before_tool_call hooks
-> adjusted arguments are validated again
-> exact authorization check when required
-> sandbox or typed runtime executes
-> result and policy evidence are audited
-> result returns to the next LLM turn
```

这意味着“仿真模式给 LLM 更多权力”是给它更多可选择的 Tool schema，而不是把
宿主 shell 对象、ROS master 句柄或机器人驱动对象交给模型。

## 模式差异

| 边界 | `simulation` | `real` |
|---|---|---|
| 读 workspace | 允许 | 仅在显式启用沙箱时允许 |
| 写 workspace | 允许，限定路径 | 需要精确后端授权 |
| 启动进程 | 仅 Docker 沙箱 | 硬拒绝 |
| 网络 | 默认 `none`，可显式改为 `bridge` | 通用进程工具不开放 |
| 宿主管理 | 硬拒绝 | 硬拒绝 |
| 凭据访问 | 硬拒绝 | 硬拒绝 |
| 直接硬件类 Agent Tool | 硬拒绝 | 硬拒绝 |
| 物理 Skill | 仍走仿真 Adapter 和安全链 | 走真实 Adapter、能力策略、安全门和执行授权 |

`deny` 的优先级高于 `allow` 和 `also_allow`。即使配置写了 `allow = ["*"]`，
模式级硬拒绝仍不能被覆盖。

## 内置计算机工具

- `computer_list_files`：列出角色 workspace 内的文件。
- `computer_read_file`：读取普通文件，并返回内容、大小和 SHA-256。
- `computer_write_file`：原子写入；覆盖已有文件时必须提交此前读取到的
  `expected_sha256`，避免 LLM 覆盖并发修改。
- `computer_exec`：接收 argv 数组，不由宿主拼接 shell 字符串；进程在 Docker
  中执行。

Docker 启动边界包含：

- role-specific workspace 是唯一读写 bind mount；
- root filesystem 只读；
- `--cap-drop ALL` 和 `no-new-privileges`；
- 非 root UID/GID；
- memory、CPU、PID、超时和输出大小限制；
- 默认 `--network none`；
- 不挂载 Docker socket、用户 home、凭据目录或真实设备；
- Docker 不可用时失败，不回退到宿主进程。

构建最小仿真镜像：

```bash
docker build \
  -t fireclaw-agent-sim:local \
  -f containers/agent-sandbox/Dockerfile \
  .
```

## 真实模式授权

真实模式的受限写入不会因为模型说“已经获得批准”而执行。宿主要求
`VerifiedExecutionAuthorization` 同时满足：

- 授权未过期；
- `scope_hash` 同时绑定 deployment profile、插件所有者、工具合同以及
  mission/task/robot/snapshot 身份；
- 授权对象包含最终 Tool 名称；
- 授权哈希与 hook 调整后的最终参数完全一致。

参数、工具合同、插件所有者或任务范围发生变化后，旧授权自动失效。当前
Mission/Robot 循环遇到
`approval_required` 会停止并上报，不会自行重试该副作用调用。通用 Agent Tool
审批后的持久化恢复入口仍需与现有 operator approval relay 继续打通。

## 与物理 Skill 的边界

`AgentTool` 构造器拒绝 `effect="physical"`。导航、扫描、覆盖搜索等机器人能力
必须作为 Physical Skill/Tool contribution 注册，并经过：

```text
identity
-> delegated task authority
-> plugin ownership
-> robot capability/profile
-> live robot state
-> SafetyGate
-> exact execution authorization
-> Adapter
```

这样，仿真中的文件诊断或参数实验可以使用通用 ReAct 工具，而真实的
`move_base` goal、取消导航和动态参数更新不会因为开启计算机工具而绕过机器人
安全边界。

## 上下文可信度

计算机工具输出统一标记为 `advisory`：

- 可帮助 LLM 读日志、查看配置、形成故障假设；
- 不能覆盖 frozen state snapshot；
- 不能伪造传感器观测、急停状态或机器人在线状态；
- 不能单独满足物理动作的 belief/evidence gate。

后续 ROS 诊断应新增 typed read-only Tool，例如 topic 列表、有限窗口的消息采样、
TF 查询和 navigation diagnostics，而不是开放宿主 `rostopic` shell。它们的输出
也必须携带时间戳、来源和 freshness。

## 配置

`fireclaw.example.toml` 包含两个角色各自的 allow/deny 与 Docker sandbox。
启用仿真计算机工具至少需要：

```toml
[deployment]
mode = "simulation"

[deployment.sandbox.mission_agent]
enabled = true
image = "fireclaw-agent-sim:local"

[deployment.sandbox.robot_agent]
enabled = true
image = "fireclaw-agent-sim:local"
```

若 Robot Gateway 使用非 dry-run Adapter，`simulation` profile 还要求
`robot_gateway.embodied_runtime_mode = "simulation"`。否则启动时直接拒绝，
防止仿真权限静默绑定真实硬件。

## 当前边界

- 只提供 workspace 文件与容器进程工具，没有声称支持任意宿主操作。
- 尚未注册 typed ROS topic/TF/navigation diagnostic Tool。
- 沙箱镜像的依赖和版本需要在仿真实验环境中固定并生成 SBOM。
- 真实模式的通用 Tool 审批恢复还未接入 Gateway API。
