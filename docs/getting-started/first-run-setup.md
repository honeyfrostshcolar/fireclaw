# FireClaw 首次设置

`fireclaw setup` 是普通用户的首次入口。它准备一个无凭据的 TurtleBot3 Gazebo Profile、验证 ROS 与
Navigation Plugin 依赖、生成不可变 Runtime release，并把该 Profile 记为当前选择。它不会启动 Gazebo、
Gateway 或任何机器人动作。

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

成功后，常用命令可省略 `--profile`：

```bash
fireclaw deploy status --no-runtime-check
fireclaw status
fireclaw recover
```

当前启动入口仍是：

```bash
fireclaw deploy run
```

后续 UX 切片会将它收敛为 `fireclaw start/stop`；本次 setup 闭环不提前实现 Web Console。

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
