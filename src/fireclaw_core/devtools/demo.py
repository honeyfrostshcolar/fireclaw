from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any

from fireclaw_core.gateway.control import operator_from_payload
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile


def run_rescue_demo(
    *,
    command: str = "去坐标 (2.0, 1.5) 救人",
    memory_path: str = "memory/fireclaw-demo.jsonl",
    event_path: str = "memory/fireclaw-demo-events.jsonl",
    task_queue_path: str = "memory/fireclaw-demo-tasks.jsonl",
    robot_id: str = "fireclaw-demo-ros1",
    session_id: str = "fireclaw-demo",
    operator_id: str = "local-operator",
    operator_role: str = "operator",
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    gateway = FireClawGateway(
        GatewayConfig(
            adapter="mock-ros1",
            robot_id=robot_id,
            memory_path=memory_path,
            event_path=event_path,
            task_queue_path=task_queue_path,
            default_session_id=session_id,
            deployment_profile=DeploymentProfile(
                mode="simulation",
                role="robot_agent",
                sandbox=SandboxProfile(),
            ),
        )
    )
    operator = operator_from_payload(
        {
            "operator_id": operator_id,
            "role": operator_role,
            "source": "demo",
        }
    )
    submitted = gateway.submit_agent(command, session_id=session_id, operator=operator)
    if submitted["status"] != "accepted":
        return {
            "status": submitted["status"],
            "task_id": submitted.get("task_id"),
            "session_id": submitted.get("session_id", session_id),
            "operator": operator.to_dict(),
            "control": submitted.get("control"),
            "submitted": submitted,
        }

    task_id = submitted["task_id"]
    trace = _wait_for_terminal_trace(gateway, task_id, timeout_seconds)
    events = trace["events"]
    operator_event = _latest_payload(events, "operator.identified") or operator.to_dict()
    control_event = _latest_payload(events, "control.decision") or {}
    result = trace.get("result") or {}
    status = result.get("status")

    return {
        "status": status if isinstance(status, str) else trace["status"],
        "task_id": task_id,
        "session_id": submitted["session_id"],
        "operator": operator_event,
        "control": control_event,
        "state": _compact_state(trace["state"]),
        "result": _compact_result(result),
        "event_types": [event.get("type") for event in events],
        "action_events": [
            _compact_action_event(event) for event in events if str(event.get("type", "")).startswith("action.")
        ],
        "robot_state": asdict(gateway.robot.get_robot_state()),
    }


def _wait_for_terminal_trace(gateway: FireClawGateway, task_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        trace = gateway.task_trace(task_id)
        if trace.get("result") is not None:
            return trace
        time.sleep(0.01)
    raise TimeoutError(f"Demo task {task_id} did not finish before timeout.")


def _latest_payload(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("type") == event_type:
            payload = event.get("payload")
            if isinstance(payload, dict):
                return payload
    return None


def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "task": dict(state.get("task") or {}),
        "skills": list(state.get("skills") or []),
        "actions": list(state.get("actions") or []),
    }
    task_result = compact["task"].get("result")
    if isinstance(task_result, dict):
        compact["task"]["result"] = {
            "status": task_result.get("status"),
            "message": task_result.get("message"),
        }
    return compact


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    execution = result.get("execution") if isinstance(result.get("execution"), dict) else {}
    steps = []
    for step in execution.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        steps.append(
            {
                "skill_name": step.get("skill_name"),
                "status": step.get("status"),
                "inputs": step.get("inputs"),
                "output": step.get("output"),
            }
        )
    return {
        "status": result.get("status"),
        "message": result.get("message"),
        "planning": result.get("planning"),
        "safety": result.get("safety"),
        "execution": {
            "status": execution.get("status"),
            "step_count": len(steps),
            "steps": steps,
        },
    }


def _compact_action_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    output = payload.get("output")
    if not isinstance(output, dict):
        output = {}
    return {
        "type": event.get("type"),
        "timestamp": event.get("timestamp"),
        "payload": {
            "action_id": payload.get("action_id"),
            "action_type": payload.get("action_type"),
            "skill_name": payload.get("skill_name"),
            "status": payload.get("status"),
            "progress": payload.get("progress"),
            "message": payload.get("message"),
            "ros1_interface": output.get("ros1_interface"),
            "ros1_name": output.get("ros1_name"),
            "error": payload.get("error"),
        },
    }
