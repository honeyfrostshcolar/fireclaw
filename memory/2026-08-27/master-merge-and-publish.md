# 合并到 master 并发布远程仓库

## 时间

- 2026-08-27T23:56:34+08:00

## 任务目标

- 将当前 `agent/embodied-evaluation-collision-calibration` 分支的已验证改动合并到 `master`，并推送到 `origin/master`。

## 当前进展

- 开始时当前分支为 `agent/embodied-evaluation-collision-calibration`，相对其远程跟踪分支领先 7 个提交。
- 工作区包含大量此前未提交的 FireClaw 实现、配置、文档、测试和执行记录；没有重置或覆盖这些改动。
- 远程地址为 `git@github.com:honeyfrostshcolar/fireclaw.git`。

## 已检查文件和范围

- 最近执行记录：`memory/2026-08-24/`、`memory/2026-08-26/`。
- 重点回归代码：`src/fireclaw_core/planner/llm_planner.py`、`src/fireclaw_core/mission/mission_deliberation.py`、`src/fireclaw_core/task/terminal_outcome.py`、`src/fireclaw_core/infra/operator_projection.py`。
- 重点回归测试：`tests/test_llm_deliberation_policy.py`、`tests/test_operator_console.py`、`tests/test_web_console_browser_e2e.py`。
- 暂存区最终包含 160 个预期文件；空的误生成文件 `--config` 未纳入提交，已可恢复地移动到 `/tmp/fireclaw-untracked-config`。

## 修复和验证

- 将 Mission planner 默认结构化单轮输出预留从 4096 调整为 3072 tokens，使 8K 上下文在已读取环境 belief 并暴露 observation Tool schema 时仍有足够的安全关键输入空间。
- 更新 operator console 测试断言以匹配现行确定性的 `zh-CN` 终态消息 `任务已完成。`。
- 沙箱内首次完整测试因 loopback socket 权限限制出现 186 个 `PermissionError`；授权环境复跑后只剩 5 个真实问题，修复后聚焦测试 `19 passed`。
- 最终授权环境完整回归：`2511 passed, 8 skipped in 293.38s`。
- `python -m compileall -q src tests extensions`：通过。
- `git diff --check` 与暂存区格式检查：通过。
- 浏览器端到端测试单独复跑：`3 passed`。

## Git 操作结果

- 创建提交：`20cd0d7 feat: finalize embodied runtime and operator workflows`。
- 将 `master` 从 `41bcb30` fast-forward 到 `20cd0d7`。
- 推送结果：`d6ba36b..20cd0d7 master -> master`。
- 远程校验：`origin/master` 当前为 `20cd0d7027c5186c44a67edad4534888ddc786b2`，与本地 `HEAD` 一致。
- 推送前工作区干净；本记录追加后需要再创建一个记录提交并推送。

## 当前结论

- FireClaw 当前已合并并发布到远程 `master`，代码和测试回归通过，没有执行真实机器人物理动作。
- `master` 的后续状态需在本记录提交并推送完成后再次核对为 clean 且与 `origin/master` 一致。

## 下一步

- 无需继续 Git 操作；如需现场验收，下一步应按仿真/真机隔离配置分别重启 Gateway 并执行 Gazebo 或受控硬件验收。
