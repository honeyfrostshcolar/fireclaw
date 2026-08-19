# FireClaw 续作状态审计与下一步

## 2026-08-19T23:04:23+08:00

### 任务目标

恢复此前的 FireClaw UX/发布基线工作，依据最近的持久记录、Git 状态和聚焦验证判断当前真实进展及下一步优先级。本轮只做审计、验证和续作记录；不 stage、不 commit、不启动 Gateway、ROS、Gazebo 或机器人。

### 已读取记录

- 最近两个日期目录：
  - `memory/2026-08-17/ux-delivery-baseline-review-fixes.md`
  - `memory/2026-08-16/ux-delivery-baseline-{implementation,planning,review}.md`
  - `memory/2026-08-16/ux-roadmap-completion-audit.md`
- 因最近记录明确引用 2026-08-14 的实施来源，进一步读取：
  - CLI progressive disclosure、daemon runtime manager、UX lifecycle verification；
  - Web Console MVP、zero-handwritten config、friendly errors 的完成记录。

### 当前进展

- 第一阶段“可发布交付基线”的审查阻断项已在 2026-08-17 修复；当时真实 wheel、simulation bundle、isolated smoke 和全量 pytest 均通过。
- 当前 HEAD 仍为 `d6ba36b`（2026-08-13），分支 `agent/embodied-evaluation-collision-calibration` 相对其远端领先 6 个提交，且没有 staged changes。
- 2026-08-14 至 2026-08-17 完成的生命周期 CLI、Web Console、零手写配置、友好错误、package resources、release tools、负向安全测试和文档仍主要存在于工作树；大量关键文件为 untracked，因此 clean clone 仍无法复现本机成果。
- tracked diff 当前为 29 个文件、`1867 insertions(+), 242 deletions(-)`；另有大量 untracked 源码、测试、规划文档和 memory 记录。
- `data/robots/gazebo_turtlebot3/memory-runtime.sqlite3` 是运行时数据库，必须排除在产品提交之外。
- `git diff --check` 当前无输出。

### 本轮命令与结果

```bash
find memory -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r | head -2
git status --short --branch
git diff --stat
git diff --cached --stat
git log --date=iso-local --pretty=format:'%h %ad %d %s' -12
git diff --check
git ls-files --others --exclude-standard | sort
git diff --name-status
node --check src/fireclaw_core/web_console/app.js
PYTHONPATH=src:. /srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_distribution_resources.py \
  tests/test_simulation_bundle_release.py \
  tests/test_distribution_release_check.py
```

结果：

- JavaScript syntax check：PASS；
- 发布资源、simulation bundle 与 distribution gate 聚焦测试：`25 passed in 20.46s`；
- 未运行全量 pytest，因为最后记录已有 `2339 passed, 8 skipped` 的完整证据，而当前首先需要解决的是版本化边界，不是重复长回归。

### 当前问题

1. 最大阻断不是新的功能缺陷，而是成果尚未形成明确、可复现的 Git change set；继续开发会进一步扩大不可审计的脏工作树。
2. 多个功能共同修改 `mission_cli.py`、`mission_gateway.py` 和 Web 静态资源，事后按功能硬拆 patch 容易产生测试不通过或安全语义不完整的中间提交。
3. 默认 Robot Adapter 尚未生产权威 `robot.stopping` 与带 `stop_evidence` 的 `robot.stopped_confirmed` / `task.stopped` 事件；Web 目前会安全地保持“停止未确认”。
4. 原始 UX 路线仍有产品级缺口：wheel-install 首次任务 E2E、active Profile/Web 贯通、preview-to-execution 不可变合同、配置真实连通性/安全保存、浏览器 E2E 等。

### 当前结论

第一阶段可以表述为“本地实现与验证完成，尚未版本化”。下一步必须先固化经过验证的集成基线，而不是直接添加新功能。鉴于共享文件高度耦合，优先建立一个完整、可测试的 UX delivery baseline checkpoint，比追溯拆分成多个可能不自洽的功能提交更安全。

### 下一步推荐顺序

1. 制定 proposed change set：纳入 UX/config/errors/resources/Web/release tools、对应 tests/docs/memory 和必要的兼容性改动；明确排除 `data/robots/**` 运行时数据库、凭据、日志和生成物。
2. 在提交前为运行时数据库补充精确 `.gitignore` 规则，逐项审查 proposed change set；不得使用宽泛 `git add .`。
3. 对最终候选集重跑 `git diff --check`、全量 pytest、wheel + simulation bundle + isolated distribution check。
4. 仅在用户明确授权后 stage/commit；建议保留一个可复现的集成检查点，不强行拆出不能独立通过的历史中间状态。
5. 基线固化后，下一工程阶段优先实现 Robot Adapter 权威停止证据 producer 及其仿真/实机契约测试；开始设计前先用 CodeGraph 检查 FireClaw 当前事件链和 OpenClaw analogue。
6. 随后补 P0 产品级 E2E 和 preview-to-execution 合同，再收敛 active Profile/配置保存，最后开展支持包、移动端只读、教学流程与真实用户研究。

### 研究层面判断

- 当前发布基线主要提升工程正确性、供应链可审计性与实验可复现性，本身不足以构成论文核心创新。
- 权威停止证据、传感器不确定性下的 fail-safe 状态机及可审计的人机确认合同，若形成形式化安全属性、对照基线和仿真/实机系统评估，才可能发展为研究贡献；仅增加事件名或 UI 三态仍属于工程实现。

### 需要运行的命令

建立提交候选集后：

```bash
git status --short
git diff --check
PYTHONPATH=src:. /srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q
```

随后按 `docs/release/distribution-contract.md` 重新构建 wheel、simulation bundle，并执行完整 distribution check。禁止在未审查清单前使用 `git add .`。
