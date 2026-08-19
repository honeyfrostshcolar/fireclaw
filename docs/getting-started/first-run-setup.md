# FireClaw 首次设置

`fireclaw setup` 是当前源码仓库内的首次设置入口。它准备一个无凭据的 TurtleBot3 Gazebo Profile、验证
ROS 与 Navigation Plugin 依赖、生成内容寻址 Runtime release，并把该 Profile 记为当前选择。它不会启动
Gazebo、Gateway 或任何机器人动作。当前流程仍依赖完整 FireClaw 源码仓库和仓库内 ROS workspace；本页
不声称 wheel 安装后已经具备一条命令的首次仿真任务闭环。

## 仿真设置

在完整 FireClaw 仓库中运行：

```bash
fireclaw setup
```

交互模式默认推荐“仿真体验”。脚本或 CI 可以使用：

```bash
fireclaw setup --mode simulation --json
```

默认用户文件位于 `FIRECLAW_HOME`；未设置该环境变量时使用用户目录下的 `.fireclaw`。生成内容包括：

- `profiles/gazebo-turtlebot3-burger.toml`：只含确定性 planner，不写 Provider key；
- `state/active-profile.json`：只保存 Profile 路径、模式、模板版本和更新时间；
- `workspaces/gazebo-turtlebot3-burger/`：任务数据和 Agent workspace；
- `deployments/gazebo-turtlebot3-burger/`：内容寻址 Runtime release。

成功后可分别使用以下生命周期命令（可省略 `--profile`）。它们尚未被编排成自动的
setup → start → open 流程：

```bash
# 1. 启动后台守护进程与 Gateway
fireclaw start

# 2. 检查运行健康状态与 readiness
fireclaw status

# 3. 打开 Web Console 仪表盘（支持 --no-browser 仅打印 URL）
fireclaw open

# 4. 停止后台守护进程与其托管服务
fireclaw stop
```

`fireclaw stop` 只证明守护进程退出，不构成机器人已经物理停止的证据。没有 Robot Adapter/硬件回执时，
机器人物理状态必须保持 `UNKNOWN` 并由现场人员确认。

高级部署与故障恢复命令（如 `fireclaw deploy ...`、`fireclaw recover`）仍可按需使用。

## Web Console 运维控制台使用指南

通过 `fireclaw open` 打开的 Web 控制台（默认运行在 `http://127.0.0.1:8766/console`）是一个 MVP
运维界面。以下模块包含可验证的 UI/API 骨架，也有尚未闭环的产品合同；不得把页面缺省值当作实机事实：

控制台只投影 Gateway/Adapter 提供的状态，不自行证明硬件事实。尚未收到 readiness、急停或停止证据时，页面显示 `UNKNOWN`；`ready` 表示任务准入条件满足，不代表机器人已经物理停止。

### 1. 首页概览 (Overview)
- **准入状态**：显示 Mission Gateway 当前返回的 readiness 投影；缺失值统一显示 `UNKNOWN`。
- **机器人/模式/Profile**：当前 Gateway 合同尚未提供完整 active Profile 与权威运行模式，因此 Web 不填入
  TurtleBot3 或 Simulation 作为事实。
- **推荐入口**：只根据当前 Gateway 投影切换页面，不构成动作准入或物理安全判断。

### 2. 任务下发 (Task Dispatch)
- **自然语言与搜救模板**: 支持在输入框中输入自然语言指挥指令（例如“去A区搜索被困人员”），亦可一键填入常见搜救预设。
- **意图理解与计划预览 (Intent Preview Prototype)**：点击“解析任务”后显示结构化意图卡片：
  - **解析意图 (Parsed Intent)**: 系统理解的标准化任务目标；
  - **执行机器人 (Target Robot)**: 拟指派的本体 ID；
  - **预估规划步骤 (Planned Steps)**: 步骤编号与原子 Tool 序列；
  - **当前风险等级 (Risk Level)**: 安全与环境风险评估。
- **确认下发**：Web 不再自动确认，必须显式点击按钮；但当前 preview 不是 server-owned immutable
  PlanArtifact，`/tasks` 也尚未保证执行同一计划，因此这不是完整的 preview-to-execution 安全合同。

### 3. 执行监控 (Real-time Execution)
- **SSE 事件流**：控制台订阅 Gateway 事件总线。HTTP `accepted` 只显示为请求已接受；页面不再伪造
  `RUNNING`、`move_base` 或导航进度，执行状态必须来自后续权威事件。
- **严格取消三态状态机 (Strict 3-State Cancel Machine)**: 三态按证据来源推进，不根据任务终态猜测机器人运动状态：
  1. `cancel_requested` (取消请求已发送): 网关接受任务层取消请求；不表示制动已经开始；
  2. `stopping` (机器人报告正在停止): 仅由机器人运行时的 `robot.stopping` 反馈进入；
  3. `stopped_confirmed` (已确认物理停止): 仅在 `task.stopped` / `robot.stopped_confirmed` 同时携带 `physical_stop_confirmed=true` 和 `stop_evidence` 时进入。

`task.cancelled` 与 `mission.cancelled` 不是物理停止证据。未接入权威停止反馈的 Adapter 不会显示第三态；操作员应继续按停止未确认处置，必要时执行现场急停。

### 4. 恢复中心原型 (Recovery Request Prototype)

- 页面显示 Gateway 当前阻断摘要和可用诊断快照；没有证据时明确显示 `UNKNOWN`，不会补出防跌落、雷达
  距离或急停释放状态。
- 当前按钮只提交带操作员确认的 Mission Gateway 准入投影重置请求。它没有接入 Robot Gateway 的正式
  request/confirm/TTL 两阶段恢复，不确认物理停止，也不自动恢复旧任务。实机仍须走
  `fireclaw recover` 与 hardware-safety 现场流程。

### 5. 配置助手原型 (Experimental Settings Assistant)

- 5 类模板、ROS 图发现、core Schema 表单、diff、内容快照与回滚组件已经存在，但尚未形成统一的
  Plugin-manifest 权威 Schema 或可直接启动的完整 Profile。
- Web discovery 与 backend 字段合同、typed topic/action probe、保存路径边界、全量验证、原子替换、
  SecretManager 引用和 known-good 回滚仍待完成。
- Gateway 未提供 active Profile 时，Web 会拒绝猜测保存和回滚目标。即使内容快照写入成功，也只表示
  内容与哈希已记录，不证明 Profile 可启动或满足实机安全要求。

### 6. 友好错误提示与操作员处置引导 (Friendly Error Guidance)

已接入 FriendlyError 的路径使用 4 段式提示，并可按需展开底层技术详情：
- **4 段式标准化结构**:
  1. ❶ **发生了什么**: 异常原因及受影响的传感器/主题/节点；
  2. ❷ **机器人安全证据**：当前固定为 `UNKNOWN`，直到 typed Adapter evidence 合同完成；
  3. ❸ **自动处置回执**：当前明确表示没有可验证回执，不从错误码推断已制动、下电或安全；
  4. ❹ **建议下一步**: 操作员现场处置指引与排错建议。
- **Web Console 交互体验**:
  - **CRITICAL 严重错误**：弹出 4 段式模态框、文字建议与可折叠技术详情；建议项不代表对应 handler
    已注册或动作已执行；
  - **WARNING 警告**: 弹出双行增强 Toast 提示（含安全状态小字与 `查看详情 →` 跳转链接，8 秒持续展示）；
  - **INFO 提示**: 右下角轻量提示。
- **CLI 终端调试与错误字典**:
  - CLI 执行时默认输出带状态图标与中文分段的友好框；
  - 添加 `--verbose` 参数可展开技术堆栈与诊断信息；
  - 运行 `fireclaw errors list`（支持 `--category`, `--severity`, `--json`）可检索全部 40+ 条注册错误码；
  - 运行 `fireclaw errors get <error_code>` 可查看单条错误码定义与推荐处置动作。


## 中断与重复运行

setup 是幂等的：

- 已生成的 Profile 不会被覆盖；
- 已安装且 fingerprint 相同的 release 会返回 `reused=true`；
- 依赖检查失败时保留 Profile，但不会写 active profile；修复问题后运行同一命令即可续接；
- Profile 损坏、模式不一致或路径为符号链接时保持失败，不会自动“修复”成另一套配置。

只验证并记录 Profile、不生成 Runtime release：

```bash
fireclaw setup --mode simulation --no-deploy
```

这属于高级诊断用法；正常首次设置不需要该参数。

## 实机边界

首次设置不会生成真实机器人 Profile，也不会自动部署或启动实机。实机只能使用已经人工审查的 Profile：

```bash
fireclaw setup --mode real --profile /path/to/reviewed-robot.toml
```

该流程只执行非致动验证并记录当前选择。真正运行前仍必须完成 hardware-safety preflight/acceptance；
simulation Profile 不能通过修改一个 mode 字段变成可信实机配置。

setup 不读取现有 Provider API key，也不会把凭据复制进生成文件。模型密钥继续通过受控环境变量提供。
