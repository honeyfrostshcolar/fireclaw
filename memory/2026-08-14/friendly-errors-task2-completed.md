# Friendly Errors Task 2: CLI 4 段式中文输出与 Gateway 结构化错误响应完成记录

- **时间**: 2026-08-14T18:59:00+08:00
- **目标**: 将 FriendlyError 解析系统集成到 CLI (`mission_cli.py`) 和 Gateway (`mission_gateway.py`)，完成 4 段式错误呈现与结构化响应。
- **已修改文件**:
  - `src/fireclaw_core/mission/mission_cli.py`: 添加 `print_friendly_error`，替换 profile/memory/approval/robot-profile 等错误输出分支为 4 段带框中文输出。
  - `src/fireclaw_core/mission/mission_gateway.py`: 添加 `_write_friendly_error` 方法，并在 `/config/*`、`/plan-mission`、`/recover` 等关键端点输出结构化 JSON 错误 `{"status": "error", "message": "...", "error": {...}}`，同时保留向后兼容的顶层 `message`。
  - `tests/test_friendly_cli_and_api.py`: 新建 14 个测试用例，覆盖 CLI 4 段输出、verbose 详情、动态 stderr 捕获、Gateway 结构化响应、向后兼容性与状态码校验。
- **验证命令**:
  - `PYTHONPATH=src python3 -m unittest -v tests/test_friendly_cli_and_api.py tests/test_friendly_errors.py tests/test_config_cli_and_api.py tests/test_web_console_app.py`
  - 结果: 57 个测试全部通过 (OK)。
- **下一步**: Task 3 (Web Console 前端 Toast 与 4 段模态框增强)。
