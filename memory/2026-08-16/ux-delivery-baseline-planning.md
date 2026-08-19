# UX 路线第一步：可发布交付基线规划记录

## 2026-08-16T22:43:05+08:00

### 任务目标

用户要求沿 Superpowers 步骤流规划 UX 路线的第一步。本轮只做现状复核、架构收敛和实施计划，不修改业务代码，不启动仿真/机器人，不提交或推送。

### 当前进展

- 已读取最近两天相关 memory 与当前 git status；
- 已完成 UX 路线现状审计，结论见 `memory/2026-08-16/ux-roadmap-completion-audit.md`；
- 已检查当前 wheel、setup 资源定位、Web 静态资源定位、部署 plan/apply 调用链和 TurtleBot3/Navigation 工作区；
- 已检查 OpenClaw 发布 analogue；
- 已产出第一步设计稿与逐任务实施计划。

### 已完成

新增规划文档：

- `docs/superpowers/specs/2026-08-16-ux-delivery-baseline-design.md`
- `docs/superpowers/plans/2026-08-16-ux-delivery-baseline.md`

### 关键命令

```bash
git status --short
rg --files docs/superpowers/specs docs/superpowers/plans memory/2026-08-16
codegraph explore "Show the current source and call flow for build_deployment_plan and apply_deployment..."
codegraph explore "Show exact current source for _materialize_release..."
du -sh examples/setup_templates extensions/navigation-move-base robots/turtlebot3_burger src/fireclaw_core/web_console
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir <temporary-dir>
```

另使用显式 source-only allowlist 做了只写 `/tmp` 的压缩体积探测：

```text
/tmp/fireclaw-sim-assets-probe.s685uL/turtlebot3-source-assets.tgz
8851187 bytes (8.5 MiB)
```

### 文件与符号检查

FireClaw：

- `pyproject.toml`
- `src/fireclaw_core/infra/user_setup.py`
  - `setup_fireclaw`
  - `_resolve_simulation_source_root`
  - `_ensure_generated_simulation_profile`
- `src/fireclaw_core/mission/mission_gateway.py`
  - `_handle_get`
- `src/fireclaw_core/deployment/deployer.py`
  - `build_deployment_plan`
  - `apply_deployment`
  - `_materialize_release`
  - `_validate_profile_files`
  - `_discover_source_packages`
- `src/fireclaw_core/deployment/runtime_descriptor.py`
  - `load_plugin_runtime_descriptor`
  - `_parse_provider`
- `examples/setup_templates/gazebo_turtlebot3.toml`
- `extensions/navigation-move-base/fireclaw.plugin.json`
- `extensions/navigation-move-base/runtime/fireclaw.runtime.json`
- TurtleBot3 与 Navigation 的 upstream/provenance 文件。

OpenClaw analogue：

- `openclaw/package.json`：显式 `files` allowlist/exclusions，`prepack`、`release:check`；
- `openclaw/scripts/release-check.ts`：required、forbidden、forbidden content、size 与 packed smoke；
- `openclaw/test/release-check.test.ts`：安装后 CLI smoke 和 release contract 测试。

### 观察结果

1. 当前 `pyproject.toml` 仅发现 Python packages，未显式包含 package data。
2. 审计 wheel 有 240 个条目，但没有 Web HTML/CSS/JS 和 setup/robot assets。
3. Web Console 约 156 KiB，适合直接进入 core wheel。
4. 两个 ROS 工作区当前约 95 MiB，包含本机 `build/`、`devel/`，不能整体打包。
5. 显式 source-only 候选约 36 MiB，gzip 后约 8.5 MiB，适合作为独立、版本化 simulation sidecar bundle。
6. `setup_fireclaw()` 仍依赖源码根，并要求机器人工作区 `devel/setup.bash`；因此“安装后自动物化/构建/续接”必须作为下一阶段实现，不能在本阶段文档里伪装成已完成。
7. 当前 deployer 会把 source packages symlink 到 content-addressed release workspace 后运行 `catkin_make install`；sidecar bundle 必须提供稳定真实路径，不能依赖短生命周期 resource context。
8. 当前 dirty worktree 有大量既有修改和 untracked UX 文件，实施时必须显式分类，禁止批量 add。

### 当前结论

第一步应定义为“可发布交付基线”，而不是继续补功能。发布拓扑采用：

- core wheel：Python + Web + package-owned setup template + simulation catalog；
- sidecar bundle：TurtleBot3/Navigation 运行源码、配置、模型和许可证；
- release check：直接验证 wheel/bundle，并在空工作目录做安装后只读 smoke。

这个切分保留后续的一键体验，同时避免把 95 MiB 本机编译目录和第三方源码无差别塞进 core wheel。

### 当前问题与风险

- 当前会话没有暴露名为 `superpowers` 的可调用 Skill；已使用仓库既有 Superpowers 文档格式与 TDD/checkpoint 流程替代，未声称实际调用该 Skill。
- bundle 的自动获取、原子物化和 ROS 构建续接尚未实现，属于第二阶段。
- package-owned 模板与 `examples/` 兼容镜像需要 parity test，防止内容漂移。
- 第三方源码进入 bundle 前必须检查许可证与 provenance 完整性。
- 隔离 venv smoke 使用 `--no-deps`，只证明不依赖仓库源码；完全离线依赖闭包需要安装/升级向导阶段另行解决。

### 下一步

等待用户确认设计和实施边界。确认后从计划 Task 0 开始，先记录基线与文件分类，再按 TDD 顺序实施。不得直接跳到自动启动或 Web 行为修复。

### 需要运行的命令

实施开始时：

```bash
date -Is
git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
```

最终验收命令已经写入 `docs/superpowers/plans/2026-08-16-ux-delivery-baseline.md`。

## 2026-08-16T22:47:27+08:00 校对更新

- 设计稿 213 行、实施计划 435 行、规划记录 118 行（更新前计数），文件均非空；
- 三个新增文档未检测到尾随空格；
- 本轮没有运行 pytest，因为只新增规划文档，没有修改产品或测试代码；
- `git diff --check` 仍只报告审计时已知的两处既有问题：`README.md:146` 和 `src/fireclaw_core/mission/mission_gateway.py:365`；计划已把它们纳入实施阶段 Task 5，本轮未擅自修改现有业务 diff；
- 没有 stage、commit、push，也没有启动 Gateway、ROS、Gazebo 或机器人。
