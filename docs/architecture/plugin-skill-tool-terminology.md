# Plugin, Skill, Tool, Runtime, and Adapter Terminology

日期：2026-07-29

本文是 FireClaw 对插件、技能、工具和算法运行时的权威术语规范。README、
架构文档、新增 API、用户界面和后续 Agent 工作必须遵循本文。

## OpenClaw 语义基线

OpenClaw 将 Skill 和 Tool 明确分开：

- Skill 以 `SKILL.md` 为主要载体，内容会作为能力说明、工作方法和操作流程
  注入 Agent 上下文；
- Tool 通过插件 API 的 `registerTool()` 注册，是带有参数 schema 的原子可调用
  操作；
- Plugin 是安装、激活、所有权、冲突处理和生命周期边界，可以同时注册多个
  Tool 并附带一个或多个 Skill。

例如 OpenClaw 的 Tavily Plugin 注册 `tavily_search` 和
`tavily_extract` 两个 Tool，同时附带一个 Tavily Skill，说明什么时候使用
普通搜索、深度搜索、内容提取，以及如何把多个 Tool 组合为研究流程。

因此 Skill 不是 Tool 的别名，也不是每个函数的包装名称。

## Canonical Terms

### Plugin

中文：插件、能力插件或扩展包。

Plugin 是可安装、可启用、可禁用、可升级、可审计的软件交付和生命周期单元。
一个 Plugin 可以贡献：

- 多个 Tool；
- 一个或多个 Skill；
- Adapter、Service、Hook、Context Engine；
- ROS/SDK 配置和算法 Runtime；
- 测试、launch 文件和部署元数据。

Plugin 不是某一次工具调用，也不等于某一个 Skill。

### Skill

中文：技能、能力说明或可复用工作流。

Skill 是 Agent 面向某一类任务的操作知识和工作方法。它可以包含：

- 适用条件和目标；
- 应选择哪些 Tool；
- Tool 的调用顺序和参数策略；
- 观察、反馈和完成判断；
- 失败恢复、降级和升级规则；
- 领域约束和禁止事项。

Skill 可以协调多个 Tool，也可以只提供知识而不新增 Tool。Skill 的粒度应由
“是否形成一个连贯、可复用的任务方法”决定，而不是由函数数量决定。

### Tool

中文：工具或原子操作。

Tool 是 LLM 可以提出调用的最小受类型约束操作，例如：

- `navigate_to_point(x, y, yaw, frame_id)`；
- `get_navigation_status()`；
- `cancel_navigation()`；
- `scan_region(region_id, mode)`。

Tool 声明输入/输出 schema、权限、风险、资源、超时和执行绑定。LLM 只能提出
Tool 调用；宿主仍负责 capability policy、Safety Gate、授权和执行。

### Runtime / Algorithm

中文：运行时、算法实现或执行引擎。

Runtime/Algorithm 真正完成计算和控制，例如：

- ROS1 `move_base`；
- ROS2 Nav2；
- 覆盖搜索规划器；
- 目标检测/分割节点；
- CUDA 或独立 conda 进程；
- 机器人厂商 SDK。

Runtime 不应依赖 Mission Planner、Agent Loop 或 LLM Provider。

### Adapter

中文：适配器。

Adapter 是可信的协议转换边界。它负责：

- 把已通过审核的 Tool 参数转换为 ROS action/service/topic、SDK 或进程请求；
- 归一化 feedback、结果、错误、超时和取消；
- 隔离算法依赖和硬件实现；
- 保持 Tool 语义稳定，使 Runtime 可以替换。

## Navigation Example

```text
move_base Navigation Plugin
├── Navigation Skill
│   ├── 目标选择和可达性检查
│   ├── 导航、监控、取消和恢复流程
│   └── 堵塞、超时和状态不确定时的处理规则
├── navigate_to_point Tool
├── move_base_navigation_status Tool
├── move_base_cancel_navigation Tool
├── move_base_clear_costmaps Tool
├── Plugin-owned ROS/SDK Adapter
└── move_base Runtime
```

日常表达统一为：

> 机器人具有导航 Skill；move_base Navigation Plugin 提供该 Skill 并注册
> 导航 Tools；这些 Tools 通过插件自己的 Adapter 由 move_base Runtime 执行。

`navigate_to_point` 是 Tool，不应再被描述成一个完整的导航 Skill。

## Repository Layout

```text
extensions/
  navigation-move-base/
    fireclaw.plugin.json
    plugin/
    tools/
    runtime/
    ros_ws/src/
    config/
    launch/
    tests/
    skills/navigation/SKILL.md

skills/
  <skill-name>/SKILL.md
```

- `extensions/` 保存插件、工具实现、算法、ROS package、Adapter、配置和测试；
- Plugin 可以在自己的 `skills/` 子目录附带 Skill；
- 根目录 `skills/` 保存仓库级或用户级 Agent 工作流；
- 算法实现不应放在根目录 `skills/`。

## FireClaw Legacy Identifiers

以下现有名称形成兼容接口，但其实际语义比名称更接近 Tool：

| Legacy identifier | Canonical meaning |
|---|---|
| `Skill` | executable Tool definition |
| `SkillRegistry` | executable Tool registry/projection |
| `PhysicalSkillPlugin` | physical Tool contribution definition |
| `skill.started/succeeded/failed` | legacy physical Tool lifecycle event |
| `required_skills/allowed_skills` | legacy task Tool allow/require lists |

这些名字不能作为新概念设计的依据。后续应通过兼容别名、序列化迁移和事件
版本升级逐步修正，不能直接全局重命名并破坏已有 checkpoint、任务合同、配置
和审计日志。

旧的 executable manifest 与 workspace Tool loader 已经删除。可执行 Tool 必须
由带 `fireclaw.plugin.json` 的 Plugin 注册；进程类 Tool 还必须进入受策略约束的
`ComputerSandbox`。真实机器人 Tool 必须由受信任 Plugin handler 连接 Adapter
或 ROS/SDK Runtime。

在迁移完成前：

- 代码引用 legacy identifier 时保留原拼写；
- 文档必须说明它实际代表 Tool；
- 新增用户可见术语遵守本文；
- 不再新增一个原子动作就称其为新的 Skill。

## Non-Examples

错误：

- “`cancel_navigation` 是一个导航 Skill。”
- “把 move_base 算法放进 `skills/` 就会自动注册。”
- “Plugin 和 Skill 是同一个东西。”

正确：

- “`cancel_navigation` 是 Navigation Skill 可使用的一个 Tool。”
- “move_base 算法位于 Navigation Plugin 的 Runtime/ROS workspace。”
- “Navigation Plugin 可以附带 Navigation Skill 并注册多个导航 Tool。”
