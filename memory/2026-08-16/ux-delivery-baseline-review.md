# UX 可发布交付基线验收审查

## 2026-08-16T23:19:33+08:00

### 任务目标

用户表示已经完成 `docs/superpowers/plans/2026-08-16-ux-delivery-baseline.md`，要求检查结果。本轮只做审查和验证，不修改产品代码、测试或用户文档。

### 审查结论

状态：**CHANGES REQUIRED**。

正常发布路径已经基本建立并可运行：core wheel 确实包含 Web/setup 资源，simulation bundle 可确定性构建，完整参数下的 distribution check 与 installed smoke 通过，全量测试在允许本地 socket 的环境中为 `2326 passed, 8 skipped`。

但当前还不能把第一阶段标记为完全完成。release/security contract 存在可复现漏检，文档仍有安全状态过度声明，且新增实现文件仍全部 untracked。

### 已验证通过

1. 新增聚焦测试：

   ```text
   13 passed in 12.71s
   ```

2. 真实构建产物：

   ```text
   /tmp/fireclaw-delivery-review.gqJK6o/fireclaw-0.1.0-py3-none-any.whl
   886686 bytes
   SHA-256 a5892a85a481dc0860425785c6c3dabc8c8a55e1f4f07fbf24eb116192d1afa0
   246 files

   /tmp/fireclaw-delivery-review.gqJK6o/fireclaw-sim-turtlebot3-burger-v1.tar.gz
   8749516 bytes
   SHA-256 1d30d7ca325f5df25cf4715256288cbbd1f0653ec5b98a565d34cbfba8e9b6a2
   652 files
   34482985 unpacked bytes
   ```

3. 完整参数下的 release check：wheel PASS、bundle PASS、isolated smoke PASS。

4. wheel 已确认包含：

   - `fireclaw_core/web_console/index.html`
   - `fireclaw_core/web_console/style.css`
   - `fireclaw_core/web_console/app.js`
   - `fireclaw_core/resources/setup_templates/gazebo_turtlebot3.toml`
   - `fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`

5. 全量测试：沙箱内由于禁止创建本地 socket 得到 `172 failed, 2154 passed, 8 skipped`，失败均表现为 `PermissionError: [Errno 1] Operation not permitted`。按权限流程在沙箱外复跑后：

   ```text
   2326 passed, 8 skipped in 209.43s
   ```

   因此 172 个沙箱失败不属于代码回归。

### 必须修复的问题

#### 1. Package resource ID 存在路径穿越

位置：

- `src/fireclaw_core/resources/__init__.py:18-30`
- `src/fireclaw_core/resources/__init__.py:33-46`

`template_id` 只替换 `-`，`bundle_id` 只 strip；两者都允许 `/` 和 `..` 进入 `joinpath()`。

实测：

```text
load_setup_template("../../../../pyproject")
=> 成功读取 pyproject.toml，首行为 [build-system]

load_simulation_bundle_catalog("../../../../extensions/navigation-move-base/fireclaw.plugin")
=> 成功读取 Plugin JSON，id 为 fireclaw.navigation.move-base
```

需要使用固定 ID allowlist 或严格正则，并验证解析结果仍是目标资源目录的直接子文件。

#### 2. Simulation bundle inspector 接受 manifest 外的额外文件

位置：`tools/release/simulation_bundle.py:248-321`。

检查器只验证 manifest 声明的文件是否存在及 checksum，不验证 archive 中的每个普通文件都被 manifest 覆盖，也不拒绝重复 member。

对抗性 tar 中加入未写入 manifest 的 `unreviewed-extra.txt` 后：

```text
valid_with_unmanifested_extra = True
errors = []
```

此外检查器不比较 manifest 的 `schema_version`、`bundle_id`、`bundle_version`、`total_files`、`unpacked_bytes`、file size/mode；catalog 中的 provenance/LICENSE 也不是 required paths。当前完整性门不能实现设计中“逐文件审计”和许可证门禁。

#### 3. Release check 可在未检查 bundle/smoke 时返回 ALL PASSED

位置：`tools/release/distribution_check.py:277-305`。

对真实有效 wheel 实测：

```text
run_full_distribution_check(valid_wheel, None, None, run_smoke=False).all_passed
=> True

run_full_distribution_check(valid_wheel, bundle_path, None, run_smoke=False).all_passed
=> True
```

传入 bundle path 但漏传 catalog 时会静默跳过。CLI 也把 `--simulation-bundle` 设为 optional，并允许 `--skip-smoke` 后仍显示 ALL PASSED。完整 gate 应要求全部组件；部分检查需要明确输出 PARTIAL/SKIPPED，不能复用完成 verdict。

#### 4. 文档中的物理停止语义仍然过度声明

位置：

- `README.md:115-153`
- `docs/getting-started/first-run-setup.md:28-100`
- `src/fireclaw_core/mission/mission_gateway.py:1716-1732`
- `src/fireclaw_core/web_console/app.js:879-886`

文档声称 cancelling 表示底盘已经收到制动反馈、cancelled/stopped 表示速度归零并确认停止。但 Gateway 在普通 cancel result 后自行发布 `task.cancelling`/`stopping`；前端又把 `mission.cancelled` 直接映射成 `stopped_confirmed`。这里没有权威 odometry/driver stop evidence。

这与第一阶段 Task 5 的“删除未经 artifact/权威状态验证的完成声明”不符，也违反 UX 原始要求中三种取消状态必须严格区分的安全边界。应先降级文档和 UI 文案，后续再接入权威停止证据。

#### 5. Release 工具依赖开发机的 editable/PYTHONPATH 状态

位置：

- `tools/release/simulation_bundle.py:332`
- `tools/release/distribution_check.py:319`
- `docs/release/distribution-contract.md:38-47`

文档命令直接使用 `python -m tools...`，当前登录 shell 没有 `python` 命令。更重要的是，在没有 system-site-packages 或 editable FireClaw 的新 venv 中，从仓库根执行工具会失败：

```text
ModuleNotFoundError: No module named 'fireclaw_core'
```

应让工具从 repo-root 显式读取 catalog，或提供安装后的 release-tool entry point；至少文档和 CI 命令必须使用确定解释器并显式处理 `src` layout，不能依赖 ambient install。

### 测试与卫生缺口

- `tests/test_simulation_bundle_release.py:71-81` 的 `test_bundle_rejects_forbidden_segments_and_extensions` 只有注释，没有断言，实际是空测试。
- 计划要求的 path traversal、symlink/hardlink/device、duplicate member、manifest mismatch、size/mode、provenance/license 等负向用例大多未实现。
- `git diff --check` 只检查 tracked diff；新增 untracked 文件仍有尾随空格，例如 `src/fireclaw_core/resources/__init__.py:22`。实施记录中的“0 errors”没有覆盖新文件。
- `docs/release/distribution-contract.md:9` 写 core wheel 当前约 250 KiB，真实构建为 865.9 KiB。
- `src/fireclaw_core/resources/`、`tools/`、新增 tests/docs/Web/config/errors 等仍显示为 `??`。按此前约定本轮不 stage 是正确的，但在进入正式提交前必须建立显式 proposed change set，否则 clean clone 不包含这些实现。

### 文件检查

- `pyproject.toml`
- `.gitignore`
- `src/fireclaw_core/resources/__init__.py`
- `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
- `src/fireclaw_core/web_console/__init__.py`
- `src/fireclaw_core/infra/user_setup.py`
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/web_console/app.js`
- `tools/release/simulation_bundle.py`
- `tools/release/distribution_check.py`
- 三个新增 distribution tests
- `README.md`
- `docs/getting-started/first-run-setup.md`
- `docs/release/distribution-contract.md`
- `memory/2026-08-16/ux-delivery-baseline-implementation.md`

### 下一步建议

先做一个限定范围的 review-fix，不进入第二阶段：

1. 修复两个 resource loader 的 ID validation；
2. 加固 bundle manifest completeness、catalog/schema/provenance 和 bounded streaming；
3. 让 full/partial release verdict 不可混淆；
4. 补齐所有负向测试，删除空测试；
5. 修正文档中的物理安全过度声明和命令可复现性；
6. 重新跑聚焦测试、真实 artifacts、isolated smoke、沙箱外全量 pytest；
7. 给用户一份明确的 track/commit 候选清单，但不擅自 stage/commit。

### 物理安全

审查过程没有启动 ROS、Gazebo、Gateway 或机器人。distribution installed smoke 只执行 CLI help 与 package resource 读取。
