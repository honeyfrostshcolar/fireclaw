# Navigation Runtime 生命周期判断

## 2026-08-11T10:57:26+08:00 — 是否由 FireClaw 启动 ROS 导航节点

### 用户问题

用户提出：能否由 FireClaw 在启动时或导航任务到来时，自行启动 `map_server`、AMCL、
`move_base` 等节点，再加载 Navigation Plugin 并完成导航；基础节点常驻，其他算法节点
按需启动。

### 本次检查

使用 CodeGraph 检查：

- FireClaw `FireClawPluginApi.register_service()`、`register_dispose()`、Plugin Host
  contribution/dispose 生命周期；
- Robot Gateway 当前 extension activation 与 `start()/stop()`；
- Navigation Plugin 当前 `entrypoint.py`/`Ros1MoveBaseBackend`；
- 当前是否存在 production-grade ROS launch/process supervisor；
- OpenClaw analogue：
  `openclaw/src/plugins/plugin-registration.types.ts::OpenClawPluginService` 与
  `openclaw/src/plugins/services.ts::startPluginServices`。

### 当前实现事实

- 当前 Navigation Plugin 只注册 Tool 并在 `adapter=ros1` 时创建
  `Ros1MoveBaseBackend`；它不启动 `roscore`、robot bringup、map、AMCL 或 `move_base`。
- 当前 Gazebo acceptance runner 在 FireClaw 外部启动 ROS/Gazebo，再由 Plugin 连接
  `/move_base`。
- FireClaw Plugin Host 已能注册通用 `service` contribution 和 dispose callback，但 Robot
  Gateway 尚无 OpenClaw 式的统一 Plugin Service `start/stop` runner；Gateway stop 也没有
  通过该合同按逆序停止 service。
- production core 尚无同时具备固定 launch profile、进程所有权、readiness、日志、超时、
  restart 和有界 shutdown 的 ROS runtime supervisor。现有
  `SubprocessRos1CommandRunner` 是有界诊断命令 runner，不适合作为长驻进程管理器。

### 结论

用户提出的能力可实现，而且适合作为部署工程闭环，但不能让 LLM 直接执行任意
`roslaunch`、shell 命令或路径。应复用 OpenClaw 的 Plugin Service 形状并适配机器人安全：

```text
FireClaw/Robot Gateway startup
  -> load Navigation Plugin once
  -> start trusted Navigation Runtime Service
  -> adopt already-running compatible ROS graph, or start a fixed allowlisted profile
  -> wait for bounded readiness
  -> mark navigation capability ready
  -> Tool calls use Ros1MoveBaseBackend
Gateway shutdown
  -> cancel/stop safely
  -> stop only processes owned by this service, in reverse dependency order
```

Plugin 不应每个任务重新导入；Plugin inventory 在 Agent 启动时保持稳定。可按配置选择：

- `external`：只健康检查由 systemd/vendor bringup 启动的 runtime；
- `managed + always`：FireClaw 启动时运行固定 navigation profile；
- `managed + on_demand`：首次能力请求时启动，空闲后可有界停止。

### 推荐分层

基础常驻且不依赖 LLM：硬件驱动、急停/watchdog、底盘控制、关键传感器、TF/state、odom；
已知地图任务为主时，map server、定位和 `move_base` 也建议预热常驻。运行 `move_base`
不等于允许运动，真实目标仍必须通过 Safety Gate、精确授权、motion lease 和急停链。

按需运行：高成本感知、受困者检测、3D 重建、SLAM/特殊算法等。AMCL 与 SLAM 等互斥
runtime 必须由固定 deployment profile 选择，不能由模型任意同时启动。

### 若后续实施

1. 先在 FireClaw Plugin SDK/Host/Gateway 增加 typed Plugin Service `start/stop` runner，
   复用 OpenClaw 的顺序启动、失败隔离和逆序停止结构；
2. 再在 Navigation Plugin 注册受信 Navigation Runtime Service；
3. 只接受配置中的固定 launch profile ID，不接受 LLM 提供命令、包名、路径或 ROS 名称；
4. readiness 至少验证 ROS master、`/move_base` action、`map -> base_link` TF、必要 sensor、
   map/localization 状态；
5. 记录 owned/adopted、PID/process group、启动日志、readiness、crash/restart、停止确认和审计；
6. 测试 duplicate start、部分启动失败、外部 runtime adoption、Gateway crash/restart、任务中
   shutdown、急停和不确定终态；
7. 未经用户明确要求，本次不实施代码。

## 2026-08-11T11:17:56+08:00 — 用户选择暂时保留手动 launch，并追问 Runtime 部署

### 用户决定

用户决定暂不实现 FireClaw-managed ROS node lifecycle，当前继续采用：系统/操作员先启动
对应 launch，FireClaw 随后加载 Plugin 并连接运行中的 ROS runtime。

### 新确认的部署缺口

- `extensions/navigation-move-base/ros_ws/src/navigation/` 是 pinned ROS Navigation
  Runtime 源码和可复现构建来源；若目标机没有 system `move_base`，可在部署阶段为目标机
  编译该 workspace，并 source/install 生成的 overlay 后启动节点。
- Plugin Python 代码不会在 discovery/activation 时自动编译 `ros_ws`，当前 manifest 也只含
  identity、entrypoint、trust、capabilities、skills 和 config schema，没有 runtime dependency、
  build artifact、platform compatibility、install receipt 或 launch profile 字段。
- 因此当前 Navigation Plugin 不是“复制目录后即可自动安装并启动所有 ROS Runtime”的完整
  发布包；它已有 Tool/Skill/Adapter、pinned algorithm source、Gazebo proof 和构建说明，但
  通用 Runtime installer/deployer 仍缺失。

### 推荐的长期 Plugin 发布模型

安装、启动和调用必须分开：

1. install/deploy：Plugin 携带或声明 ROS source、prebuilt deb/install-space、OCI image、模型
   权重和系统依赖；安装器验证 ROS distro/CPU/GPU/ABI，离线或受控构建，并记录 digest/receipt；
2. start：systemd、robot bringup 或以后可信 Plugin Service 启动固定 launch profile；
3. use：FireClaw Plugin 的 Adapter 通过 ROS action/service/topic 调用已 ready 的 Runtime。

新 coverage-mapping 等算法 Plugin 不应要求机器人固件预装隐藏节点；其 extension 应同时包含
FireClaw manifest/Skill/Tools/Adapter 和可部署 Runtime payload/launch/config。真正不可避免的
只是目标机必须有兼容的基础平台（ROS、驱动、架构/GPU 依赖），任何系统都无法执行未部署的
算法代码。消防机器人不应在任务到来时联网下载或现场编译，应在部署/升级阶段预构建、验证并
冻结 Runtime。

### 当前最实用工作流

- 有系统安装的兼容算法包：直接使用系统 runtime；
- 无系统包但 Plugin 带 `ros_ws`：部署阶段在目标架构构建并 source/install overlay；
- 系统启动时用一个受控 robot bringup launch 包含所选 Plugin runtime；
- FireClaw 后启动并只连接/健康检查；runtime 缺失时 Tool 必须报告 unavailable/fail closed；
- 未经用户明确要求，本次不实现 installer、manifest 扩展或 lifecycle 代码。

## 2026-08-11T11:39:00+08:00 — 多 Plugin 手工部署的可扩展性问题

用户指出：若每个 ROS 算法 Plugin 都要求进入各自目录编译、逐个 source、分别配置并启动，
新机部署会很繁琐。该判断成立；这是当前开发阶段的手工流程，不应成为最终产品体验。

CodeGraph 核对未发现现有 FireClaw deployment/installer/bundle/workspace builder/systemd 或
bringup generator 能自动收集 selected Plugin Runtime。当前缺口应明确命名为
**Plugin Runtime packaging and deployment layer**，而不是继续由用户逐 Plugin 操作。

需要保持的语义：一个 Plugin 可注册多个 atomic Tools，但通常只拥有一个或少数 Runtime；
因此部署与配置单位是 Plugin/Runtime capability，不是每个 Tool。例如 Navigation Plugin 的
导航、status、参数读取、cancel、clear-costmap Tools 共用同一个 `move_base` Runtime。

合理的目标部署体验应是一个 robot profile/bundle 选择所需 Plugins，由单一部署入口完成：

- 检测 system-provided Runtime，避免重复构建；
- 收集 bundled ROS packages/dependencies，构建统一 target overlay/install prefix；
- 或安装 prebuilt deb/OCI runtime artifact；
- 生成统一 environment、robot bringup 和 systemd/startup 配置；
- 验证 plugin/runtime compatibility 与 readiness；
- 开机只启动一次，不在每个任务到来时编译。

机器人特有 map、frame、sensor topic、footprint 和 hardware limits 仍必须配置，但应集中在一个
robot deployment profile 中，不应散落为每个 atomic Tool 的手工配置。若后续继续扩展多个
ROS 算法，建议先实现这一部署层，再批量增加 Plugin；本次仍不修改代码。

## 2026-08-11T11:48:06+08:00 — 用户确认需要统一 Plugin Runtime 部署器

### 用户确认的目标体验

用户认为该部署层有必要实现。期望新机器人安装 FireClaw 时自动构建所选算法 Runtime、生成
统一 ROS 环境；以后只维护一份机器人特有配置（sensor topic、TF、footprint、map、底盘限制、
GPU/模型路径），启动一个统一 bringup 与 FireClaw 后，所有已选择且 readiness 通过的 Plugin
Tools 即可使用，不再逐 Plugin build/source/launch。

### 设计边界

不把系统包安装、ROS 编译和 root/network 副作用隐藏在普通 `pip install` hook 中。用户体验可
由一个 installer 命令覆盖，但实现上必须有显式、可审计、可重试的 deploy plan/apply 阶段。
编译发生在部署/升级阶段，不发生在每次开机或任务执行阶段。

复用的 OpenClaw analogue：package install/compatibility 与 runtime activation 分离；Plugin
Service 使用 typed `start/stop`。FireClaw 的机器人适配新增 ROS distro/ABI/architecture、
workspace build、robot bindings、launch composition、readiness 和物理安全约束。

### 推荐 MVP

1. 为 Plugin 增加可选 data-only Runtime descriptor（优先独立
   `runtime/fireclaw.runtime.json`，由 manifest 安全相对路径引用）；第一版只支持 typed
   `system_ros1` 与 `ros1_catkin` provider，不允许 manifest 携带任意 shell 命令。
2. 在现有 `fireclaw` argparse CLI 中增加 `deploy plan/apply/status`；`plan` 纯只读，`apply`
   执行所有有副作用步骤并保留日志/receipt。
3. 由 robot profile 显式选择 Plugins 和集中 bindings；部署器优先采用满足版本要求的 system
   ROS package，否则收集 bundled source。
4. 将所选 ROS packages 构建到独立 staging/install prefix，而不是修改每个 Plugin 源目录；
   检测重复 package、ROS distro/CPU/GPU/ABI 冲突，生成 lock/manifest/digest。
5. 生成统一 environment wrapper 和单一 robot bringup launch。各 Plugin 提供固定 launch
   fragment 与 typed args，robot profile 提供值；不接受 LLM 生成 package/path/command。
6. FireClaw 启动时核对 deployment receipt 与 readiness；仅暴露已部署、兼容、ready 的 Tool，
   缺失 Runtime 必须明确 unavailable/fail closed。
7. Navigation Plugin 作为首个 vertical slice：system `move_base` reuse、bundled
   `ros_ws` fallback、production launch fragment、统一 setup/bringup、Gazebo clean-environment
   e2e。其 acceptance world/contact monitor 保持 test-only。

### 预期用户流程

```text
# 新机/升级时一次
fireclaw deploy plan  --profile <robot-profile>
fireclaw deploy apply --profile <robot-profile>

# 每次开机（后续可生成 systemd target）
<generated fireclaw robot bringup wrapper>
<generated FireClaw gateway wrapper>
```

生产发布后优先使用目标架构预构建的 deb/install-space/OCI artifact；源码现场构建保留给研发与
fallback。本次只完成设计核对和记录，尚未修改业务代码；等待用户明确要求开始实施。

## 2026-08-11T14:56:46+08:00 — Navigation Plugin Runtime 统一部署纵向切片已实现

### 用户授权与目标

用户明确回复“可以，就按这个来，开干吧”，授权实施上一节确认的统一 Plugin Runtime
部署层。目标保持为：部署/升级阶段一次 provider 解析与构建，开机时一个统一 bringup 和一个
FireClaw Gateway；不再逐 Plugin 进入目录 build/source/launch。

### OpenClaw analogue 与适配决策

实施前再次使用 CodeGraph 检查：

- FireClaw `mission_cli.main`、`FireClawExtensionManifest/_read_manifest`、Plugin Host、Gateway
  config/extension activation；
- OpenClaw `OpenClawPluginService` 与 `startPluginServices` 的顺序启动、失败隔离、逆序停止；
- OpenClaw installed Plugin index/update repair 的安装状态与 runtime activation 分离形状。

本切片复用“安装状态与 runtime activation 分离、data-only manifest、显式状态检查”的结构；
没有同时引入 Plugin Service supervisor。原因是用户此前选择保留操作员/系统启动统一 launch，
且长驻进程所有权、restart 与逆序 shutdown 是下一独立切片，不能与 Runtime packaging 一次混入。

### 已实现

1. Plugin manifest 可选 `runtime` 字段：
   - `FireClawExtensionManifest.runtime`；
   - `FireClawExtensionCandidate.runtime_descriptor_path`；
   - descriptor 必须是 Plugin 内部 regular non-symlink JSON；
   - discovery 仍不导入 entrypoint、不执行 Runtime 代码。
2. 新增 `fireclaw_core.deployment`：
   - data-only Runtime descriptor parser；
   - Robot deployment Profile parser；
   - bounded trusted subprocess runner；
   - `build_deployment_plan`、`apply_deployment`、`inspect_deployment_status`；
   - 第一版 provider 仅为 `system_ros1` 与 `ros1_catkin`，未知字段/command 被拒绝。
3. Provider resolver：
   - source 配置的 setup files 在隔离子进程中合成 ROS environment；
   - 通过 `CMAKE_PREFIX_PATH/<prefix>/share/<package>/package.xml` 只读探测 system package；
   - system package 完整时优先复用；否则选 bundled Catkin source；
   - 校验 ROS distro、当前 architecture、重复 Runtime ID、跨 Plugin 重复 ROS package、source
     package 集合与 source tree digest。
4. 显式 CLI：
   - `fireclaw deploy plan --profile ...`：不创建部署目录、不 build；
   - `fireclaw deploy apply --profile ...`：显式产生副作用；
   - `fireclaw deploy status [--no-runtime-check] --profile ...`；
   - `python -m fireclaw_core deploy ...` 与 console script 统一分发。
5. 内容寻址部署产物：
   - `<output>/<robot>/releases/<fingerprint>/`；
   - source fallback 把选定 package 汇入一个统一 workspace，用固定
     `catkin_make -C <workspace> -DCMAKE_INSTALL_PREFIX=<release>/install install`；
   - Catkin 在最终 content-addressed prefix 直接构建，避免 rename 后 install-space 内嵌 prefix
     失效；
   - profile、manifest、descriptor、launch、system package evidence/source digest 共同决定
     fingerprint；
   - deployment lock、runtime inventory、artifact hashes、release/root receipt；
   - 相同 fingerprint 幂等复用；失败 release 移到 `failures/` 并保留日志；成功后才原子切换
     `current` symlink，旧 release 不删除。
6. 统一启动产物：
   - `setup.bash`；
   - `fireclaw_bringup.launch`，先 include robot base launch，再 include selected Plugin launch；
   - `fireclaw.generated.toml`，使用绝对 Plugin paths，不复制 Provider/API credential；
   - `bin/fireclaw-bringup`；
   - `bin/fireclaw-gateway`，先运行完整 live `deploy status`，只有 ROS readiness 全部通过才启动
     Robot Gateway 并加载 Plugin。
7. Readiness：
   - `ros1_package`、`ros1_node`、`ros1_topic`、`ros1_action`、`ros1_tf`；
   - `/move_base` action 要求 goal/status/cancel topics；
   - TF 使用 bounded `rosrun tf tf_echo` 并要求实际 Translation/Rotation evidence；
   - 状态区分 `ready`、`installed`、`installed_not_ready`、`stale`、`invalid`、
     `not_deployed`；不 ready 时 fail closed。
8. Profile/安全加固：
   - 复用 canonical `load_robot_capability_profile`，避免生成 Gateway 无法使用的 profile；
   - profile 文件 symlink 拒绝，output root 复用 `validate_runtime_root`；
   - launch/binding 类型与文件存在性校验；
   - Plugin config 必须 data-only JSON-compatible；直接 `api_key/password/secret/token` 等字段
     被拒绝，允许 `_env/_file/_path/_ref` 间接引用；错误不回显凭据值；
   - Runtime descriptor 不接受 arbitrary shell、URL 或安装命令。
9. Navigation vertical slice：
   - `fireclaw.plugin.json` 引用 `runtime/fireclaw.runtime.json`；
   - system Noetic provider 要求 `move_base/map_server/amcl`；
   - source fallback 声明 pinned Navigation repo 的 16 个 package，不包含 acceptance-only Gazebo
     contact monitor；
   - 新 production `launch/fireclaw_navigation.launch`；
   - 集中 binding：robot navigation launch、map、scan topic、map/odom/base frames、footprint、
     linear/angular velocity limits；
   - readiness：packages、map_server/AMCL/move_base nodes、scan、action、map-to-base TF；
   - `fireclaw_acceptance_world.launch` 保持 test-only，未被 production descriptor 引用。
10. 文档/模板：
    - `docs/deployment/plugin-runtime-deployment.md`；
    - 更新 ROS1 guide、extension loader 文档和 Navigation README；
    - `examples/deployment_profiles/navigation_robot.toml.example`。

### 主要文件

新增：

- `src/fireclaw_core/deployment/{__init__,command,profile,runtime_descriptor,deployer}.py`
- `extensions/navigation-move-base/runtime/fireclaw.runtime.json`
- `extensions/navigation-move-base/launch/fireclaw_navigation.launch`
- `examples/deployment_profiles/navigation_robot.toml.example`
- `docs/deployment/plugin-runtime-deployment.md`
- `tests/test_plugin_runtime_deployment.py`

修改：

- `src/fireclaw_core/plugin/extension_loader.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/__main__.py`
- `extensions/navigation-move-base/fireclaw.plugin.json`
- `extensions/navigation-move-base/README.md`
- `docs/architecture/extension-loader.zh-CN.md`
- `docs/deployment/ros1-deployment-guide.md`
- `pyproject.toml`（为 Python 3.10 显式声明既有 `tomli` compatibility dependency）

### 验证证据

1. 部署/Extension 聚焦测试逐步扩展后，相关聚焦结果为 `31 passed`。
2. Navigation/Plugin/Deployment policy 组合回归：

```text
python -m pytest -q \
  tests/test_plugin_runtime_deployment.py \
  tests/test_extension_loader.py \
  tests/test_move_base_navigation_plugin.py \
  tests/test_plugin_host.py \
  tests/test_runtime_path_security.py \
  tests/test_deployment_tool_policy.py \
  tests/test_deployment_agent_tool_integration.py
69 passed
```

3. 本机真实 Navigation descriptor 的 system provider resolution：
   - source `/opt/ros/noetic/setup.bash`；
   - 选择 `system_ros1`；
   - evidence 恰为 `move_base/map_server/amcl`；
   - 在 pytest temp output 实际 `apply` 后，静态 status=`installed`；
   - 生成 launch 含 9 个 Navigation binding args；
   - 没有运行生成的 bringup、没有启动 ROS 节点。
4. 完整 suite 初次在 filesystem sandbox 中运行 `test_mission_cli.py` 时，6 个既有 Gateway 测试
   因 `socket(...)=PermissionError: Operation not permitted` 失败；这是沙箱 loopback 限制，不是
   代码失败。获用户授权后在沙箱外最终重跑：

```text
2021 passed, 7 skipped in 167.55s
```

   7 个 skip 仍为显式 ROS/Gazebo opt-in lanes。随后仅扩展同一 Navigation deployment test 的
   assertions，targeted 结果 `2 passed in 1.41s`；业务代码未再变化。
5. 其他验证：

```text
python -m py_compile / compileall: passed
git diff --check: passed
python -m fireclaw_core deploy --help: 正确路由 plan/apply/status
```

### 实施中发现并修复的问题

- Python 3.10 首轮 test collection 因直接 `import tomllib` 失败；改为项目现有
  `tomllib/tomli` fallback，并在 `pyproject.toml` 显式补 Python<3.11 dependency。
- `python -m fireclaw_core deploy` 首次被 `KNOWN_SUBCOMMANDS` 漏项误路由到 Agent CLI；已加入
  deploy 并加回归测试。
- 早期 staging directory build 后 rename 会破坏 Catkin install-space 的内嵌 prefix；改为在
  最终 content-addressed release path 构建，但成功前不切 `current`。
- 早期 Gateway wrapper 只做 static status；改为完整 live readiness gate，未 ready 不启动
  Gateway/Plugin。
- profile symlink 检查曾在 `resolve()` 后执行而失效；改为先检查 authored path。
- artifact receipt 最初会 hash Catkin build/devel intermediates；改为记录 source digest 与
  install-space/runtime artifacts，workspace 只保留诊断而不进入 runtime artifact receipt。

### 当前结论

工程上，Navigation Plugin 已从“代码/ROS source/测试都在 extension，但需要人工逐目录部署”
推进为可执行的统一部署纵向切片。新机仍必须先具备兼容 ROS 与机器人硬件/vendor 基础环境，
这属于不可消除的平台前置条件；但 FireClaw-selected Plugin 的 provider 解析、bundled source
fallback、统一 environment、统一 launch、Gateway config、receipt 与 readiness 已不再要求用户
逐 Plugin 手工操作。

这属于工程基础设施，不构成论文算法创新。研究层面的价值是提高真实/仿真评测的环境可复现性
与 provenance，但不能单独作为方法贡献。

### 已知限制与下一步

尚未实现：

- deb/prebuilt install-space/OCI provider；
- GPU/CUDA/model/ABI typed provider；
- system dependency 安装或联网 artifact 获取；
- systemd/开机 target；
- Gateway 内部 Plugin Service supervisor/process ownership/restart/reverse stop；
- 部分 Plugin ready 时只暴露该子集（当前生成 Gateway wrapper 对全部 selected Plugin 做整体
  fail-closed gate）；
- clean-machine source fallback 的真实 Navigation `catkin_make install` 与 Gazebo/实机 E2E。

下一步应由用户复制并填写
`examples/deployment_profiles/navigation_robot.toml.example`，先运行真实机器上的：

```bash
fireclaw deploy plan --profile /path/to/firebot.toml
```

核对 system/source provider 与 binding 后，再显式 `deploy apply`。若继续开发，优先补一个
干净容器/新机的 source-fallback deployment acceptance，再考虑 prebuilt artifact provider；
不要现在同时扩展 managed Plugin Service lifecycle。

### Git 状态

本次没有 commit、push 或 PR。业务代码、文档、测试和本 memory 仍为本地工作区修改；原有
`memory/2026-08-11/` 未跟踪记录被保留，未覆盖用户内容。

## 2026-08-11T15:54:33+08:00 — TurtleBot3 Burger robot-owned 配置样板

### 用户目标

用户要求使用仓库 `robots/` 下现有 TurtleBot3 Burger ROS/Gazebo 平台，实际做一份
机器人专属 Navigation 配置，作为以后接入其他仿真或真实机器人时的参考模板。

### 检查与设计依据

- 按仓库规则先用 CodeGraph 检查 Navigation Runtime/Profile/launch 参数解析，再定向检查
  `robots/turtlebot3_burger` 中 pinned TurtleBot3 launch、URDF、地图与参数。
- 检查的上游机器人配置包括：
  `turtlebot3_world.launch`、`turtlebot3_navigation.launch`、`amcl.launch`、
  `move_base.launch`、Burger costmap/DWA YAML 和 map。
- 本切片没有 OpenClaw 对应实现：ROS1/Gazebo、URDF、AMCL/costmap 和机器人 TF 是
  FireClaw 机器人平台适配问题。沿用的是上一切片已经核对过的 Plugin Runtime descriptor 与
  deployment/Profile 边界，没有发明新的 Plugin API。
- 不修改 pinned 上游 TurtleBot3 源码；新增独立 robot-owned Catkin package，便于以后用实机包
  替换，同时保持 Navigation Plugin 不变。

### 新增与修改文件

新增 `robots/turtlebot3_burger/ros_ws/src/fireclaw_turtlebot3_burger/`：

- `package.xml`、`CMakeLists.txt`：配置型 Catkin package，安装 `launch/` 与 `config/`；
- `launch/robot_base.launch`：启动 Gazebo、Burger URDF、spawn、
  `robot_state_publisher`；Gazebo 模型提供 `/scan`、`/odom`、`/cmd_vel` 和 odom/base TF；
- `launch/navigation_stack.launch`：实现 FireClaw Navigation Plugin 固定参数合同，启动
  `/map_server`、`/amcl`、`/move_base`；
- `config/amcl.yaml`：Burger 激光/里程计粒子滤波参数；
- `config/costmap_common.yaml`、`global_costmap.yaml`、`local_costmap.yaml`：传感器层与
  global/local costmap 参数；frame、footprint、scan topic 不写死，由 Profile 注入；
- `config/move_base.yaml`、`dwa_local_planner.yaml`：move_base/DWA 调度与轨迹参数；最大线/角
  速度不写死，由 Profile 注入；
- package README：说明它是 robot-owned 配置，不是 Tool/Runtime 算法。

新增 `robots/turtlebot3_burger/fireclaw.toml.example`：

- combined Robot capability + Runtime deployment Profile；
- `mode=simulation`、ROS Noetic、robot workspace setup；
- `robot_launch` 指向 `robot_base.launch`；
- Navigation bindings 指向 `navigation_stack.launch` 和 TurtleBot3 map；
- `/scan`、`map/odom/base_footprint`、Burger 非对称 footprint、`0.22 m/s`、`2.75 rad/s`；
- 选择 `fireclaw.navigation.move-base` Plugin；
- 使用 `.example` 是因为 `.gitignore` 有意忽略本机 `fireclaw.toml`。样板可直接运行，也可复制
  成本机文件。

修改：

- `robots/turtlebot3_burger/README.md`：加入构建、plan/apply、统一 bringup/Gateway 流程；
- `tests/test_turtlebot3_burger_deployment_assets.py`：5 项静态合同测试，覆盖 Profile、Plugin
  参数集合、base/navigation 分层、YAML 中 Profile-owned 值未被写死、地图 sidecar 与 Catkin
  install assets。

### 启动结构

```text
generated fireclaw_bringup.launch
  -> robot_base.launch
       -> Gazebo + Burger URDF + /scan + /odom + TF
  -> Navigation Plugin fireclaw_navigation.launch
       -> navigation_stack.launch
            -> map_server + AMCL + move_base
            -> AMCL/costmap/DWA robot-owned YAML
```

### 实施中发现并修复

1. 第一版 `robot_base.launch` include 上游 `turtlebot3_remote.launch` 并传
   `model=burger`；`roslaunch --nodes` 仍先求值上游 default
   `$(env TURTLEBOT3_MODEL)`，因未 export 环境变量而失败。
2. 修正为 robot-owned launch 直接设置 `robot_description` 并启动
   `robot_state_publisher`，消除隐式 `TURTLEBOT3_MODEL` 前置条件；之后统一 launch 成功展开为
   `/gazebo`、`/spawn_turtlebot3_burger`、`/robot_state_publisher`、`/map_server`、
   `/amcl`、`/move_base`。
3. 沙箱内真实 `roslaunch` 因 `netifaces.interfaces()` 返回
   `PermissionError: Operation not permitted` 失败；获授权在沙箱外重跑，属于沙箱网络限制，
   没有为此修改产品行为。
4. 一条仅用于压缩 plan 输出的 `jq` 命令因本机无 `jq` 失败；随后直接调用
   `build_deployment_plan` 输出摘要并通过，不是 FireClaw/ROS 错误。

### 验证结果

- XML：三个新增 XML 文件 `xmllint --noout` 通过；
- Catkin：robot workspace 两次 `catkin_make` 通过，识别 12 个 package，新增
  `fireclaw_turtlebot3_burger` 排在拓扑首位；仅有沙箱 `~/.ros` rospack cache 只读警告、
  pinned legacy setuptools 警告和 Gazebo Classic EOL 警告；
- 聚焦回归：

```text
tests/test_turtlebot3_burger_deployment_assets.py
tests/test_plugin_runtime_deployment.py
tests/test_move_base_navigation_plugin.py
tests/test_robot_profile.py
tests/test_gazebo_acceptance_harness.py
68 passed in 2.79s
```

- 当前 `.example` Profile plan：status=`ready`，fingerprint=
  `5ccd34f10162e1a64bbc915d7e4cba20778a44253d7d418604bb0e63e51b47a8`，选择
  `system_ros1`，robot/stack launch 均解析到新增包；
- 在 Profile 改名为 `.example` 前，用内容相同的 `fireclaw.toml` 实际执行 plan/apply 到：
  `/tmp/fireclaw-turtlebot3-reference-deploy/gazebo-turtlebot3-burger`，生成 release fingerprint
  `0a75fda72f61a7cb72f2d80db993a1956511c42cc9cc17b53b18881fb2dab0fc`；路径名参与 fingerprint，
  因此改名后的摘要 fingerprint 不同；
- 用生成的 `bin/fireclaw-bringup` 实际启动 Gazebo/ROS，地图为 384x384、0.05 m/cell；AMCL
  likelihood field、global/local costmap、DWA 初始化成功，`odom received`；
- `fireclaw deploy status` live 结果 status=`ready`，以下全部 `ok=true`：
  system packages `move_base/map_server/amcl`、nodes `/map_server` `/amcl` `/move_base`、topic
  `/scan`、action `/move_base`、TF `map -> base_footprint`；
- 测试结束后通过 Ctrl-C 正常关闭本次拥有的 move_base、AMCL、map_server、
  robot_state_publisher、Gazebo、ROS master；
- `git diff --check` 和新增文件 trailing-whitespace 检查通过；没有 commit/push。

### 当前结论与边界

这份样板现在真实展示了“机器人包提供物理/仿真与导航配置，Navigation Plugin 提供通用
Runtime/Tools，Profile 负责绑定，统一 bringup 负责启动”的完整关系，不是只有注释或伪路径。

仍保留此前部署层已知边界：外部 robot launch/map/config 当前通过稳定路径引用，并未把整个
robot package 内容复制冻结到 content-addressed release。该 source-checkout 样板的
`robot.ros1_config` 与 `robot.data_dir` 也遵循现有 CWD-relative 约定，所以文档明确要求从仓库
根目录运行。生产机器人应使用绝对安装路径，后续可再引入 robot asset bundle digest/receipt。

下一步推荐先由用户逐个查看 Profile、两个 launch 和 YAML 的映射；需要接入实机时，复制这个
薄 package，把 Gazebo base bringup 替换成厂商底盘/雷达/URDF launch，再依据实机尺寸和测试数据
调整 footprint、速度、AMCL/costmap/DWA 参数，不修改 Navigation Plugin。

## 2026-08-11T17:07:48+08:00 — Navigation composition 改为 Plugin-owned

### 用户决定与本次目标

用户明确否定“每台机器人都必须自己实现固定格式的 `navigation_stack.launch` 并保存整套
AMCL/costmap/DWA YAML”的边界，决定采用：

```text
FireClaw root fireclaw.toml
  -> robot/map/topic/TF/footprint/physical limits
Navigation Plugin
  -> generic map_server + AMCL + move_base launch
  -> conservative default YAML
Robot package
  -> robot_base.launch only (hardware/simulation, sensors, odometry, base TF)
```

本次没有修改用户本机已存在且被 Git 忽略的 `fireclaw.toml`，避免覆盖其私有 Provider 配置和
其他本机改动；更新的是可复制的根 `fireclaw.example.toml`。原来位于
`robots/turtlebot3_burger/fireclaw.toml.example` 的样板不再保留。

### 设计依据与 OpenClaw analogue

- 先用 CodeGraph 检查了现有 `RuntimeLaunchArgument`、Runtime descriptor parser、
  `_resolve_launch_arguments`、root `fireclaw.toml` loader 和 deploy CLI flow。
- root Gateway config loader 保留整个 structured `[deployment]`，并允许 `[robot]` 和
  `[plugins].selected` 与部署 Profile 共存，因此无需新建第二套根配置格式。
- OpenClaw 没有 ROS1 map_server/AMCL/move_base 或机器人 TF/costmap 的直接实现；本次继续沿用
  已核对过的 OpenClaw-shaped data-only Plugin Runtime/Profile/deployer 边界，没有新增 Agent Tool
  或 LLM 可调用的进程接口。
- 变化属于 FireClaw 机器人 Runtime composition：可信 deployer 把 robot base launch 与
  Plugin-owned fixed launch 组合；任务阶段的 LLM 仍不能选择 launch、topic、frame 或速度限制。

### 实际修改

Navigation Plugin：

- 重写 `extensions/navigation-move-base/launch/fireclaw_navigation.launch`，不再 include
  `navigation.stack_launch`，直接启动 `/map_server`、`/amcl`、`/move_base`；
- 新增 `config/defaults/{amcl,costmap_common,global_costmap,local_costmap,move_base,
  dwa_local_planner}.yaml`；
- Runtime descriptor 删除 `navigation.stack_launch`，新增 `scan_frame`、`cmd_vel_topic`、
  `odom_topic`、速度/加速度、AMCL odometry/noise、sensor/costmap range、initial pose 等 typed
  bindings，并增加 `/odom` 与 `base_frame -> scan_frame` readiness；
- descriptor 新增 data-only `assets` 列表；deployer 记录每个 asset SHA-256 并把它计入 deployment
  fingerprint，避免默认 YAML 修改后仍错误复用旧指纹。

Root Profile：

- `fireclaw.example.toml` 现在同时是 Gateway config、Robot capability profile 和 Runtime
  deployment profile；
- TurtleBot3 map/topic/TF/footprint/limit/AMCL/costmap values 都在
  `[deployment.bindings.navigation]`；
- `[deployment.launch].robot_launch` 只指向 robot base；
- `[plugins].selected` 选择 Navigation Plugin；Mission/Robot Gateway 样板指向复制后的根
  `fireclaw.toml`。

Robot package：

- 删除 robot-owned `launch/navigation_stack.launch` 与 6 个导航 YAML；
- `fireclaw_turtlebot3_burger` 只剩 `launch/robot_base.launch`；
- CMake install 仅安装 `launch/`，package.xml 删除 map_server/AMCL/move_base/DWA dependencies；
- README 和顶层 TurtleBot3 文档改成新的职责说明。

同步更新：

- `examples/deployment_profiles/navigation_robot.toml.example`；
- `docs/deployment/plugin-runtime-deployment.md`；
- `extensions/navigation-move-base/README.md`；
- `tests/test_plugin_runtime_deployment.py` 与
  `tests/test_turtlebot3_burger_deployment_assets.py`。

### 验证命令与结果

Robot workspace：

```bash
source /opt/ros/noetic/setup.bash
export SETUPTOOLS_USE_DISTUTILS=stdlib
catkin_make  # cwd=robots/turtlebot3_burger/ros_ws
```

结果：12 packages 构建成功；仅有已知 rospack cache 只读、legacy setuptools 和 Gazebo Classic
EOL warnings。

静态与聚焦回归：

```text
68 passed in 2.77s
20 deployment/asset tests passed in 1.82s（加入 Runtime asset hash 后）
xmllint: Plugin launch、robot_base.launch、package.xml 通过
6 个 Plugin default YAML 均解析为非空 mapping
git diff --check 通过
```

完整回归第一次在默认受限沙箱运行，所有需要 loopback socket 的 Gateway tests 收到
`PermissionError: Operation not permitted`，结果为 `1894 passed, 132 failed, 7 skipped`；这些失败
均是 sandbox network policy，不是产品断言失败。获授权在沙箱外重跑相同命令：

```text
2026 passed, 7 skipped in 168.62s
```

加入 Runtime asset fingerprint 回归测试后又在同一沙箱外环境执行最终全量验收：

```text
2027 passed, 7 skipped in 166.53s
```

### Live Gazebo proof

使用根 example Profile 部署到
`/tmp/fireclaw-generic-navigation-deploy/gazebo-turtlebot3-burger`，system provider 被选中；live
launch 对应 fingerprint 为
`fffe1f7ed7a7978107b0a7fc103957945a4a49da1fb27c74ba03f5f3f72ae61e`。后续加入 asset hashing
后最终 plan fingerprint 为
`376817ebe889a3506f402df65bf18747d35de98d526a04b54d1dac5e383f5424`；Runtime 行为文件内容未变。

生成 launch 成功展开为：

```text
/gazebo
/spawn_turtlebot3_burger
/robot_state_publisher
/map_server
/amcl
/move_base
```

参数 dump 证明 root Profile 覆盖生效：

```text
/amcl/laser_max_range = 3.5
/move_base/DWAPlannerROS/min_vel_x = -0.22
/move_base/global_costmap/scan/sensor_frame = base_scan
```

`inspect_deployment_status` 返回 `status=ready`，以下 9 项全部 `ok=true`：system package、三个
navigation nodes、`/scan`、`/odom`、`/move_base` action、`map -> base_footprint` TF、
`base_footprint -> base_scan` TF。

最初两次短脚本使用 `SimpleActionClient.wait_for_server(rospy.Duration(...))`，在
`/use_sim_time=true` 且 Gazebo real-time factor 较低时发生超时误判；ROS graph 与五个 action
topics 均在，逐项连接检查也确认 caller ID 为 `/move_base`。最终 smoke 改为只在测试脚本中使用
wall-clock handshake/deadline（没有修改产品 action semantics），向 `map` frame 的
`(-1.0, -0.5, yaw=0)` 发送约 1 m 目标：收到 70 次 feedback，终态 `3/SUCCEEDED`，status text
为 `Goal reached.`。

验证后通过 Ctrl-C 正常关闭本次拥有的 move_base、AMCL、map_server、robot_state_publisher、
Gazebo、rosout 和 ROS master；进程检查未发现残留 ROS/Gazebo Runtime。

### 当前结论与下一步

工程边界已按用户决定纠正：标准 ROS1 LaserScan + move_base 部署不再要求用户编写
`navigation_stack.launch` 或复制导航 YAML。用户换机器人时只需提供 base bringup，并在根
`fireclaw.toml` 配置实际 map/topic/TF/footprint/limits；Plugin Runtime provider 负责复用或编译
Navigation Stack，Plugin launch 负责启动三个导航节点。

非标准导航方案（例如 TEB、专用 3D costmap、Nav2 或厂商私有导航 runtime）不应继续向这一份
move_base baseline launch 塞任意分支，应作为对应 Runtime/Plugin 变体显式建模。

研究层面仍是部署正确性、可复现性与 provenance 加固，不构成算法创新；它为以后实机/仿真
对照实验减少环境差异，但不能单独作为论文贡献。

## 2026-08-11T17:53:37+08:00 — 面向用户的 TurtleBot3 演示流程核对

### 用户目标

用户希望亲自运行一次当前 TurtleBot3 Burger Gazebo 导航演示，要求明确说明如何启动、打开
可视化、通过 FireClaw 下发导航任务以及如何判断成功。

### 本次核对

- `fireclaw.example.toml` 的 deployment plan 在当前机器仍为 `status=ready`，选择
  `system_ros1`，当前 fingerprint 为
  `a1b5ed133d0825995f6f6c4bb52666595266dbcbef00b282c644bc0da8d74691`；
- 生成的 `fireclaw-bringup` 可直接启动 Gazebo base 与 Plugin-owned
  map_server/AMCL/move_base；base launch 默认 `gui=false`，因此另开 `gzclient` 才能看到 Gazebo
  窗口；RViz 可复用 TurtleBot3 navigation 配置；
- FireClaw Robot Gateway 的正式演示请求使用 `POST /tasks` 的 structured task，目标采用此前
  live smoke 已证明可达的 `map` 坐标 `(-1.0, -0.5, yaw=0)`；随后通过
  `GET /tasks/<task_id>` 与 `/events?task_id=<task_id>` 查看 Tool/action 证据；
- 当前 example 的 `dry_run=true` 表示仿真执行，不代表 InMemory backend：Profile adapter 为
  `ros1` 时 Navigation Plugin 仍选择 `Ros1MoveBaseBackend` 并把 goal 发到 Gazebo
  `/move_base`。当前样例一般不会要求 `/confirm`；若用户改用 non-dry execution，则按同一
  session 调用 `/confirm`。

### 新发现的启动包装缺口

1. 当前 `pyproject.toml` console script 仍指向已不存在的
   `fireclaw_core.mission_cli:main`；本机 `/home/lpp/miniconda3/envs/py310/bin/fireclaw --help`
   实际报 `ModuleNotFoundError`。源码模块入口
   `PYTHONPATH=src .../python -m fireclaw_core` 正常。
2. 生成的 `fireclaw.generated.toml` 把 runtime cwd 设为 deployment `state/`，但根 example 的
   `robot.ros1_config` 是仓库根相对路径；Profile loader 当前不按 Profile 文件目录解析该字段，
   因而直接使用生成的 `fireclaw-gateway` wrapper 可能在 state cwd 下找不到 ROS1 adapter
   YAML。

这两个问题属于 CLI/路径 packaging，不改变已验证的 ROS Navigation Runtime。为了给用户一套
当前即可执行的流程，本次演示使用：生成的 `fireclaw-bringup` + 手动 live `deploy status` +
从仓库根配置运行 `python -m fireclaw_core robot-gateway --config fireclaw.example.toml
--robot-profile fireclaw.example.toml`。本次没有修改业务代码；后续应单独修复 console entrypoint
及 Profile-relative path resolution，再恢复两条生成 wrapper 的无绕行启动体验。

## 2026-08-11T17:59:07+08:00 — 当日暂停与明日续接点

### 用户决定

用户表示今天先暂停，明天继续；希望完整保留当前上下文，避免下一次重新梳理。

### 今日最终状态

- Navigation Plugin 的 ROS Runtime 部署、Plugin-owned map_server/AMCL/move_base launch、
  TurtleBot3 robot-owned base bringup、统一 environment、readiness gate 和根 Profile 已完成；
- ROS/Gazebo live proof 已确认 `/scan`、`/odom`、两个 TF、三个导航节点及 `/move_base` action
  ready，直接 actionlib 目标 `(-1.0, -0.5)` 以 `SUCCEEDED(3)` 到达；
- 已向用户提供五终端的手动演示流程：一次性 build/deploy、统一 bringup、gzclient、RViz、
  Robot Gateway、`POST /tasks` structured navigation、task/event 结果检查及安全停止顺序；
- 用户尚未亲自执行该演示流程，因此“从独立 Gateway 进程经 Navigation Plugin 到 live Gazebo
  `/move_base`”仍应由下一次用户实跑完成最终确认。

### 明日优先顺序

1. 先让用户按已给出的演示流程逐步运行；每一步只在上一步达到预期状态后继续；
2. 若失败，先用 `deploy status`、`/health`、`/skills`、task trace 与 task events 定位层级，
   不要一开始重构导航代码；
3. 演示通过后，修复两个已确认的启动包装缺口：
   - 将 `pyproject.toml` 的 console entrypoint 从不存在的
     `fireclaw_core.mission_cli:main` 改到当前统一入口，并补 console-script 回归；
   - 统一 Robot Profile 中 `ros1_config`、`data_dir` 等相对路径的解析基准，使生成的
     `fireclaw-gateway` 在 deployment state cwd 下也可靠，并补真实生成配置测试；
4. 修复后重新验证最终产品式两命令启动：`fireclaw-bringup` 与 `fireclaw-gateway`，再更新文档，
   去掉演示中的 `python -m fireclaw_core` 绕行说明。

### 明日不要重复的工作

- 不要重新设计 Navigation Plugin/Tool/Runtime 边界；用户已经确认当前分层；
- 不要把通用导航 YAML 或 `navigation_stack.launch` 放回 robot package；
- 不要覆盖 Git 忽略的用户本机 `fireclaw.toml`；继续用 committed
  `fireclaw.example.toml` 或显式用户副本；
- 不要把当前部署工程描述成论文算法创新。
