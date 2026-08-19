# UX 路线第一步：可发布交付基线设计

> 状态说明：本文是当时的设计/实施记录，不是当前产品能力声明。当前闭环边界与剩余缺口以 `memory/2026-08-19/seven-stage-ux-closure-audit.md` 及后续记录为准。

日期：2026-08-16

状态：Draft — 待确认后实施

主题：先把现有 UX 能力变成可安装、可审计、可重复验证的发布基线

## 1. 背景

当前仓库已经存在 `setup/start/open/status/stop`、Web Console、零手写配置和友好错误等实现骨架，但“源码树里能运行”还不等于“用户安装后能使用”。本次审计得到的直接证据是：

- 全量测试当前为 `2313 passed, 8 skipped`，说明源码级回归基线较稳定；
- 现有 wheel 共 240 个条目，但 UX 静态资源中只包含 `web_console/__init__.py`，缺少 `index.html`、`style.css`、`app.js` 和 setup 资源；
- `pyproject.toml` 只配置了 Python package discovery，没有声明 package data；
- `setup_fireclaw()` 仍通过源码仓库根目录定位模板、Plugin、机器人 launch 和 map；
- TurtleBot3 与 Navigation 两个工作区当前约 95 MiB，其中混有不可发布的 `build/`、`devel/` 产物；只保留运行所需源码和元数据后，候选仿真资源约 36 MiB，压缩后约 8.5 MiB；
- 多个本轮 UX 源码、测试和文档仍是 untracked，且 `git diff --check` 还有两处尾随空格。

因此，第一步不继续叠加新功能，而是先建立可发布事实基线。否则后续即使把 `setup -> start -> open` 串起来，最终用户安装的 wheel 仍然拿不到相应资源。

## 2. 目标

第一步完成后，必须能够证明：

1. FireClaw core wheel 在脱离仓库工作目录后可以安装并执行 CLI 帮助；
2. Web Console 的 HTML/CSS/JS 确实存在于 wheel 中，并能通过统一资源接口读取；
3. setup 模板和仿真资源目录清单具有明确的包内所有权；
4. TurtleBot3 仿真依赖以确定性、版本化、可校验的 sidecar bundle 产出，不携带本机编译结果；
5. 发布检查会同时拒绝“缺必需文件”和“误带敏感/生成文件”；
6. 文档只描述已经由发布产物验证过的能力；
7. 当前工作树中的产品文件、测试、文档和运行数据被明确分类，不通过 `git add .` 混入提交。

这一阶段是发布与工程正确性工作，不引入新的研究方法，也不构成论文创新。它对研究的价值是提高实验环境可复现性，避免源码环境与用户安装环境不一致。

## 3. 非目标

本阶段明确不完成以下事项：

- 不让 `fireclaw setup` 自动构建 ROS 工作区、启动 Gazebo、启动 Gateway 或打开浏览器；
- 不实现仿真 bundle 的联网下载、升级和回滚；
- 不实现实机自动发现，也不触发任何实机致动；
- 不修复 Web Console 的任务确认、取消三态或恢复中心权威语义；
- 不继续扩展零配置 Schema、移动端或支持包；
- 不启动 ROS、Gazebo 或机器人做验收。

这些分别属于后续“首次使用闭环”“Web 安全语义”“零手写配置”和“后续体验”阶段。

## 4. OpenClaw analogue 与采用方式

已检查的 OpenClaw 对应实现：

- `openclaw/package.json` 的 `files` 明确列出 `dist/` 并排除不应发布的构建内部文件；
- `openclaw/scripts/release-check.ts` 对 pack 结果同时检查 required paths、forbidden paths、forbidden content 和 unpacked size；
- `openclaw/test/release-check.test.ts` 固定 packed CLI smoke 命令，并验证隔离安装环境；
- `prepack` 与 `release:check` 是独立发布门，而不是依赖开发机上的源码测试间接保证。

FireClaw 复用这一结构：显式发布清单、独立 release check、安装后 smoke、必需/禁入双向约束。差异是 FireClaw 还需要处理 ROS 源码、第三方许可证、仿真/实机隔离和 tar 路径安全，因此不会直接照搬 npm 的文件布局。

## 5. 发布物拓扑

### 5.1 Core wheel

`fireclaw-<version>-py3-none-any.whl` 包含：

- `fireclaw_core` 与 `fireclaw_plugin_sdk` Python 代码；
- `fireclaw_core.web_console` 的 `index.html`、`style.css`、`app.js`；
- package-owned 的 credential-free setup 模板；
- TurtleBot3 bundle catalog/manifest；
- 标准项目元数据、许可证和 CLI entry point。

Core wheel 不包含：

- ROS `build/`、`devel/`、`logs/`；
- `data/robots/`、任务日志、memory 记录或本地 Profile；
- `openclaw/` 参考源码、`.git/`、`.codegraph/`；
- token、证书、`.env`、真实站点地图或私有网络配置；
- 整个仿真源码 bundle。

### 5.2 TurtleBot3 simulation bundle

第一版 sidecar 命名为：

`fireclaw-sim-turtlebot3-burger-v1.tar.gz`

它保留当前 Profile 期待的相对布局：

```text
bundle-root/
  examples/ros1_configs/
  extensions/navigation-move-base/
  robots/turtlebot3_burger/
```

bundle 只允许显式清单中的运行源码、Plugin manifest/entrypoint、Skill、Runtime descriptor、默认参数、launch、地图/模型以及上游许可证与 provenance 文件。禁止递归打包整个目录，也禁止加入编译产物、测试输出、缓存和符号链接。

首阶段只负责稳定地产出和验证该 bundle。下一阶段再让已安装的 `fireclaw setup` 自动取得、校验、物化和构建它。这样可以在不把 95 MiB 本机工作区塞进 core wheel 的前提下，保留最终的一键体验。

## 6. 资源所有权

### 6.1 Setup 模板

package-owned 模板位于：

`src/fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml`

`setup_fireclaw()` 通过 `importlib.resources` 读取它，不再把“能找到源码仓库中的模板”当作模板存在的依据。现有 `examples/setup_templates/gazebo_turtlebot3.toml` 暂留一个兼容周期，测试要求它与 package-owned 模板完全一致，避免两份内容静默漂移。

ROS config、Plugin 和机器人资源仍由 sidecar bundle 持有，因为生成后的 Profile 必须引用可长期存在的真实文件路径，不能引用短生命周期的临时资源上下文。

### 6.2 Web Console

静态资源继续位于 `src/fireclaw_core/web_console/`。服务端改为调用该 package 提供的受限资源读取函数，只允许 `index.html`、`style.css` 和 `app.js`，不再依赖从 `mission_gateway.py` 推导源码目录。

该接口同时解决两个问题：

- wheel 安装后的资源定位；
- `/static/*` 的路径逃逸防护。

## 7. 仿真 bundle 清单与完整性

bundle catalog 是 package data，也是 release builder 的唯一输入。至少包含：

- `schema_version`、`bundle_id`、`bundle_version`；
- 兼容的 FireClaw 版本范围；
- 明确的 include paths 和 required paths；
- forbidden path segments 与 forbidden file suffixes；
- 上游仓库、commit、许可证文件位置；
- 压缩与解压体积上限。

builder 必须：

1. 只遍历 include allowlist；
2. 按 POSIX 相对路径排序；
3. 拒绝绝对路径、`..`、符号链接、硬链接、设备文件和超出根目录的文件；
4. 为每个文件记录 SHA-256、size 和规范化 mode；
5. 固定 uid/gid、用户名、组名和 mtime，使相同输入产生相同归档；
6. 把 manifest 放进归档，并在 release check 中逐文件复核；
7. 默认体积预算为压缩后不超过 16 MiB、解压后不超过 64 MiB，修改预算必须人工审查。

## 8. Release check

新增独立检查器，结构上对应 OpenClaw 的 `release-check.ts`。它不信任源码树，而是检查实际构建出的 wheel 和 bundle。

### 8.1 Wheel 检查

- 构建结果恰好有一个目标版本 wheel；
- 必需 Python 模块、CLI entry point、Web 静态资源、setup 模板和 bundle catalog 均存在；
- 禁入路径和敏感文件名不存在；
- archive member 路径安全；
- wheel 体积在已记录预算内；
- metadata 中的版本与文件名一致。

### 8.2 Bundle 检查

- 文件名、bundle ID 与版本一致；
- required paths 全部存在；
- 不存在 forbidden paths/content；
- manifest 与每个文件的 digest/size 一致；
- 不存在链接、设备文件、路径逃逸；
- 压缩与解压体积均未超预算；
- 必需的上游 provenance 和许可证存在。

### 8.3 隔离安装 smoke

在临时 venv 和空工作目录中安装 wheel，移除仓库 `PYTHONPATH`，只设置临时 `FIRECLAW_HOME`，验证：

- `fireclaw --help`；
- `fireclaw setup --help`；
- `fireclaw start --help`；
- `fireclaw open --help`；
- `fireclaw status --help`；
- `fireclaw stop --help`；
- 通过 package resource API 读取 Web Console 和 setup 模板。

smoke 不启动 Gateway、ROS、Gazebo 或机器人。依赖解析是否完全离线是安装器阶段的课题；本阶段重点证明运行时没有从当前仓库偷读代码或静态文件。

## 9. 工作树与提交边界

实施时先把当前 dirty worktree 分类：

- 产品源码；
- 对应测试；
- 用户文档与 Superpowers 计划；
- 运行时/实验数据；
- 生成产物。

产品源码、测试和用户文档进入显式变更清单；`data/robots/`、临时 wheel、bundle、ROS 编译目录和日志不得因批量 add 混入。实施过程禁止 `git add .`，并且没有用户明确授权时不 stage、不 commit、不 push。

## 10. 验收标准

第一步只有在以下条件全部满足时才算完成：

1. package-data 单元测试通过；
2. 实际 wheel 含全部 Web 静态资源、setup 模板和 bundle catalog；
3. 确定性 simulation bundle 可以连续构建两次并得到相同 SHA-256；
4. wheel 与 bundle 的 required/forbidden/integrity/size 检查全部通过；
5. 空工作目录中的隔离安装 smoke 全部通过；
6. 全量 `pytest -q` 通过，既有安全测试不退化；
7. `git diff --check` 通过；
8. README 和 first-run 文档不再暗示“安装后的 setup 已能自动构建并启动仿真”；
9. 产出一份包含命令、artifact 路径、SHA-256、文件数、体积和测试结果的 memory 记录；
10. 没有启动或致动任何机器人/仿真 Runtime。

## 11. 下一阶段接口

第二步“首次使用闭环”只能依赖本阶段产出的稳定接口：

- package-owned setup template；
- versioned simulation bundle catalog；
- 经过 release check 的 sidecar bundle；
- Web resource API；
- 可复用的安装后 smoke harness。

第二步再实现 bundle 自动定位/下载、原子物化、ROS 工作区构建续接、`setup -> start -> open` 串联以及失败恢复。
