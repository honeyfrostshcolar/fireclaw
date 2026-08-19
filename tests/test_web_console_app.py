from __future__ import annotations

import json
import unittest
from pathlib import Path
from urllib import request
from urllib.error import HTTPError

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.gateway.network_security import GatewayNetworkPolicy
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig


# ---------------------------------------------------------------------------
# Test doubles & helpers
# ---------------------------------------------------------------------------


class FakeSubagentClient:
    def __init__(self):
        self.calls: list[tuple] = []
        self.cancel_calls: list[tuple] = []
        self.presence_results: dict[str, dict] = {}

    def submit_task(self, entry, **kwargs):
        self.calls.append((entry, kwargs))
        return {
            "status": "accepted",
            "task_id": kwargs.get("task_id", "task-1"),
            "robot_id": entry.robot_id,
        }

    def cancel_task(self, entry, task_id, *, operator=None):
        self.cancel_calls.append((entry, task_id, operator))
        return {
            "status": "cancel_requested",
            "task_id": task_id,
            "robot_id": entry.robot_id,
        }

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-08-14T00:00:00+00:00",
            "state": {},
        }


def _make_registry() -> RobotRegistry:
    return RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="turtlebot3_burger",
                base_url="http://robot-1.local:8765",
                capabilities=("victim_search", "hazard_survey", "move_base"),
            ),
        ]
    )


from fireclaw_core.mission.mission_planner import (
    MissionPlan,
    MissionPlannerContext,
    MissionPlanningResult,
    MissionSubtask,
)


class FakePlanner:
    def plan(self, command: str, *, context: MissionPlannerContext) -> MissionPlanningResult:
        available_ids = [e.robot_id for e in context.available_robots]
        if not available_ids:
            return MissionPlanningResult(
                status="no_robots",
                message="No online robots available.",
                intent=None,
                plan=None,
            )
        return MissionPlanningResult(
            status="planned",
            message="Plan created.",
            intent="search",
            plan=MissionPlan(
                intent="search",
                command=command,
                subtasks=[
                    MissionSubtask(
                        robot_id=available_ids[0],
                        command=command,
                        floor=2,
                        capability_required="victim_search",
                        execution_group=0,
                    )
                ],
            ),
        )


def _make_agent(registry: RobotRegistry | None = None) -> MissionAgent:
    registry = registry or _make_registry()
    client = FakeSubagentClient()
    planner = FakePlanner()
    return MissionAgent(
        registry=registry,
        subagent_client=client,
        planner=planner,
    )


def _make_gateway(
    mission_agent: MissionAgent | None = None,
    registry: RobotRegistry | None = None,
    api_token: str | None = None,
) -> MissionGateway:
    reg = registry or _make_registry()
    agent = mission_agent or _make_agent(reg)
    config = MissionGatewayConfig(
        port=0,
        api_token=api_token,
        network=GatewayNetworkPolicy(),
    )
    return MissionGateway(
        config,
        mission_agent=agent,
        registry=reg,
    )


def _raw_request(
    base_url: str,
    method: str,
    path: str,
    data: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req_headers: dict[str, str] = {}
    if headers:
        req_headers.update(headers)
    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["content-type"] = "application/json; charset=utf-8"
    req = request.Request(
        f"{base_url}{path}",
        data=body_bytes,
        method=method,
        headers=req_headers,
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
            return response.status, resp_headers, response.read()
    except HTTPError as exc:
        resp_headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        return exc.code, resp_headers, exc.read()


def _json_request(
    base_url: str,
    method: str,
    path: str,
    data: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    status, _, body = _raw_request(base_url, method, path, data=data, headers=headers)
    return status, json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Tests: Gateway Intent Parsing, Tasks & Recovery API Contracts
# ---------------------------------------------------------------------------


class TestWebConsoleGatewayContracts(unittest.TestCase):
    def setUp(self):
        self.gw = _make_gateway()
        self.gw.start()

    def tearDown(self):
        self.gw.stop()

    def test_plan_mission_structured_output(self):
        """POST /plan-mission decomposes natural language task into structured preview."""
        payload = {
            "task_description": "前往二楼搜索被困人员并汇报位置",
            "profile": "profiles/turtlebot3_burger.json",
        }
        status, data = _json_request(self.gw.base_url, "POST", "/plan-mission", data=payload)
        self.assertEqual(status, 200)
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("intent", data)
        self.assertIn("target_robot", data)
        self.assertIn("steps", data)
        self.assertIn("risk_level", data)
        self.assertIsInstance(data.get("steps"), list)
        self.assertGreater(len(data.get("steps")), 0)
        self.assertIn("raw_plan", data)

    def test_plan_mission_empty_task_rejected(self):
        """POST /plan-mission with empty description returns 400."""
        payload = {"task_description": "  "}
        status, data = _json_request(self.gw.base_url, "POST", "/plan-mission", data=payload)
        self.assertEqual(status, 400)
        self.assertEqual(data.get("status"), "error")

    def test_tasks_alias_submission(self):
        """POST /tasks creates a mission/task and returns accepted status."""
        payload = {
            "command": "前往二楼搜索人员",
            "target_robot": "turtlebot3_burger",
        }
        status, data = _json_request(self.gw.base_url, "POST", "/tasks", data=payload)
        self.assertEqual(status, 202)
        self.assertIn("task_id", data)
        self.assertEqual(data.get("status"), "accepted")

    def test_tasks_cancel_alias(self):
        """POST /tasks/<id>/cancel requests cancellation of the task."""
        # First submit a task
        submit_payload = {"command": "前往二楼搜索人员"}
        _, submit_data = _json_request(self.gw.base_url, "POST", "/tasks", data=submit_payload)
        task_id = submit_data.get("task_id")
        self.assertTrue(task_id)

        # Cancel the task
        status, cancel_data = _json_request(
            self.gw.base_url, "POST", f"/tasks/{task_id}/cancel", data={}
        )
        self.assertEqual(status, 200)
        self.assertEqual(cancel_data.get("status"), "cancel_requested")
        self.assertEqual(cancel_data.get("task_id"), task_id)

    def test_admission_projection_reset_requires_operator_confirmed(self):
        """POST /recover without operator_confirmed=True is rejected with 400."""
        payload = {"operator_confirmed": False, "reason": "未人工确认"}
        status, data = _json_request(self.gw.base_url, "POST", "/recover", data=payload)
        self.assertEqual(status, 400)
        self.assertEqual(data.get("status"), "error")
        self.assertIn("confirmation", data.get("message", "").lower())

    def test_admission_projection_reset_does_not_claim_physical_recovery(self):
        """POST /recover resets only the local projection and returns UNKNOWN physical state."""
        payload = {
            "operator_confirmed": True,
            "reason": "现场障碍已人工排除，确认周围物理环境安全",
        }
        status, data = _json_request(self.gw.base_url, "POST", "/recover", data=payload)
        self.assertEqual(status, 200)
        self.assertEqual(data.get("status"), "ok")
        self.assertTrue(data.get("admission_projection_reset"))
        self.assertFalse(data.get("physical_stop_confirmed"))
        self.assertEqual(data.get("robot_physical_status"), "unknown")
        self.assertNotIn("recovered", data)
        self.assertIn("readiness", data)
        self.assertEqual(data["readiness"].get("status"), "ok")


# ---------------------------------------------------------------------------
# Tests: Frontend app.js Code Structure & Contract Checks
# ---------------------------------------------------------------------------


class TestWebConsoleAppJSContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_js_path = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "fireclaw_core"
            / "web_console"
            / "app.js"
        )
        cls.js_content = app_js_path.read_text(encoding="utf-8")

    def test_app_js_state_model_presence(self):
        """app.js must maintain state model for activeTab, robot, taskDraft, activeExecution, and recovery."""
        required_state_fields = [
            "activeTab",
            "robot",
            "taskDraft",
            "activeExecution",
            "recovery",
        ]
        for field in required_state_fields:
            self.assertIn(field, self.js_content, f"State field '{field}' not found in app.js")

    def test_app_js_three_state_cancel_logic(self):
        """app.js must implement 3-state cancellation machine: cancel_requested -> stopping -> stopped_confirmed."""
        self.assertIn("cancel_requested", self.js_content)
        self.assertIn("stopping", self.js_content)
        self.assertIn("stopped_confirmed", self.js_content)
        self.assertIn("updateCancelState", self.js_content)

    def test_app_js_requires_explicit_physical_stop_evidence(self):
        """Task cancellation alone must never be rendered as a confirmed physical stop."""
        self.assertIn("physical_stop_confirmed", self.js_content)
        self.assertIn("robot.stopped_confirmed", self.js_content)
        self.assertNotIn(
            "type === 'task.cancelled' || type === 'task.stopped' || type === 'mission.cancelled'",
            self.js_content,
        )

    def test_app_js_intent_parsing_and_confirmation(self):
        """app.js must handle parsing task intent, rendering preview, and confirmation submission."""
        self.assertIn("parseTaskIntent", self.js_content)
        self.assertIn("confirmAndStartTask", self.js_content)
        self.assertIn("cancelTaskPreview", self.js_content)
        self.assertIn("/plan-mission", self.js_content)
        self.assertNotIn("parseTaskIntent(true)", self.js_content)
        self.assertNotIn("if (autoConfirm)", self.js_content)

    def test_app_js_does_not_invent_runtime_or_physical_state(self):
        """Unknown Gateway fields and task acceptance must remain non-authoritative."""
        forbidden_claims = [
            "mode: 'simulation'",
            "activeRobotId: 'turtlebot3_burger'",
            "profilePath: 'profiles/turtlebot3_burger.json'",
            "底盘防跌落传感器正常",
            "雷达防碰撞距离安全（> 0.35m）",
            "急停开关处于释放状态",
            "任务已成功下发并开始执行",
            "move_base 巡航启动中",
            "准入已恢复放行",
        ]
        for claim in forbidden_claims:
            self.assertNotIn(claim, self.js_content)
        self.assertIn("机器人物理状态仍为 UNKNOWN", self.js_content)
        self.assertIn("尚未收到机器人开始运动或 Tool 执行证据", self.js_content)

    def test_app_js_sse_subscription_and_reconnect(self):
        """app.js must handle EventSource SSE stream with cursor tracking."""
        self.assertIn("subscribeEvents", self.js_content)
        self.assertIn("EventSource", self.js_content)
        self.assertIn("lastEventId", self.js_content)

    def test_app_js_recovery_center_logic(self):
        """app.js must label its operator-confirmed recovery request as non-authoritative."""
        self.assertIn("executeRecovery", self.js_content)
        self.assertIn("operator_confirmed", self.js_content)
        self.assertIn("/recover", self.js_content)
        self.assertIn("准入投影重置请求", self.js_content)

    def test_app_js_four_part_error_modal(self):
        """app.js must define showErrorModal with 4 parts."""
        self.assertIn("showErrorModal", self.js_content)
        self.assertIn("modal-what-happened", self.js_content)
        self.assertIn("modal-robot-safe-status", self.js_content)
        self.assertIn("modal-action-taken", self.js_content)
        self.assertIn("modal-next-steps", self.js_content)


if __name__ == "__main__":
    unittest.main()
