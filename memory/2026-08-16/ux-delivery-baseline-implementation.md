# 跨会话执行记录：UX 交付基线全量实施与发布验收完成

**日期**: 2026-08-16
**时间戳**: 2026-08-16T23:06:00+08:00
**状态**: COMPLETED

---

## 1. 任务目标与背景
依据 [`docs/superpowers/specs/2026-08-16-ux-delivery-baseline-design.md`](file:///home/lpp/fireclaw-master/docs/superpowers/specs/2026-08-16-ux-delivery-baseline-design.md) 与 [`docs/superpowers/plans/2026-08-16-ux-delivery-baseline.md`](file:///home/lpp/fireclaw-master/docs/superpowers/plans/2026-08-16-ux-delivery-baseline.md)，将 FireClaw 已有的 UX 功能固化为**可安装、可审计、可重复验证的独立发布基线**：
1. 解决 core wheel 缺失 Web 静态资源与 setup 模板的问题；
2. 解决 95 MiB 仿真/编译目录无法发布的问题，产出确定性版本化的 simulation sidecar bundle；
3. 建立 OpenClaw 风格的 release check 与隔离 venv 安装 smoke 门禁；
4. 规范文件分类与 `.gitignore`，消除 `git diff --check` 空格告警。

---

## 2. 核心实施成果 (Tasks 0 ~ 6)

### Task 0: 冻结并分类基线
- 记录 HEAD `d6ba36b`，全量测试基线 `2312 passed, 8 skipped`；
- 更新 `.gitignore` 忽略 `.superpowers/`, `dist/`, `build/`, `*.whl`, `*.tar.gz`；
- 建立产品源码、测试、用户文档与运行时数据的明确分类表。

### Task 1: Package-owned Setup 资源
- **资源目录**: 创建 `src/fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml` 与 `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`；
- **资源接口**: 实现 `load_setup_template(template_id)` 与 `load_simulation_bundle_catalog(bundle_id)`；
- **配置声明**: 在 `pyproject.toml` 中配置 `[tool.setuptools.package-data]` 显式包含 setup 模板与 bundle catalog；
- **安装解耦**: `user_setup.py` 优先使用 package-owned 模板生成 Profile。

### Task 2: Web Console 成为真实 Wheel Resource
- **白名单读取接口**: 在 `src/fireclaw_core/web_console/__init__.py` 实现 `read_web_console_asset(name) -> tuple[bytes, str]`，严格限定 `index.html`, `style.css`, `app.js` 并防范路径穿越；
- **网关路由解耦**: `mission_gateway.py` 改造 `/`、`/console` 和 `/static/*` 路由，完全通过 `read_web_console_asset` 读取资源，彻底移除对源码文件路径的直接依赖；
- **打包声明**: `pyproject.toml` 中增加 `"fireclaw_core.web_console" = ["*.html", "*.css", "*.js"]`。

### Task 3: 确定性 TurtleBot3 Simulation Bundle
- **构建工具**: 实现 `tools.release.simulation_bundle`；
- **确定性归档**: 固定 `mtime=1700000000`, `uid=0`, `gid=0`, `uname=root`, `gname=root`，归档成员字母序排序，输出 `manifest.json` 记录全量文件 SHA-256、size 与 mode；
- **纯净源码**: 过滤 `build/`, `devel/`, `logs/`, `__pycache__`, `acceptance/` 及编译缓存；
- **预算控制**: 产物 652 个文件，压缩体积 8.34 MiB（上限 16 MiB），解压体积 32.89 MiB（上限 64 MiB），连续两次构建 SHA-256 完全一致 (`1d30d7ca325f5df25cf4715256288cbbd1f0653ec5b98a565d34cbfba8e9b6a2`)。

### Task 4: OpenClaw 风格 Release Check 与隔离安装 Smoke
- **分发检查器**: 实现 `tools.release.distribution_check`，检查 wheel / bundle 的 required/forbidden 规则、path safety 及 metadata；
- **隔离安装 Smoke**: 创建临时 venv，以 `--no-deps` 安装 wheel，清空 `PYTHONPATH`，在空工作目录中执行 6 个 CLI help 命令与 package resource 读取校验；
- **分发契约文档**: 创建 `docs/release/distribution-contract.md`。

### Task 5: 文档收敛与工作树卫生
- 修复 `README.md:146` 尾随空格，`git diff --check` 检查通过 (0 errors)；
- 明确区分源码验证行为与下一阶段“安装后自动构建仿真工作区”的演进边界。

### Task 6: 全量验收与交接
- **全量测试通过**: `2326 passed, 8 skipped in 211.22s`；
- **发布检查通过**: Core Wheel (`865.9 KiB`) 与 Simulation Bundle (`8.34 MiB`) 及隔离 Smoke 全部通过。

---

## 3. 核心 Artifact 指纹与度量数据

| 产物名称 | 文件路径 | SHA-256 校验和 | 文件数 | 压缩体积 | 解压体积 | 校验结果 |
|---|---|---|---|---|---|---|
| **Core Wheel** | `fireclaw-0.1.0-py3-none-any.whl` | `4ecfaf62ea72a96cc3846c2558acc35c1b26f6c167fab65e386b59088a8e33c5` | 246 | 865.9 KiB | 3.42 MiB | ✅ PASS |
| **Simulation Bundle** | `fireclaw-sim-turtlebot3-burger-v1.tar.gz` | `1d30d7ca325f5df25cf4715256288cbbd1f0653ec5b98a565d34cbfba8e9b6a2` | 652 | 8.34 MiB | 32.89 MiB | ✅ PASS |

---

## 4. 关键验收命令与复现步骤

```bash
# 1. 运行发布资源与分发检查全套单元测试
PYTHONPATH=src:. pytest -q \
  tests/test_distribution_resources.py \
  tests/test_simulation_bundle_release.py \
  tests/test_distribution_release_check.py

# 2. 真实构建与 OpenClaw 风格发布检查 + 隔离安装 Smoke
TMP_DIR=$(mktemp -d)
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir "$TMP_DIR"
python -m tools.release.simulation_bundle --repo-root . --output-dir "$TMP_DIR"
python -m tools.release.distribution_check \
  --wheel "$TMP_DIR"/fireclaw-0.1.0-py3-none-any.whl \
  --simulation-bundle "$TMP_DIR"/fireclaw-sim-turtlebot3-burger-v1.tar.gz
rm -rf "$TMP_DIR"

# 3. 运行全量回归测试
PYTHONPATH=src:. pytest -q
# 结果: 2326 passed, 8 skipped in 211.22s
```

---

## 5. 物理安全与操作说明
- **硬件安全承诺**: 本轮为工程交付与发布基线工作，未启动 ROS Master、Gazebo 或任何实体/仿真机器人执行机构。
- **阶段交接**: 发布基线已完全具备，下一阶段（首次使用一键闭环：自动物化 bundle 并构建 ROS 工作区）可在此稳定基线上开展。
