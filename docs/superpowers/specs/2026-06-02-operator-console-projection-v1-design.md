# Operator Console Projection v1 Design

## Goal

Add a human-facing operator layer inspired by OpenClaw's TUI/channel projection model. FireClaw should keep structured Gateway/EventLedger data for machines, but operators should see Chinese progress text instead of raw JSON.

## OpenClaw Reference

CodeGraph confirmed OpenClaw's split:

- `src/tui/gateway-chat.ts:189` sends `chat.send` and returns a `runId`.
- `src/gateway/server-methods/chat.ts:2291` handles `chat.send` as a structured Gateway method.
- `src/gateway/chat-abort.ts:74` registers in-flight runs by `runId`.
- `src/gateway/server-methods/chat.ts:2029` broadcasts final chat events.
- `src/infra/agent-events.ts:209` emits structured agent events.
- `src/tui/tui-event-handlers.ts:57` projects chat/tool/lifecycle events into UI state.
- `src/tui/components/chat-log.ts:266` updates assistant text.
- `src/tui/components/chat-log.ts:324` turns tool events into a tool execution component.

The useful pattern is: structured protocol internally, human projection externally.

## FireClaw Approach

Add two small Python modules:

- `operator_projection.py`: maps EventLedger event dictionaries to Chinese operator messages.
- `operator_console.py`: runs a local Gateway task in a background thread, polls EventLedger, and prints each new projected message.

This keeps FireClaw's current Gateway API unchanged and avoids premature curses, WebSocket, or SSE work. It still demonstrates the OpenClaw-style boundary:

```text
Gateway JSON/EventLedger -> OperatorEventProjector -> Chinese terminal progress
```

## Event Projection Contract

`OperatorEventProjector.project(event)` returns either a Chinese string or `None`.

First mappings:

- `task.received`: `已接收任务：<command>。`
- `task.planned`: `正在规划救援任务。`
- `safety.decided allow`: `安全检查通过。`
- `safety.decided clarify`: `需要补充信息：<reason>`
- `safety.decided block`: `安全检查未通过：<reason>`
- `safety.decided require_confirmation`: `该任务需要人工确认：<reason>`
- `confirmation.pending`: `等待人工确认后继续执行。`
- `confirmation.confirmed`: `人工确认已收到，继续执行。`
- `skill.started`: domain-specific Chinese message for known rescue skills.
- `skill.attempted failed`: `技能 <name> 第 <n> 次尝试失败：<error>`
- `skill.failed`: `技能 <name> 执行失败：<error>`
- `task.completed`: `任务完成：<message>`
- `task.cancelled`: `任务已取消。`

Known rescue skill names are translated to operator language:

- `navigate_to_floor`: `正在前往<floor>楼。`
- `search_for_victims`: `正在搜索<floor>楼被困人员。`
- `assess_victim`: `正在评估被困人员状态。`
- `report_status`: `正在向操作员报告现场状态。`
- `return_to_safe_zone`: `正在返回安全区域。`

## Operator Console Contract

`run_operator_command(...)` accepts a command and a Gateway config, calls `FireClawGateway.submit_agent(...)`, polls events by the returned `task_id`, prints projected messages, and returns the final result once the task trace contains a final result.

Earlier FireClaw versions had to wrap synchronous `run_agent(...)` in a local worker thread. Gateway Async Task Runner v1 removed that bridge by making `task_id` available immediately.

## Non-Goals

- No full-screen terminal UI.
- No WebSocket/SSE.
- No remote Gateway client.
- No voice output.
- No true async HTTP task queue.
- No live cancellation.

## Research Impact

This makes FireClaw more realistic for robotics operation: operators see task progress in natural language while structured traces remain available for audit, evaluation, and incident analysis. This is an engineering enabler for operator trust and safety studies, not a standalone research contribution.
