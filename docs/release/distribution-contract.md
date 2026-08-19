# FireClaw 发布产物与分发契约 (Distribution Contract)

## 1. 架构拓扑与发布边界

FireClaw 发布分为两个正交的交付产物：

| 产物名称 | 格式 | 职责范围 | 体积预算 |
|---|---|---|---|
| **Core Wheel** (`fireclaw-*.whl`) | 标准 Python wheel | `fireclaw_core` 与 SDK Python 模块、Web Console 静态资源（HTML/CSS/JS）、Package-owned setup 模板、Simulation catalog 元数据、CLI 入口点 | ≤ 4 MiB；实际大小以当次 Release Check 输出为准 |
| **Simulation Bundle** (`fireclaw-sim-*.tar.gz`) | 确定性 gzip tarball | ROS1 导航与 TurtleBot3 仿真运行源码、Plugin manifest、Launch/Map/World 配置与 upstream provenance | ≤ 16 MiB 压缩 / ≤ 64 MiB 解压；实际大小以当次 Release Check 输出为准 |

---

## 2. 必需与禁入规范 (Required & Forbidden Rules)

### Core Wheel 必需项:
- `fireclaw_core/__init__.py`
- `fireclaw_core/__main__.py` (CLI 入口)
- `fireclaw_core/web_console/index.html`
- `fireclaw_core/web_console/style.css`
- `fireclaw_core/web_console/app.js`
- `fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml`
- `fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
- `console_scripts` 声明 `fireclaw = fireclaw_core.__main__:main`

### Core Wheel 禁入项:
- 任何 ROS 编译产物 (`build/`, `devel/`, `install/`)
- 运行时日志与测试输出 (`logs/`, `results/`, `*.jsonl`)
- 真实机器人运行时数据 (`data/robots/`)
- 参考与开发子目录 (`openclaw/`, `.git/`, `.codegraph/`, `.superpowers/`)
- 二进制与字节码缓存 (`*.pyc`, `*.so`, `*.o`, `*.a`)
- 凭据、私钥或 Token (`credentials`, `secret.token`)

### Simulation Bundle 完整性门禁:

- bundle ID、版本、文件名必须与 wheel 内置 catalog 一致；CLI 不接受工作树中的另一份 catalog 作为发布依据。
- archive 文件集合必须与 `manifest.json` 双向完全一致；额外文件、缺失文件、重复成员、链接、设备文件和不安全路径均失败。
- 每个文件必须同时匹配 SHA-256、字节数和规范化权限（`0o644` / `0o755`）。
- catalog 声明的 required paths、上游 commit、license 标识及 provenance/license 证据文件必须全部存在。
- 压缩、解压体积以及成员数量在读取内容前执行上限检查，避免将异常归档当作普通发布产物处理。
- `--skip-smoke` 只用于诊断；缺少 bundle 或 smoke 时，最终 verdict 必须失败，不能显示 `ALL PASSED`。

---

## 3. 校验与验证命令

```bash
set -euo pipefail
shopt -s nullglob

# 0. 显式选择 Python；必须为 3.10 或更新版本
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), sys.version'

# 1. 在独立临时目录中构建 Wheel 与 Simulation Bundle
ARTIFACT_DIR="$(mktemp -d -t fireclaw-release.XXXXXX)"
"$PYTHON_BIN" -m pip wheel . --no-deps --no-build-isolation --wheel-dir "$ARTIFACT_DIR"
"$PYTHON_BIN" -m tools.release.simulation_bundle --repo-root . --output-dir "$ARTIFACT_DIR"

# 2. 运行完整 Release Check 与隔离安装 Smoke 验证
WHEEL_PATH=("$ARTIFACT_DIR"/fireclaw-*.whl)
BUNDLE_PATH=("$ARTIFACT_DIR"/fireclaw-sim-*.tar.gz)
test "${#WHEEL_PATH[@]}" -eq 1
test "${#BUNDLE_PATH[@]}" -eq 1
"$PYTHON_BIN" -m tools.release.distribution_check \
  --repo-root . \
  --wheel "${WHEEL_PATH[0]}" \
  --simulation-bundle "${BUNDLE_PATH[0]}"

# 3. 记录 Release Check 输出与 SHA-256 后，再清理这个明确的临时目录
rm -r -- "$ARTIFACT_DIR"
```

上述两个 release tool 会从 `--repo-root/src` 引导仓库源码，因此可由未安装 `fireclaw` 的干净 Python venv 运行；这不替代 wheel 的隔离安装 smoke。
