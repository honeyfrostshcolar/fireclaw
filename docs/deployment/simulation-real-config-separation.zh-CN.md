# 仿真与实机配置隔离

FireClaw 不会通过 ROS Master、Gazebo 进程或设备型号自动判断当前连接的是仿真还是真机。
运行模式是安全边界，必须由配置文件显式声明。因此不要再使用含义不清的
`fireclaw.toml`；仿真和实机使用不同文件名、不同数据目录和不同启动命令。

| 用途 | 私有运行配置 | 可提交模板 | `deployment.mode` | 默认是否可驱动 |
| --- | --- | --- | --- | --- |
| 本机 Gazebo 演示 | `fireclaw.sim.toml` | `fireclaw.sim.example.toml` | `simulation` | 是，仅限显式标记为 simulation 的 ROS adapter |
| 真实机器人 | `fireclaw.real.toml` | `fireclaw.real.example.toml` | `real` | 否；模板保持 `dry_run=true`、Robot Agent disabled、硬件审查未通过 |

两个私有文件都被 `.gitignore` 忽略，可能包含 Provider 凭据、现场地址和机器人身份。
模板不能保存真实 API key、证书私钥或现场网络信息。

## 三条不可混用规则

1. 不要把 `fireclaw.sim.toml` 的 `mode` 改成 `real`；实机必须从
   `fireclaw.real.example.toml` 重新建立配置。
2. 所有长运行服务都显式写 `--config`，不要依赖默认查找的旧文件名。
3. `fireclaw_core mission --server ...` 是操作员 CLI，只连接已经运行的 Mission
   Gateway；它不读取机器人配置，所以不带 `--config`。

## 当前本机 Gazebo 演示

仓库根目录的私有 `fireclaw.sim.toml` 已保留当前 LLM Provider 设置，并显式配置：

- Mission/Robot embodied runtime 均为 `simulation`；
- LLM 请求超时 60 秒；
- 单次导航 Tool 超时 360 秒；
- Robot Agent 整体循环超时 600 秒；
- Mission 等待一组机器人执行的上限为 720 秒；
- 仿真 memory 与 SQLite index 使用 `data/robots/gazebo_turtlebot3/` 下的独立文件。

终端 1：启动 Gazebo、机器人底层和 `move_base`：

```bash
cd /home/lpp/fireclaw-master
source /opt/ros/noetic/setup.bash
source /home/lpp/fireclaw-master/extensions/navigation-move-base/ros_ws/devel/setup.bash
source /home/lpp/fireclaw-master/robots/turtlebot3_burger/ros_ws/devel/setup.bash
export TURTLEBOT3_MODEL=burger
export GAZEBO_PLUGIN_PATH=/home/lpp/fireclaw-master/extensions/navigation-move-base/ros_ws/devel/lib:${GAZEBO_PLUGIN_PATH:-}
export DISPLAY=:0
roslaunch /home/lpp/fireclaw-master/extensions/navigation-move-base/launch/fireclaw_acceptance_world.launch gui:=true
```

等待 Gazebo、机器人、地图和 `move_base` 就绪后，分别打开三个终端。每个终端都进入
`py310` 并回到仓库根目录。

终端 2：启动 Robot Gateway 与 Robot Agent：

```bash
conda activate py310
cd /home/lpp/fireclaw-master
python -m fireclaw_core robot-gateway \
  --config /home/lpp/fireclaw-master/fireclaw.sim.toml
```

终端 3：启动 Mission Gateway 与 Mission Agent：

```bash
conda activate py310
cd /home/lpp/fireclaw-master
python -m fireclaw_core serve \
  --config /home/lpp/fireclaw-master/fireclaw.sim.toml
```

终端 4：打开操作员 CLI：

```bash
conda activate py310
cd /home/lpp/fireclaw-master
python -m fireclaw_core mission \
  --server http://127.0.0.1:8766
```

这里 `mission` 的 URL 表示“连接本机 8766 端口上的 Mission Gateway”，不是再次启动
Mission Agent。对话、规划和确认发生在 `serve` 进程中，CLI 只负责输入、显示事件和确认。

## 以后接入真实机器人

不要直接运行仓库里的实机模板。先在部署机器的私有配置目录复制：

```bash
cp /home/lpp/fireclaw-master/fireclaw.real.example.toml \
  /etc/fireclaw/fireclaw.real.toml
```

然后逐项替换 `/opt/firebot`、`/opt/fireclaw`、机器人 ID、Robot Gateway 地址、ROS
workspace、bringup launch、地图、topic、TF、运动限制、TLS 和硬件安全接口。必须完成
`docs/deployment/real-robot-hardware-safety-acceptance.md` 的预检和现场验收后，才允许同时：

- 将 `hardware_safety_acceptance.profile_reviewed` 改为 `true`；
- 将 `robot_gateway.dry_run` 改为 `false`；
- 启用经过审核的 Robot Agent/LLM policy；
- 让 Robot Gateway 监听局域网地址，并配置 token、TLS、证书校验和允许的 host。

模板有意保持不可直接部署：`profile_reviewed=false`、`dry_run=true`、Robot Agent
disabled，并带有不存在的安装路径及未配置的远程安全参数。只改其中一个开关不代表获得了
真机动作授权。

真实部署时，机器人端运行 Robot Gateway；上位机端运行 Mission Gateway/Agent 和
Mission CLI。两台机器都可以运行同一个 `fireclaw_core` 软件包，但应保留各自主机的私有
配置和凭据。上位机配置中的 `[robot].base_url` 必须指向机器人端经过 TLS 保护的 Robot
Gateway，不能继续使用仿真的 `127.0.0.1:8765`。

## 启动前快速核对

仿真命令中只能出现 `fireclaw.sim.toml`，实机部署命令中只能出现
`fireclaw.real.toml`。还可以在启动前检查关键字段：

```bash
rg -n '^(mode|dry_run|embodied_runtime_mode|profile_path)\s*=' \
  /path/to/fireclaw.sim.toml
```

仿真必须看到 `mode="simulation"`、`dry_run=false` 和
`embodied_runtime_mode="simulation"`。实机模板在验收前必须看到 `mode="real"` 和
`dry_run=true`。
