# FireClaw 插件导入机制

FireClaw 采用与 OpenClaw 相同的“清单发现 + 入口激活 + 统一注册表”形状，
但保留机器人安全边界。

## 新增插件不改核心代码

插件提供方只需要放置一个目录：

```text
extensions/my-plugin/
  fireclaw.plugin.json
  plugin/entrypoint.py
  plugin/runtime.py
  runtime/fireclaw.runtime.json
  skills/my-skill/SKILL.md
```

`fireclaw.plugin.json` 只包含 data-only 发现元数据，可选引用 Runtime 描述：

```json
{
  "id": "example.scan",
  "api_version": "1",
  "entrypoint": "plugin/entrypoint.py",
  "runtime": "runtime/fireclaw.runtime.json",
  "trust_level": "trusted",
  "enabled_by_default": true
}
```

启动 Robot Gateway 后，核心流程是：

```text
[plugins].paths
  -> 读取 fireclaw.plugin.json（不执行代码）
  -> 校验 ID、API 版本、相对路径和符号链接
  -> 部署阶段可读取 typed Runtime descriptor（仍不导入 Plugin 代码）
  -> 按配置跳过 disabled 插件
  -> 导入插件声明的 entrypoint
  -> 调用 register(plugin_scoped_api)
  -> FireClawPluginHost 原子提交 Agent Tool/Physical Tool/Service 等贡献
```

`entrypoint.py` 由插件提供方实现，例如：

```python
from fireclaw_plugin_sdk import ToolSpec

def register(api):
    api.register_tool(
        ToolSpec(
            name="scan_status",
            description="Read the current scan status.",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda arguments: {"status": "succeeded"},
        )
    )
```

第三方 Python Plugin 应依赖公开的 `fireclaw_plugin_sdk` 合同，不应直接导入
`fireclaw_core.agent.tool_runtime` 或 `fireclaw_core.plugin.plugin_host` 等内部
实现。SDK 描述会在宿主注册事务中转换为内部 `AgentTool`。

核心只认识 `register(api)` 和通用贡献类型，不认识 `start_scan_tool` 的
具体参数，也不需要在 `gateway.py` 增加 `if plugin == ...`。插件自己的
`runtime.py` 可以连接 ROS、Nav2、move_base、SDK 或独立进程，核心只通过
插件声明的 Tool 和受信任 Adapter 使用它。

物理能力使用公开 SDK 的 `PhysicalToolSpec` 和
`api.register_physical_tool(...)` 注册。插件 handler 可以调用自己的 ROS
action、服务、算法进程或机器人 SDK；核心只负责把它接入通用的动作生命周期、
能力策略、安全门、取消和审计链，不再要求 `RobotAdapter` 上存在同名方法。

## 配置归属

通用配置按 manifest ID 传给插件：

```toml
[plugins]
paths = ["extensions"]

[plugins.config."example.scan"]
enabled = true
scan_topic = "/scan"
```

FireClaw 只处理 `enabled` 和插件加载流程；`scan_topic` 的类型、取值范围和
是否允许真实机器人使用，由 `example.scan` 自己校验。这样增加插件不会把
每个插件的配置分支塞进 Gateway 核心。

## 安全边界

- manifest 是发现描述，不是宿主命令授权；
- Runtime descriptor 只允许 typed provider/launch/readiness 数据，不允许
  Plugin 自带任意 shell 命令；
- 插件 Tool 仍要经过角色、deployment mode、机器人能力、SafetyGate 和精确
  执行授权；
- `trusted` 插件当前在受信任进程内运行，适合明确安装的本地扩展；
- `sandboxed` 插件只能贡献绑定 Docker 执行边界的 Tool；
- 任一插件注册失败时，该插件的贡献整体回滚，不留下半套 Tool；
- `PhysicalSkillPlugin` 是当前内部兼容类名，其规范语义是 physical Tool
  contribution；新增插件应通过公开 SDK 的 `PhysicalToolSpec` 注册；
- `RobotAdapter.<domain_action>()` 回退路径已经移除；physical Tool 必须提供
  Plugin-owned handler；
- `fireclaw_core.navigation` 兼容模块已经删除，导航实现只存在于
  `extensions/navigation-move-base/plugin/`。

第三方签名验证和独立插件进程仍是后续安全加固项，不会改变当前 manifest /
entrypoint 契约。
