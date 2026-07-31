# FireClaw OpenClaw-style Plugin Extension Loader

## 时间

2026-07-31

## 任务目标

让 FireClaw 像 OpenClaw 一样只负责扫描、读取和激活插件清单；插件提供方
自己提供 entrypoint、Tool schema、Runtime/ROS Adapter、Skill workflow 和
插件配置校验。新增导航、扫描、覆盖搜索等能力不再需要改 Gateway 核心或为
每个插件增加硬编码注册分支。

## OpenClaw 对照

本次用 CodeGraph 检查了：

- `openclaw/src/plugins/plugin-api.types.ts:193` 的 `registerTool` 注入式 API；
- `openclaw/src/plugins/registry.ts:27` 的统一注册表和贡献所有权；
- `openclaw/src/plugins/discovery.ts:71,1501,1527` 的候选插件发现；
- `openclaw/src/plugins/manifest.ts:127` 的 manifest-first 校验；
- `openclaw/src/plugins/loader-runtime-candidate.ts:76` 的 entrypoint 导入和
  `register` 激活；
- `openclaw/src/gateway/server-plugins.ts:575` 的 Gateway 插件加载入口；
- `openclaw/src/plugins/install-package.ts:84,191,226` 的安装/来源校验。

OpenClaw 也不是把任意 GitHub 仓库自动变成工具；插件必须提供 manifest、入口
和符合 API 的注册函数。FireClaw 复用该形状，额外保留 ROS、安全门、真实模式
审批和 Robot Capability Policy。

## 已修改/新增

- `src/fireclaw_core/plugin/extension_loader.py`
  - 读取 `fireclaw.plugin.json`，限制 manifest 大小、插件数量和一层扫描深度；
  - 校验 ID、API 版本、相对 entrypoint、普通文件、符号链接和 trust class；
  - disabled 插件不导入；
  - entrypoint 在私有 package namespace 导入，支持插件内部相对模块导入；
  - 调用 provider `register(api)`，并通过 `FireClawPluginHost` 原子提交/失败回滚；
  - 记录 discovery/activation diagnostics、插件来源和 Tool IDs。
- `src/fireclaw_core/plugin/__init__.py`
  - 导出通用发现/加载 API。
- `src/fireclaw_core/gateway/gateway.py`
  - Robot task 路径改为通用 `load_fireclaw_extensions(...)`；
  - Gateway 不再导入或调用 move_base-specific registration function；
  - 传入 mode、role、adapter、robot 和通用 plugin config/services。
- `src/fireclaw_core/gateway/config.py`、`fireclaw.example.toml`
  - 增加 `[plugins].paths` 和 `[plugins.config."plugin-id"]`；
  - 旧 move_base Gateway 字段保留为兼容迁移入口，新配置应使用 manifest ID。
- `extensions/navigation-move-base/`
  - 新增 `fireclaw.plugin.json`；
  - `plugin/entrypoint.py` 自己选择后端、读取自己的配置并注册六个导航 Tool；
  - `plugin/move_base.py` 现在拥有 move_base 参数目录、策略、ROS1/InMemory
    backend 和 Tool contract；
  - `src/fireclaw_core/navigation/move_base_plugin.py` 变为只转发到扩展实现的
    legacy compatibility shim。
- `extensions/_template/`
  - 增加 manifest 和 provider entrypoint 模板。
- 文档：`docs/architecture/extension-loader.zh-CN.md`、
  `docs/architecture/plugin-host-agent-harness.md`、根 README 和 extension
  README 已记录 manifest/entrypoint 流程。

## 当前安全边界

- manifest 只描述身份和入口，不授予宿主命令权限；
- trusted 插件当前在 FireClaw 进程内执行，适用于显式安装的本地扩展；
- sandboxed 插件只能贡献带 Docker execution boundary 的 Tool；
- Tool 仍须通过 role/mode、Robot Capability Policy、SafetyGate 和精确执行
  授权；
- 第三方签名验证和独立插件进程尚未实现，记录为后续安全加固，不应把
  `trusted` 当成不可信代码沙箱。

## 验证

- `tests/test_extension_loader.py`、`tests/test_move_base_navigation_plugin.py`、
  `tests/test_plugin_host.py`、`tests/test_cli.py`：37 passed；
- Gateway structured/dry-run/approval/embodied 集成测试：14 passed；
- 全量：2006 passed，7 skipped，退出码 0（约 2 分 55 秒）；
- `git diff --check`：通过；
- `fireclaw.example.toml`：可被 TOML loader 正常解析。

## 当前问题/下一步

1. 插件代码仍是 trusted in-process；若要允许第三方仓库，应补 manifest 来源、
   签名/摘要、文件权限和独立进程或 Docker 运行时校验。
2. manifest 的 `config_schema` 目前只做结构类型校验；可增加与 OpenClaw 类似
   的 JSON Schema 实例校验，但插件自己的字段仍应由插件拥有。
3. Plugin loader 当前在 Robot Gateway 任务路径使用；Mission Agent 的可用工具
   集合可在需要时接入同一 loader，不应再新增第二套注册机制。
4. TurtleBot3 Gazebo 中应实测 `move_base` status、参数读取、仿真调参、cancel
   和 clear costmap 的端到端行为。

## 2026-07-31 16:46 SDK 边界重构

### 任务目标

解决第三方 Plugin 必须直接导入 `fireclaw_core` 内部 AgentTool、PluginHost
和 DeploymentPolicy 类型的问题。目标是采用 OpenClaw 类似的公开 SDK 边界：
插件依赖稳定的 SDK 合同，宿主在注册边界转换成内部运行模型。

### 已完成

- 新增 `src/fireclaw_plugin_sdk/`：
  - `ToolSpec`：与宿主内部实现无关的 LLM Tool 描述；
  - `PluginApi`：Tool-only entrypoint 的静态 Protocol；
  - `DeploymentMode`、`AgentRole`、`ToolEffect`、`ToolHandler` 公共类型；
  - Tool 输入 schema、角色/模式、effect、超时、输出大小和 metadata 的 API v1
    校验；拒绝 `physical` Tool effect，物理能力仍必须走 Skill/Capability 链。
- 新增 `src/fireclaw_core/plugin/sdk_adapter.py`：只在宿主注册时把 `ToolSpec`
  转换为内部 `AgentTool`，避免公开 SDK 反向依赖核心；旧 AgentTool/测试 Tool
  仍保持兼容。
- `FireClawPluginApi.register_tool()` 现在接受 `ToolSpec`，先经过转换再进行
  原子注册、冲突检查、所有权记录和后续 AgentToolRuntime 投影。
- `extensions/navigation-move-base/plugin/move_base.py` 改用
  `fireclaw_plugin_sdk.ToolSpec` 和 SDK `DeploymentMode`，不再导入
  `fireclaw_core.agent.tool_runtime`、`fireclaw_core.plugin.plugin_host` 或
  `fireclaw_core.policy.deployment`。
- 旧 `register_move_base_navigation_plugin()` 移到
  `src/fireclaw_core/navigation/move_base_plugin.py` 兼容层；新的 manifest
  entrypoint 不再携带宿主 Host 类型依赖。
- 更新 extension 模板、导航 Plugin README、extensions README、
  `docs/architecture/plugin-sdk.zh-CN.md` 和 Plugin Host 架构文档。
- 新增 `tests/test_plugin_sdk.py`，并增加 manifest loader 加载只导入
  `fireclaw_plugin_sdk` 的第三方样例测试。

### 验证

- 聚焦：`20 passed`（SDK、extension loader、move_base、Plugin Host）；
- 完整（允许 Gateway 绑定本机临时端口）：`2010 passed, 7 skipped`，176.20s；
- `git diff --check`：通过；
- `fireclaw_plugin_sdk` 被 setuptools `find_packages('src')` 正确发现；
- 导航插件和模板未再包含 `from fireclaw_core` / `import fireclaw_core`。

### 当前结论

第三方 Python Plugin 仍需要安装兼容的 `fireclaw_plugin_sdk`（当前随 FireClaw
distribution 发布），但不需要依赖核心内部模块或修改 Gateway。ROS、SDK 和
算法依赖由插件自己声明。`trusted` 插件仍然是同进程执行，不等于不可信代码
沙箱；独立 Plugin 进程、签名和来源校验仍是后续安全工作。

### 下一步

1. 为 SDK 建立独立发布/版本兼容策略（可从 FireClaw distribution 拆出 wheel）。
2. 继续把物理能力、Service、Hook 等其他贡献类型逐步抽成公开 SDK contracts。
3. 在 TurtleBot3 仿真中验证 SDK 迁移后的导航 status、参数调节、cancel、clear
   costmap 端到端行为。

## 2026-07-31 任务工具全面插件化迁移

### 用户要求

用户指出：此前已经要求 Agent Tool 和物理 Tool 都由插件提供，不能每增加一个
能力就修改 Gateway 或 `Ros1RobotAdapter`。本次要求修正遗留的物理能力绑定，并
全面检查核心注册路径。

### 已完成

- 新增公开 SDK `PhysicalToolSpec`、`TaskInputBindingSpec` 和
  `PluginApi.register_physical_tool()`；插件 handler 可以返回 JSON 对象，不需要
  导入 `RobotActionResult`。
- Host boundary 将 `PhysicalToolSpec` 转换为内部兼容的
  `PhysicalSkillPlugin`，`skill_from_physical_plugin()` 优先使用插件提供的
  `action_handler`，不再要求 Adapter 存在同名方法。
- move_base Navigation Plugin 新增物理 `navigate_to_point` Tool：
  `InMemoryMoveBaseBackend` 和 `Ros1MoveBaseBackend` 自己实现目标发送、反馈、
  取消和结果归一化。核心 `Ros1RobotAdapter.navigate_to_point()` 不再是导航
  Plugin 的执行入口。
- `FireClawAgent` 支持在构造阶段通过统一 Extension Loader 加载插件并绑定物理
  能力；Gateway 和 CLI 都把插件路径、ROS backend、sandbox 和兼容 dispatcher
  作为服务注入。
- 新增 `extensions/computer-tools` 和 `extensions/ros1-diagnostics` 内置插件，
  将原来位于核心模块的 LLM Tool schema 迁入插件。核心旧模块只保留兼容桥接。
- 新增 `extensions/robot-legacy-physical`，承载现有 legacy
  `navigate_to_floor`、victim search/assessment、status report 和 safe-zone
  Physical Tool 合同；核心旧 `builtin_physical_skills.py` 已降级为从 manifest
  加载的兼容投影，不再创建具体能力定义。
- Gateway 不再直接调用 `register_computer_tool_plugin()` 或
  `register_ros1_diagnostic_tool_plugin()`；这些能力由 manifest loader 发现。

### 验证

- `tests/test_plugin_sdk.py`: 3 passed，包括一个没有 Adapter 同名方法的物理
  Plugin handler；
- `tests/test_move_base_navigation_plugin.py`: 5 passed，包括导航物理 Tool 由
  extension 注册并调用 InMemory move_base backend；
- ROS diagnostics、Agent Tool、Deployment Tool 集成：30 passed；
- 执行、skill metadata、inventory、safety、capability policy（除一条旧 owner
  ID 断言）通过；旧断言期待 `fireclaw.navigation.point`，新插件审计 owner 为
  `fireclaw.navigation.move-base`，行为变化是有意的。

### 当前兼容残留

- 各 Robot Adapter 中的同名物理方法和 `RobotAdapterActionBackend` 自动
  `getattr` 仍保留给旧 checkpoint、直接 Adapter 测试和旧调用方；新插件路径
  不依赖这些方法。下一步可以在迁移完旧调用方后删除这些方法。
- `*.skill.json`、`register_agent_tool()` 等仍是旧兼容注册 API；它们不应成为
  新 ROS/物理能力的来源。

### 下一步

1. 让 `RobotAdapterActionBackend` 关闭默认同名方法扫描，改由显式兼容开关保留
   旧测试；
2. 将 legacy dispatcher 逐步替换为插件自己的 ROS/SDK Runtime，之后删除 Adapter
   中的领域方法；
3. 更新 capability-policy 的旧 owner ID 测试和文档，并在 TurtleBot3 中实测
   Plugin-owned navigate action。

## 2026-07-31 任务工具全面插件化收尾检查

### 本轮修复

- 修复 Extension Loader 的导入循环：`builtin_physical_skills` 和 ROS1 配置不再
  在 Python 模块导入阶段激活插件；插件目录改为惰性加载，避免
  `skills -> robot -> plugin -> tool_runtime -> policy -> skills` 的循环导致
  `fireclaw.navigation.move-base` 被静默标记为 failed。
- `create_default_skill_registry()` 在传入空的共享 Host 时也通过统一 loader
  加载第一方扩展，保证直接构造 `FireClawAgent` 和 Gateway 使用同一路径。
- 统一 legacy adapter mode（`dry_run/mock_ros1/mock_ros2` 到插件使用的
  `dry-run/mock-ros1/mock-ros2`）。模拟和 mock adapter 现在由导航插件提供的
  `AdapterDispatchMoveBaseBackend` 调用注入 dispatcher；Tool 仍由导航插件拥有，
  但旧模拟测试的命令记录、topic 输出和行为保持兼容。
- `PhysicalToolSpec.plugin_id` 必须与注入的 Plugin API 身份一致；Host 拒绝伪造
  owner，避免 capability policy/audit 记录被第三方插件冒用。
- 核心 Agent Tool bridge 改为仅在兼容函数中动态转换 `AgentTool`，不再在
  computer/ROS diagnostic bridge 模块导入阶段依赖内部 Tool 类。
- 术语文档的导航图改为“Plugin-owned ROS/SDK Adapter”，明确不是核心
  `Ros1RobotAdapter` 提供导航 Tool。
- capability-policy 旧 owner 断言改为 canonical
  `fireclaw.navigation.move-base`；插件加载测试不再依赖目录排序。

### 边界扫描结论

- `src/fireclaw_core` 中没有具体 `AgentTool(...)` 或 `PhysicalToolSpec(...)`
  定义；唯一 `AgentTool(...)` 是 SDK -> 宿主内部模型的通用转换器。
- 所有具体 Tool schema、handler、导航/ROS/电脑能力均位于
  `extensions/*/plugin/`；这些 provider 不导入 `fireclaw_core`，只依赖公开
  `fireclaw_plugin_sdk`。
- 核心保留的 `PhysicalSkillPlugin`、`SkillRegistry`、Policy、Safety、Action
  Runtime 是通用生命周期/投影类型，不是领域能力定义。
- 各 Robot Adapter 的同名领域方法、`getattr(robot, action)` 和
  `*.skill.json` 仍是显式标注的兼容路径；新插件不得依赖它们。生产
  `FireClawAgent` 已将 `RobotAdapterActionBackend.auto_register_legacy_actions`
  设为 false。

### 验证

- `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q`：`2013 passed,
  7 skipped`，退出码 0，约 2 分 57 秒（Gateway 临时端口测试使用已批准的
  提权环境运行）。
- `tests/test_plugin_sdk.py`、`tests/test_move_base_navigation_plugin.py`、
  `tests/test_extension_loader.py`、`tests/test_capability_policy.py`：聚焦测试
  通过。
- `python -m compileall -q src/fireclaw_core src/fireclaw_plugin_sdk
  extensions/*/plugin`：通过。
- `git diff --check`：通过。

补充：随后将 `robot_agent` 的 supplemental Tool catalog 也改为惰性读取，避免
核心模块导入阶段激活 Plugin；提权运行 Gateway/Robot Agent 相关回归为
`48 passed in 8.79s`，沙箱内不提权运行 Gateway 会因本环境禁止创建 socket 而
得到 `PermissionError`，不属于代码失败。

### 当前结论与后续

本次迁移已经满足“新增 Agent Tool/物理 Tool 只需新增插件，不需修改核心
Gateway 或 `Ros1RobotAdapter`”的架构要求；旧领域 Adapter 方法只为历史任务和
测试保留。下一步若要进一步收紧边界，应迁移剩余 legacy action/checkpoint 后，
删除兼容 dispatcher 和 Adapter 领域方法，并为第三方插件增加签名、摘要和独立
进程/Docker 隔离。

## 2026-07-31 19:14 归档与提交准备

### 任务目标

用户确认本轮插件解耦工作完成，要求先存档，再将当前 FireClaw 变更提交并推送。
归档内容覆盖：公开插件 SDK、统一 Extension Loader、插件拥有的 Agent Tool 与
Physical Tool、导航/电脑/ROS 诊断/legacy 物理能力迁移、核心兼容桥接、测试和
安全边界。

### 当前状态

- 当前分支：`master`；远端：`origin`（`git@github.com:honeyfrostshcolar/fireclaw.git`）。
- 工作区包含此前任务累计的 Mission/Robot 生命周期、终态传播、Gateway、安全
  加固、上下文、插件系统及测试变更；本次提交按用户要求统一收录，不回滚已有改动。
- 具体领域 Tool schema、handler、导航/ROS/电脑 Runtime 位于 `extensions/*/plugin/`；
  核心只提供 SDK 转换、注册、策略、执行生命周期和兼容入口。
- 兼容残留已明确标注：`RobotAdapter` 领域方法、legacy dispatcher、`*.skill.json`
  沙箱路径和旧注册 API；生产 `FireClawAgent` 不再自动扫描 legacy action。

### 已验证结果

- `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q`：`2013 passed, 7 skipped`。
- Gateway/Robot Agent 聚焦回归：`48 passed in 8.79s`（提权环境）。
- `python -m compileall -q src/fireclaw_core src/fireclaw_plugin_sdk extensions/*/plugin`：通过。
- `git diff --check`：通过。

### 提交/推送状态

本记录写入时尚未执行 `git add`、commit 和 push；提交前需再次检查 staged
变更清单，提交后将记录 commit hash 和远端结果（如需追加，应避免遗漏当前
工作区中的本记忆文件）。
