from __future__ import annotations

import sys
import time
from typing import Any

from fireclaw_core.gateway.auth import resolve_gateway_api_token
from fireclaw_core.gateway.transport import GatewayTlsClientConfig
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient

TERMINAL_MISSION_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "completed",
    "escalated",
    "blocked",
    "timed_out",
    "lost",
}

_MISSION_TERMINAL_EVENTS = {
    "mission.completed": "succeeded",
    "mission.failed": "failed",
    "mission.cancelled": "cancelled",
    "mission.escalated": "escalated",
    "mission.blocked": "blocked",
    "mission.timed_out": "timed_out",
    "mission.lost": "lost",
}

_TERMINAL_STATUS_LABELS = {
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "escalated": "等待人工介入",
    "blocked": "已阻塞",
    "timed_out": "已超时",
    "lost": "状态失联",
}


def parse_builtin_command(input_text: str) -> tuple[str, str | None]:
    text = input_text.strip()
    if not text:
        return "help", None
    if text in {"help", "quit", "exit", "status"}:
        return text, None
    if text.startswith("cancel "):
        mission_id = text[len("cancel "):].strip()
        return "cancel", mission_id
    return "submit", text


def display_event(event: dict[str, Any]) -> None:
    event_type = str(event.get("event_type") or event.get("type") or "unknown")
    robot_id = str(event.get("robot_id") or "")
    task_id = str(event.get("task_id") or "")
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    subject = " / ".join(item for item in (robot_id, task_id) if item)
    prefix = f"[{subject}] " if subject else ""

    labels = {
        "mission.run_accepted": "任务已进入队列",
        "mission.planning": "正在规划任务",
        "mission.running": "任务开始执行",
        "mission.cancel_requested": "已请求取消，等待机器人确认终态",
        "task.received": "机器人已接收子任务",
        "task.planned": "机器人已完成子任务规划",
        "task.cancel_requested": "机器人取消请求已发送，尚未证明停止",
        "task.completed": "机器人子任务已完成",
        "task.blocked": "机器人子任务被安全策略阻塞",
        "task.escalated": "机器人子任务需要人工介入",
        "task.failed": "机器人子任务失败",
        "task.timed_out": "机器人子任务超时",
        "task.cancelled": "机器人已确认子任务取消",
        "task.lost": "机器人子任务失联；不能据此推断已经停止",
        "mission.completed": "任务已完成",
        "mission.failed": "任务失败",
        "mission.timed_out": "任务超时",
        "mission.cancelled": "任务已取消",
        "mission.blocked": "任务被阻塞",
        "mission.escalated": "任务等待人工介入",
        "mission.lost": "任务状态失联",
    }
    if event_type in labels:
        reason = payload.get("reason_code") or payload.get("message")
        suffix = f"：{reason}" if isinstance(reason, str) and reason.strip() else ""
        print(f"[事件] {prefix}{labels[event_type]}{suffix}")
        return
    if event_type == "action.feedback":
        message = payload.get("message")
        progress = payload.get("progress")
        details = []
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            details.append(f"进度 {progress:g}")
        if isinstance(message, str) and message.strip():
            details.append(message.strip())
        print(f"[进度] {prefix}{'；'.join(details) or '机器人正在执行'}")
        return
    status = str(payload.get("status") or event.get("status") or event_type)
    print(f"[事件] {prefix}{status}")


def run_interactive(
    server_url: str = "http://127.0.0.1:8766",
    timeout: float = 30.0,
    api_token: str | None = None,
    tls: GatewayTlsClientConfig | None = None,
) -> None:
    client = MissionGatewayClient(
        server_url,
        timeout=timeout,
        api_token=resolve_gateway_api_token(api_token),
        tls=tls,
    )
    print("FireClaw 任务控制台")
    print(f"Mission Gateway：{server_url}")
    print("输入 help 查看命令；输入 quit 退出。")
    while True:
        try:
            raw = input("fireclaw> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        action, arg = parse_builtin_command(raw)
        if action in {"quit", "exit"}:
            return
        if action == "help":
            print("命令：help、status、cancel <mission_id>、quit，或直接输入自然语言任务。")
            continue
        if action == "status":
            _display_fleet_state(client.get_fleet_state())
            continue
        if action == "cancel" and arg:
            result = client.cancel_mission(arg)
            status = str(result.get("status") or "unknown")
            if status == "cancel_requested":
                print("取消请求已发送；在机器人确认终态前，不代表已经停止。")
            elif status in {"cancelled", "already_terminal"}:
                print(f"取消结果：{status}")
            else:
                print(f"取消未完成：{result.get('message') or status}")
            continue
        if action == "submit" and arg:
            try:
                _submit_and_follow(client, arg)
            except KeyboardInterrupt:
                print("\n已停止本地观察；远端任务没有因此自动取消。")


def _submit_and_follow(
    client: MissionGatewayClient,
    command: str,
    *,
    sleep_fn=time.sleep,
    reconnect_initial_seconds: float = 0.25,
    reconnect_max_seconds: float = 4.0,
    max_reconnect_attempts: int | None = None,
) -> None:
    result = client.submit_mission(command)
    mission_id = result.get("mission_id")
    status = str(result.get("status") or "unknown")
    if not isinstance(mission_id, str) or not mission_id:
        print(f"任务未提交：{result.get('message') or status}")
        return
    print(f"任务已提交：{mission_id}（{status}）")

    last_sequence = 0
    seen_event_ids: set[str] = set()
    reconnect_attempts = 0
    reconnect_delay = max(0.01, reconnect_initial_seconds)
    disconnected = False
    while True:
        try:
            for event in client.stream_mission_events(
                mission_id,
                after_sequence=last_sequence,
            ):
                if not isinstance(event, dict):
                    continue
                event_id = event.get("event_id")
                if isinstance(event_id, str) and event_id in seen_event_ids:
                    continue
                sequence = event.get("sequence")
                if isinstance(sequence, int) and sequence > last_sequence:
                    last_sequence = sequence
                if isinstance(event_id, str):
                    seen_event_ids.add(event_id)
                if disconnected:
                    print(f"[连接] 事件流已恢复，从 cursor={last_sequence} 继续。")
                    disconnected = False
                reconnect_attempts = 0
                reconnect_delay = max(0.01, reconnect_initial_seconds)
                display_event(event)
                event_type = str(event.get("event_type") or event.get("type") or "")
                terminal = _MISSION_TERMINAL_EVENTS.get(event_type)
                if terminal is not None:
                    print(f"[完成] {_TERMINAL_STATUS_LABELS[terminal]}（{terminal}）")
                    return

            trace = client.get_mission_trace(mission_id)
            terminal = _terminal_trace_status(trace)
            if terminal is not None:
                print(f"[完成] {_TERMINAL_STATUS_LABELS[terminal]}（{terminal}）")
                return
        except KeyboardInterrupt:
            raise
        except Exception:
            pass

        reconnect_attempts += 1
        if max_reconnect_attempts is not None and reconnect_attempts > max_reconnect_attempts:
            print("[连接] 已达到本地重连上限；远端任务状态未被修改。", file=sys.stderr)
            return
        if not disconnected:
            print(
                f"[连接] 事件流中断；{reconnect_delay:g} 秒后从 "
                f"cursor={last_sequence} 自动重连。",
                file=sys.stderr,
            )
            disconnected = True
        sleep_fn(reconnect_delay)
        reconnect_delay = min(
            reconnect_max_seconds,
            reconnect_delay * 2,
        )


def _submit_and_poll(client: MissionGatewayClient, command: str) -> None:
    """Compatibility alias for the former polling implementation."""

    _submit_and_follow(client, command)


def _terminal_trace_status(trace: dict[str, Any]) -> str | None:
    run = trace.get("run")
    candidates = [
        trace.get("run_status"),
        run.get("run_status") if isinstance(run, dict) else None,
        run.get("status") if isinstance(run, dict) else None,
        trace.get("status"),
        trace.get("mission_status"),
    ]
    aliases = {"completed": "succeeded"}
    for value in candidates:
        if not isinstance(value, str):
            continue
        normalized = aliases.get(value, value)
        if normalized in TERMINAL_MISSION_STATUSES:
            return normalized
    return None


def _display_fleet_state(state: dict[str, Any]) -> None:
    entries = state.get("entries")
    if not isinstance(entries, list) or not entries:
        print("Fleet：没有已登记的机器人。")
        return
    print(f"Fleet：{len(entries)} 台机器人")
    for item in entries:
        if not isinstance(item, dict):
            continue
        robot = item.get("robot")
        robot = robot if isinstance(robot, dict) else item
        robot_id = str(robot.get("robot_id") or "unknown")
        online = item.get("is_online")
        stale = item.get("is_stale")
        status = "online" if online is True else "offline"
        if stale is True:
            status += "/stale"
        capabilities = robot.get("capabilities")
        capability_text = (
            ", ".join(str(value) for value in capabilities)
            if isinstance(capabilities, list) and capabilities
            else "无已声明能力"
        )
        print(f"  - {robot_id}: {status}; {capability_text}")
