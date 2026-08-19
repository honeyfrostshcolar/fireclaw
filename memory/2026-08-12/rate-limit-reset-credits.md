# Codex rate-limit reset credits 查询

- 时间：2026-08-12 15:30:53 +08:00
- 任务目标：使用本机 Codex `tokens.access_token` 查询 ChatGPT `rate-limit-reset-credits`，仅报告重置卡发放与过期时间（北京时间）。
- 安全约束：未打印或记录 access token、refresh token、cookie、完整唯一 ID 或原始响应。
- 已执行：从 `/home/lpp/.codex/auth.json` 在内存读取 access token；以 `Authorization: Bearer ...` 请求 `https://chatgpt.com/backend-api/wham/rate-limit-reset-credits`。
- 结果：HTTP 200。响应结构包含 `credits`，该数组长度为 0；当前没有可展示的重置卡时间。
- 失败尝试：本机缺少 `jq`；Python 缺少 `zoneinfo`，后改用固定 UTC+8；一次临时脚本补丁产生语法错误，修复后成功请求。
- 文件修改：仅新增本记录；查询脚本位于 `/tmp`，任务结束时删除。
- 当前结论：凭证和 Authorization 请求有效，当前账户无重置卡记录。
- 下一步：如需再次查询，可重复同一安全流程；若 HTTP 401，应报告凭证失效或 Authorization header 未正确携带。
