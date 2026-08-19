# 2026-08-14: FireClaw 顶层 CLI 帮助分层收敛 (Progressive Disclosure)

## 目标
实现顶层 CLI 帮助的分层展示，突出核心 5 个日常操作命令（`setup`, `start`, `open`, `status`, `stop`），折叠进阶与开发者工具，并支持 `--all` 显示全量命令说明。

## 实施详情
1. **测试编写**：
   - 编写 `tests/test_cli_help_grouping.py`（TDD RED/GREEN 验证）。
   - 覆盖默认 `fireclaw --help`、全量 `fireclaw --help --all`、子命令专属帮助 `fireclaw <cmd> --help`、`fireclaw help [subcommand]`、以及子进程调用测试。

2. **模块重构**：
   - 在 `src/fireclaw_core/mission/mission_cli.py` 中抽取 `build_parser(show_all=False, prog="fireclaw")`。
   - 实现 `FireClawArgumentParser`（自定义 `format_help`，支持区分顶层 parser 与子命令 parser）以及自定义 `_FireClawHelpAction` 和 `_FireClawAllAction`。
   - 在 `src/fireclaw_core/__main__.py` 中补充 `KNOWN_SUBCOMMANDS` 包含 `help` 等子命令。

3. **测试验证**：
   - `tests/test_cli_help_grouping.py`: 10/10 通过。
   - `tests/test_user_lifecycle_cli.py`: 13/13 通过。
   - `tests/test_daemon_manager.py`: 9/9 通过。
