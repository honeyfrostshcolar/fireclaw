# 阶段 2 + 3：首次使用闭环与 Web 权威状态

## 2026-08-20T00:08:37+08:00

### 任务目标

用户要求联合完成：

1. wheel-first 的 simulation bundle 校验/物化、可续接 ROS workspace 构建、稳定 Profile 路径，以及
   CLI-owned `setup -> start -> open` 一命令流程；
2. real setup 仅允许被动 ROS 图发现、draft/diff 与非致动 preflight，禁止启动或运动；
3. Gateway 注入不可变 runtime identity，readiness 提供来源、观测时间、freshness 与 evidence ID；
4. Web 全部从 Gateway 合同读取 mode、active Profile、robot readiness，不使用硬编码或无证据推断；
5. 增加 clean venv/wheel first-run、real no-motion、JSON contract 与真实 headless browser E2E。

### 启动状态

- 基线提交：`679048935fba7c7b0dc099d38bb8e4e962d51087`；分支相对远端领先 7 个提交。
- 启动时 `git status --short --branch` 只有 branch 行，工作区 clean。
- 阶段 1 全量证据：`2342 passed, 8 skipped`；clean Git archive distribution gate `ALL PASSED`。
- 本轮不会提交或推送，除非用户另行明确要求。

### CodeGraph / OpenClaw analogue

先用根项目 CodeGraph 检查：

- `infra/user_setup.py`: `setup_fireclaw`、`handle_setup`、source-root 解析、Profile 生成；
- `deployment/deployer.py`: content-addressed release、fingerprint、receipt、Catkin install 构建与复用；
- `infra/daemon_manager.py`: `start_daemon`、健康轮询、幂等启动、`open_console`；
- `mission/mission_gateway.py`: constructor、`readiness()`、fleet doctor/state；
- `gateway/serve.py`: Mission Gateway 生产装配路径；
- `web_console/app.js`: readiness 获取、overview/recovery/settings 投影；
- `config/discovery.py` 与 `hardware_safety_acceptance.py`: 被动 ROS XML-RPC 发现和非致动 preflight。

OpenClaw 有独立 `.codegraph/`，已读取 `openclaw/AGENTS.md`、`src/gateway/AGENTS.md` 与
`src/gateway/server-methods/AGENTS.md`，再检查：

- `src/commands/gateway-readiness.ts::ensureGatewayReadyForOperation`：CLI 在获准后安装/启动 Gateway，随后
  重新探测 readiness；
- `src/commands/dashboard.ts::dashboardCommand`：打开页面前验证 Gateway，恢复后若目标变化则再次验证；
- `src/commands/onboard-helpers.ts::runOnboardingGatewayProbe`：onboarding 使用有界轻量 probe；
- `src/gateway/server-methods/system-agent.ts:564`：Gateway surface 明确不得安装/重启自身 daemon。

FireClaw 复用该 owner boundary：生命周期副作用归 CLI，Gateway/Web 只暴露和消费状态。区别是 FireClaw
必须额外绑定 bundle/profile/deployment 哈希、机器人证据 freshness，以及 real no-motion 安全边界。

### 当前设计决定

- 继续遵守发布契约：core wheel 与 versioned simulation sidecar bundle 是两个产物，不把约 8.3 MiB ROS
  源码归档塞入 core wheel。
- catalog 增加 canonical artifact filename 与固定 SHA-256。wheel 安装后 `setup` 从显式参数、受控环境变量、
  当前目录或 runtime download cache 发现 companion artifact；源码 checkout 可构建相同 bundle 作为开发回退。
- 不虚构当前不存在的下载 URL。缺 bundle 时 fail closed，并指导获取同版本 companion artifact。
- bundle 与 ROS workspace 都进入 runtime root 下的内容寻址 release；Profile 绑定具体不可变 release，不绑定
  `current` symlink、源码 checkout 或 `devel/setup.bash`。
- Gateway runtime identity 在进程启动时计算并冻结；后续磁盘文件变化不会悄悄改变当前进程的身份声明。
- 为保持 HTTP 合同 additive，legacy readiness scalar 保留；新增 evidence envelopes 作为 Web 唯一消费面。

### 下一步

先写 bundle/materialization、workspace resume、quickstart、real no-motion 与 runtime identity/readiness 的失败
测试，再实现最小生产路径；随后补 headless Chrome E2E 与 clean-wheel installed E2E。

## 2026-08-20T21:38:47+08:00

### 解释器纠正

用户明确提醒 Python 3.10 位于 Conda，而不是系统 Python。最终所有测试、构建与 live validation 均使用：

```text
/srv/lpp-extra/miniconda3/envs/py310/bin/python
```

后续不得再以系统解释器缺少 Python 3.10 为阻塞理由。

### 最终实现结果

阶段 2：

- `src/fireclaw_core/infra/simulation_runtime.py` 新增 companion bundle 解析、固定 catalog SHA 校验、安全解包、
  provenance 校验、内容寻址物化、Catkin install workspace fingerprint/receipt、失败续接与 current link。
- bundle release 在发布后递归移除写权限；重用时重新核对文件集、hash、size、规范化只读 mode、目录/文件
  不可写与无 symlink。Catkin 使用 workspace 内的私有 source copy，不能修改 bundle release。
- `WORKSPACE_BUILDER_VERSION=2`；Profile 绑定具体 bundle/workspace release，不绑定源码树、`current`、
  `devel/setup.bash` 或可变路径。
- `fireclaw setup` 默认由 CLI 编排 simulation-only `setup -> start -> open`，支持
  `--simulation-bundle`、`--no-start`、`--no-browser`、`--startup-timeout`；重复调用重新验证已运行 daemon
  健康度并返回 `already_running`。
- real branch 在 deployment planning/apply 之前返回，只允许已人工审查的 real Profile、被动 ROS XML-RPC
  discovery、`applied=false` draft/diff 与 `live=false` preflight；不调用 daemon manager、Robot Tool 或运动接口。
- release distribution smoke 从无 `PYTHONPATH` 的临时 venv 安装 wheel，并使用真实 companion artifact 完成
  materialize/workspace/Profile/reuse/`setup -> start -> open`；Catkin、daemon、browser 仅在 typed boundary 使用
  test double，因此 smoke 不启动 ROS/Gazebo/实机。

阶段 3：

- `GatewayRuntimeIdentity` 在 Gateway 启动时从 active Profile + deployment receipt 构造并冻结：runtime mode、
  Profile path/SHA/revision、robot ID、deployment fingerprint。启动后磁盘 Profile 变化不会改变进程身份。
- `/readiness` 升级为 schema version 2，新增 `runtime_identity`、`observations`、`robot_readiness`；每个 Web
  消费项都带 `source`、`observed_at`、`freshness`、`evidence_id`。
- Web 默认全部为 `UNKNOWN`，只消费上述 evidence envelope；不回退 legacy mode/Profile/registry first robot，
  不从“无 blocker”推断传感器、急停或物理停止正常。缺失、stale、伪造 legacy-only 合同均 fail closed。
- headless Chrome 145 通过标准库 CDP websocket 驱动真实本机 Gateway 页面；覆盖 fresh simulation identity、
  forged legacy-only readiness、stale evidence 三类浏览器投影。

### 真实 Gazebo 首次运行发现并修复的问题

所有 live 命令都使用独立 `/tmp` `FIRECLAW_HOME`、`--no-browser`，只启动 Gazebo/ROS 仿真，未连接真实
机器人。

1. 第一次 live setup 已成功构建 Catkin 并启动 Gazebo/Robot Gateway，但 Mission Gateway 退出：
   `NameError: name 'os' is not defined`。根因是 `mission_cli.py` 的 serve 分支新增读取
   `FIRECLAW_DEPLOYMENT_RECEIPT` 后漏导入 `os`。补 import，并增加
   `test_serve_subcommand_forwards_runtime_identity_inputs`，验证 Profile/receipt 实参透传。
2. 在同一失败 HOME 续接时，bundle 校验发现
   `robots/turtlebot3_burger/ros_ws/src/CMakeLists.txt` symlink。根因是旧 workspace `src` 直接链接到
   immutable bundle，`catkin_make` 初始化 source space 时反向污染 bundle。改为私有 source copy；每次失败
   续接前重新从已校验 bundle 物化 source copy，同时保留 build/devel/install/logs。
3. 修复后首次 live 成功，重复 setup 又检测到 bundle 多出
   `extensions/navigation-move-base/plugin/__pycache__/*.pyc`。根因是运行时直接从 bundle 导入 plugin。
   最终改为物化成功后从整个 bundle release 移除写权限，workspace copy 单独恢复 owner-write；没有放宽
   校验或把 pycache 列入允许集。

旧的 `/tmp/fireclaw-stage23-live` 与 `/tmp/fireclaw-stage23-live-fixed` 保留为失败证据；最终验收 HOME 为
`/tmp/fireclaw-stage23-live-final`。

最终 live 命令：

```text
FIRECLAW_HOME=/tmp/fireclaw-stage23-live-final PYTHONPATH=src:. \
  /srv/lpp-extra/miniconda3/envs/py310/bin/python -m fireclaw_core setup \
  --mode simulation \
  --simulation-bundle /tmp/fireclaw-stage23.tm5P4r/fireclaw-sim-turtlebot3-burger-v1.tar.gz \
  --no-browser --startup-timeout 120 --json
```

第一次最终运行结果：

- `status=ready`，`health_verified=true`，`safe_state=simulation_only`；
- `real_robot_action_started=false`；
- stable Profile：
  `/tmp/fireclaw-stage23-live-final/profiles/gazebo-turtlebot3-burger-1.0.0-5488b1e6d0f2.toml`；
- bundle SHA：`5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e`；
- workspace fingerprint：`1417fc1902e25298657caa54f375968821342101db6ddf7ad6177f5fa57e6c69`；
- deployment fingerprint：`43c4443417fb20903bf5687b84a730e7abad6acce686e2272d074045336604b0`；
- bundle manifest 对比：`extra=[]`、`missing=[]`、`writable=[]`。

同一命令第二次运行结果：

- `profile_created=false`；
- bundle/workspace/deployment 均 `reused=true`；
- fingerprint 与 Profile 路径不变；
- `start.status=already_running`、`health_verified=true`、open `status=ready`；
- 第二次命令约耗时 175 秒，未重建任何 release，但续接路径仍有性能优化空间，不能宣称重复运行是瞬时的。

通过正常命令停止：

```text
FIRECLAW_HOME=/tmp/fireclaw-stage23-live-final ... python -m fireclaw_core stop --json
status=stopped, pid=152501
```

另一次 live `/readiness` 查询得到 schema version 2，runtime identity 明确为 `simulation`、版本化 Profile、
Profile SHA/revision、`gazebo_turtlebot3` 与 deployment fingerprint；robot readiness 来源为
`robot_gateway_state_probe` 且为 fresh/online。physical stop 与 emergency stop 的 value 保持 `null`、freshness
保持 `unknown`，没有将仿真在线错误表述为物理安全证据。

### 验证结果

静态与定向验证：

```text
git diff --check                                                PASS
Python compile()（9 个改动生产模块，无 pyc 写入）              PASS
node --check src/fireclaw_core/web_console/app.js                PASS
simulation runtime/bundle/serve regression                      13 passed
阶段 2 定向集                                                    37 passed
runtime identity/readiness                                      11 passed
Gateway/Web/serve 定向集                                        31 passed
真实 Chrome browser E2E                                         3 passed
受影响回归子集                                                  105 passed
```

最终全量测试：

```text
PYTHONPATH=src:. /srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q
2364 passed, 8 skipped in 281.26s (0:04:41)
```

最终产物目录：`/tmp/fireclaw-stage23-final-20260820`

```text
fireclaw-0.1.0-py3-none-any.whl
908087 bytes, 248 files
SHA-256 f9deb8921f5ed319c3735bb0d503678faa49bd3fd80ea2942b2ac5c2ccf07290

fireclaw-sim-turtlebot3-burger-v1.tar.gz
8749596 compressed bytes, 34482986 unpacked bytes, 652 payload files
SHA-256 5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e

Wheel PASS
Simulation Bundle PASS
Isolated Smoke PASS
Verdict: ALL PASSED
```

### 文件与状态

主要生产文件：

- `src/fireclaw_core/infra/simulation_runtime.py`
- `src/fireclaw_core/infra/user_setup.py`
- `src/fireclaw_core/infra/daemon_manager.py`
- `src/fireclaw_core/mission/runtime_identity.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/devtools/fleet_doctor.py`
- `src/fireclaw_core/web_console/app.js`、`index.html`
- package catalog/template、example template、distribution gate 与 README/getting-started/release 文档。

新增/修改测试覆盖 simulation runtime、bundle release、setup/no-motion、daemon reuse、runtime identity、readiness
JSON contract、Web projection、真实 Chrome、serve CLI 与 installed distribution smoke。

当前工作树有本轮预期的 tracked/untracked 改动；未创建 commit、未 push。阶段 2 与 3 的工程验收已闭环。
未运行真实硬件，这不是缺失验收，而是 real setup 的安全边界；真实机器人 deployment/motion 必须在后续独立
hardware acceptance 下授权。研究层面本轮是可复现性、状态可证据化与安全边界的工程基础，不构成方法创新或
论文贡献声明。

### 下一步建议

进入阶段 4：任务预览生成不可变 plan/token，显式确认后原子消费同一计划，并禁止任何直接下发绕过确认。
