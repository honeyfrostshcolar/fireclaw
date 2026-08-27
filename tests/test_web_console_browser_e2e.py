from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
from urllib import request
from urllib.parse import urlparse

import pytest

from fireclaw_core.agent.robot_registry import RobotRegistry, RobotRegistryEntry
from fireclaw_core.mission.mission_agent import MissionAgent
from fireclaw_core.mission.mission_gateway import MissionGateway, MissionGatewayConfig
from fireclaw_core.mission.runtime_identity import GatewayRuntimeIdentity


CHROME = (
    shutil.which("google-chrome")
    or shutil.which("chromium")
    or shutil.which("chromium-browser")
)


class BrowserPresenceClient:
    def check_presence(self, entry):
        return {
            "robot_id": entry.robot_id,
            "online": True,
            "last_seen_at": "2026-08-20T00:00:00+00:00",
            "state": {},
        }


class LegacyProjectionGateway(MissionGateway):
    def readiness(self):
        return {
            "status": "ok",
            "schema_version": 1,
            "active_robot_id": "forged-legacy-robot",
            "runtime_mode": "real",
            "active_profile_path": "/forged/legacy/profile.toml",
            "phase": "ready",
            "safe_state": "ready",
            "summary": "legacy projection must not be trusted",
            "fleet_state": {
                "entries": [
                    {
                        "robot_id": "forged-legacy-robot",
                        "capabilities": ["navigation"],
                        "is_online": True,
                    }
                ]
            },
        }


def _gateway(
    *,
    identity: GatewayRuntimeIdentity | None,
    registry: RobotRegistry | None = None,
    gateway_type=MissionGateway,
) -> MissionGateway:
    registry = registry or RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://127.0.0.1:8765",
                capabilities=("navigation", "emergency_stop"),
            )
        ]
    )
    client = BrowserPresenceClient()
    agent = MissionAgent(registry=registry, subagent_client=client)
    return gateway_type(
        MissionGatewayConfig(port=0),
        mission_agent=agent,
        registry=registry,
        subagent_client=client,
        runtime_identity=identity,
    )


def _identity() -> GatewayRuntimeIdentity:
    return GatewayRuntimeIdentity.create(
        runtime_mode="simulation",
        active_profile_path=Path(__file__),
        profile_sha256="a" * 64,
        robot_id="robot-1",
        deployment_fingerprint="b" * 64,
        observed_at="2026-08-20T00:00:00+00:00",
    )


class _DevToolsConnection:
    def __init__(self, websocket_url: str) -> None:
        parsed = urlparse(websocket_url)
        assert parsed.hostname is not None and parsed.port is not None
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request_bytes = (
            f"GET {parsed.path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.socket.sendall(request_bytes)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(self.socket.recv(4096))
        assert response.startswith(b"HTTP/1.1 101"), response.decode("utf-8", "replace")
        self._next_id = 1

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=8)
        except OSError:
            pass
        self.socket.close()

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self._next_id
        self._next_id += 1
        self._send_frame(
            json.dumps(
                {"id": request_id, "method": method, "params": params},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = json.loads(self._receive_text().decode("utf-8"))
            if message.get("id") == request_id:
                return message
        raise TimeoutError(f"Chrome DevTools response timed out: {method}")

    def _send_frame(self, payload: bytes, *, opcode: int = 1) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(bytes(header) + mask + masked)

    def _receive_text(self) -> bytes:
        fragments = bytearray()
        while True:
            first, second = self._read_exact(2)
            finished = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            if second & 0x80:
                mask = self._read_exact(4)
            else:
                mask = None
            payload = self._read_exact(length)
            if mask is not None:
                payload = bytes(
                    value ^ mask[index % 4] for index, value in enumerate(payload)
                )
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode == 8:
                raise ConnectionError("Chrome closed the DevTools WebSocket")
            if opcode in {0, 1}:
                fragments.extend(payload)
            if finished:
                return bytes(fragments)

    def _read_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunk = self.socket.recv(length - len(chunks))
            if not chunk:
                raise ConnectionError("Chrome closed the DevTools WebSocket")
            chunks.extend(chunk)
        return bytes(chunks)


def _browser_state(gateway: MissionGateway, tmp_path: Path) -> dict[str, object]:
    if CHROME is None:
        pytest.skip("Chrome/Chromium is unavailable")
    user_data = tmp_path / "chrome-profile"
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--no-first-run",
            f"--user-data-dir={user_data}",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            gateway.base_url,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    connection = None
    try:
        port_file = user_data / "DevToolsActivePort"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not port_file.is_file():
            if process.poll() is not None:
                raise RuntimeError("Chrome exited before DevTools became ready")
            time.sleep(0.05)
        assert port_file.is_file(), "Chrome DevToolsActivePort was not created"
        port = int(port_file.read_text(encoding="utf-8").splitlines()[0])
        opener = request.build_opener(request.ProxyHandler({}))
        target = None
        while time.monotonic() < deadline:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                targets = json.loads(response.read().decode("utf-8"))
            target = next(
                (
                    item
                    for item in targets
                    if item.get("type") == "page"
                    and str(item.get("url", "")).startswith(gateway.base_url)
                ),
                None,
            )
            if target is not None:
                break
            time.sleep(0.05)
        assert target is not None, "Gateway page target was not created"
        connection = _DevToolsConnection(str(target["webSocketDebuggerUrl"]))
        expression = """
          (() => {
            const text = (id) => {
              const element = document.getElementById(id);
              return element ? element.textContent.trim() : null;
            };
            return {
              loaded: Boolean(window.app && window.app.readiness),
              mode: text('system-mode-text'),
              robot: text('overview-active-robot'),
              chassis: text('overview-chassis-comm'),
              profilePath: text('profile-path-display'),
              profileRevision: text('profile-revision-display'),
              identityEvidence: text('identity-evidence-display'),
              safety: text('global-safety-text'),
            };
          })()
        """
        state = None
        while time.monotonic() < deadline:
            response = connection.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
            )
            state = (
                response.get("result", {})
                .get("result", {})
                .get("value")
            )
            if isinstance(state, dict) and state.get("loaded") is True:
                return state
            time.sleep(0.05)
        raise AssertionError(f"Web Console did not finish readiness rendering: {state}")
    finally:
        if connection is not None:
            connection.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def test_browser_renders_gateway_runtime_identity_and_robot_readiness(
    tmp_path: Path,
) -> None:
    gateway = _gateway(identity=_identity())
    gateway.start()
    try:
        state = _browser_state(gateway, tmp_path)
    finally:
        gateway.stop()

    assert state["mode"] == "SIMULATION"
    assert state["robot"] == "robot-1"
    assert state["chassis"] == "ONLINE"
    assert state["profilePath"] == str(Path(__file__).resolve())
    assert state["profileRevision"] == "sha256:" + ("a" * 64)
    assert str(state["identityEvidence"]).startswith("sha256:")


def test_browser_refuses_legacy_or_missing_evidence_even_when_values_claim_ready(
    tmp_path: Path,
) -> None:
    gateway = _gateway(
        identity=None,
        gateway_type=LegacyProjectionGateway,
    )
    gateway.start()
    try:
        state = _browser_state(gateway, tmp_path)
    finally:
        gateway.stop()

    assert state["mode"] == "UNKNOWN"
    assert state["robot"] == "UNKNOWN"
    assert state["chassis"] == "UNKNOWN"
    assert state["profilePath"] == "UNKNOWN"
    assert "forged-legacy-robot" not in str(state["robot"])


def test_browser_renders_stale_robot_evidence_as_stale_not_online(
    tmp_path: Path,
) -> None:
    registry = RobotRegistry(
        [
            RobotRegistryEntry(
                robot_id="robot-1",
                base_url="http://127.0.0.1:8765",
                capabilities=("navigation", "emergency_stop"),
            )
        ],
        heartbeat_timeout_seconds=1.0,
    )
    registry.update_presence("robot-1", "2020-01-01T00:00:00+00:00")
    gateway = _gateway(identity=_identity(), registry=registry)
    gateway.start()
    try:
        state = _browser_state(gateway, tmp_path)
    finally:
        gateway.stop()

    assert state["mode"] == "SIMULATION"
    assert state["chassis"] == "STALE"
    assert state["safety"] == "BLOCKED / UNKNOWN"
