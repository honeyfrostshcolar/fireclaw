from __future__ import annotations

import json
import unittest
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
        self.presence_results: dict[str, dict] = {}

    def check_presence(self, entry):
        if entry.robot_id in self.presence_results:
            return self.presence_results[entry.robot_id]
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-06-08T00:00:00+00:00",
            "state": {},
        }


def _make_registry() -> RobotRegistry:
    return RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://robot-1.local:8765",
                capabilities=("victim_search", "emergency_stop"),
            ),
        ]
    )


def _make_agent(registry: RobotRegistry | None = None) -> MissionAgent:
    registry = registry or _make_registry()
    client = FakeSubagentClient()
    return MissionAgent(
        registry=registry,
        subagent_client=client,
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
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req_headers: dict[str, str] = {}
    if headers:
        req_headers.update(headers)
    req = request.Request(
        f"{base_url}{path}",
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
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    status, _, body = _raw_request(base_url, method, path, headers=headers)
    return status, json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Tests: Static routes & Console HTML
# ---------------------------------------------------------------------------


class TestWebConsoleRoutes(unittest.TestCase):
    def setUp(self):
        self.gw = _make_gateway()
        self.gw.start()

    def tearDown(self):
        self.gw.stop()

    def test_get_root_serves_html_console(self):
        status, headers, body = _raw_request(self.gw.base_url, "GET", "/")
        self.assertEqual(status, 200)
        content_type = headers.get("content-type", "")
        self.assertIn("text/html", content_type)
        self.assertIn("charset=utf-8", content_type.lower())
        html_str = body.decode("utf-8")
        self.assertIn("<title>FireClaw Web Console</title>", html_str)
        self.assertIn("/static/style.css", html_str)
        self.assertIn("/static/app.js", html_str)

    def test_get_console_serves_html_console(self):
        status, headers, body = _raw_request(self.gw.base_url, "GET", "/console")
        self.assertEqual(status, 200)
        content_type = headers.get("content-type", "")
        self.assertIn("text/html", content_type)
        self.assertIn("charset=utf-8", content_type.lower())
        html_str = body.decode("utf-8")
        self.assertIn("<title>FireClaw Web Console</title>", html_str)

    def test_get_static_style_css(self):
        status, headers, body = _raw_request(self.gw.base_url, "GET", "/static/style.css")
        self.assertEqual(status, 200)
        content_type = headers.get("content-type", "")
        self.assertIn("text/css", content_type)
        self.assertIn("charset=utf-8", content_type.lower())
        self.assertGreater(len(body), 0)

    def test_get_static_app_js(self):
        status, headers, body = _raw_request(self.gw.base_url, "GET", "/static/app.js")
        self.assertEqual(status, 200)
        content_type = headers.get("content-type", "")
        self.assertIn("application/javascript", content_type)
        self.assertIn("charset=utf-8", content_type.lower())
        self.assertGreater(len(body), 0)

    def test_static_path_traversal_protection(self):
        # Standard traversal attack
        status, _, _ = _raw_request(self.gw.base_url, "GET", "/static/../../etc/passwd")
        self.assertIn(status, (403, 404))

        # URL encoded traversal attack
        status, _, _ = _raw_request(self.gw.base_url, "GET", "/static/%2e%2e/%2e%2e/etc/passwd")
        self.assertIn(status, (403, 404))

        # Non-existent static file
        status, _, _ = _raw_request(self.gw.base_url, "GET", "/static/nonexistent_file_123.js")
        self.assertEqual(status, 404)

    def test_get_readiness_endpoint(self):
        status, payload = _json_request(self.gw.base_url, "GET", "/readiness")
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("status"), "ok")
        self.assertEqual(payload.get("schema_version"), 1)
        self.assertEqual(payload.get("active_robot_id"), "robot-1")
        self.assertIn("phase", payload)
        self.assertIn("safe_state", payload)
        self.assertIn("fleet_state", payload)
        self.assertIn("fleet_doctor", payload)


if __name__ == "__main__":
    unittest.main()
