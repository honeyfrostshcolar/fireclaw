# FireClaw Plugin SDK

FireClaw 的原生 Python Plugin 在同一个可信进程中被加载，但 Plugin 不应依赖
Agent Runtime 或 Plugin Host 的内部实现。公开入口是：

```python
from fireclaw_plugin_sdk import PluginApi, ToolSpec


def register(api: PluginApi):
    api.register_tool(
        ToolSpec(
            name="scan_status",
            description="Read the current scan status.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=read_scan_status,
            effect="read",
            roles=("robot_agent",),
            modes=("simulation", "real"),
        )
    )
```

## 边界

`ToolSpec` 是公开、与宿主实现无关的描述，包含：

- 工具名称和说明；
- 输入 JSON Schema；
- 插件自己的 handler；
- effect、角色、部署模式；
- 超时、输出大小和元数据限制。

插件不需要导入以下内部模块：

```text
fireclaw_core.agent.tool_runtime
fireclaw_core.plugin.plugin_host
fireclaw_core.policy.deployment
```

FireClaw 在 `FireClawPluginApi.register_tool()` 边界把 `ToolSpec` 转换成内部
`AgentTool`，随后继续使用现有的角色/模式投影、安全策略、精确授权和审计链。
因此 SDK 只负责描述 Tool，不能绕过宿主的安全门。

## 物理 Tool

会调用 ROS、机器人 SDK 或物理算法的能力使用 `PhysicalToolSpec`：

```python
from fireclaw_plugin_sdk import PhysicalToolSpec


def register(api: PluginApi):
    runtime = api.services["my_robot_runtime"]
    api.register_physical_tool(
        PhysicalToolSpec(
            plugin_id=api.id,
            name="scan_area",
            label="Scan area",
            description="Run the bounded area-scan algorithm.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            action="scan_area",
            handler=lambda arguments, **lifecycle: runtime.scan(
                arguments,
                **lifecycle,
            ),
            domain="perception",
            safety_class="sensor_operation",
            required_sensors=("lidar",),
        )
    )
```

`PhysicalToolSpec` 的 handler 属于插件。核心只把它转换为内部生命周期对象，
不再通过 `getattr(robot, "scan_area")` 查找同名 Adapter 方法。旧的同名方法绑定
仍保留为迁移兼容路径，但新增插件不得依赖它。

## 依赖安装

第三方 Plugin 仍然需要一个兼容版本的 `fireclaw_plugin_sdk`。当前 SDK 随
FireClaw Python distribution 发布，Plugin 可以在自己的 `pyproject.toml`
中声明兼容的 FireClaw 版本；未来可以将 SDK 单独发布为独立 wheel，而不改变
`register(api)` 和 `ToolSpec` 合同。

ROS、机器人 SDK 或算法依赖由 Plugin 自己声明，例如导航 Plugin 还需要 ROS1
运行时和 `move_base` 工作空间。SDK 不会替 Plugin 启动进程，也不授予宿主命令
执行权限。

## 兼容性

旧的第一方调用仍可以直接传入内部 `AgentTool`，用于迁移期间的兼容。新的
Manifest Plugin 应只使用 `fireclaw_plugin_sdk`。`fireclaw_core.navigation`
中的旧 move_base 注册函数也只是兼容入口，新的代码应通过 manifest 加载插件。
