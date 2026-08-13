from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
from urllib import request
from urllib.error import HTTPError

import pytest

from fireclaw_core.deployment.supervisor import (
    RuntimeSupervisor,
    RuntimeSupervisorSettings,
)
from fireclaw_core.gateway.gateway import FireClawGateway, GatewayConfig
from fireclaw_core.infra.runtime_state import SqliteAuthoritativeRuntimeStore
from fireclaw_core.mission.interactive import _submit_and_follow
from fireclaw_core.mission.mission_gateway_client import MissionGatewayClient


def _gateway(
    tmp_path: Path,
    *,
    busy_timeout_seconds: float = 0.05,
    max_page_count: int | None = None,
) -> FireClawGateway:
    state_path = tmp_path / "runtime.sqlite3"
    store = SqliteAuthoritativeRuntimeStore(
        state_path,
        busy_timeout_seconds=busy_timeout_seconds,
        max_page_count=max_page_count,
    )
    return FireClawGateway(
        GatewayConfig(
            host="127.0.0.1",
            port=0,
            adapter="dry-run",
            robot_id="fault-test-robot",
            memory_path=str(tmp_path / "memory.jsonl"),
            event_path=str(tmp_path / "events.jsonl"),
            task_queue_path=str(tmp_path / "tasks.jsonl"),
            runtime_state_path=str(state_path),
        ),
        runtime_state=store,
    )


def test_database_lock_rejects_then_recovers_with_same_dedupe_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_start_task_worker",
        lambda control, operator, **kwargs: started.append(control.task_id),
    )
    lock = sqlite3.connect(gateway.runtime_state.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        rejected = gateway.submit_agent(
            "去坐标 (2.0, 1.5)",
            dedupe_key="locked-retry-1",
        )
    finally:
        lock.rollback()
        lock.close()

    assert rejected["status"] == "unavailable"
    assert rejected["code"] == "runtime_database_locked"
    assert rejected["task_execution_started"] is False
    assert started == []
    assert gateway.task_queue.list_records() == []
    assert gateway.runtime_storage_state()["task_admission_allowed"] is False

    accepted = gateway.submit_agent(
        "去坐标 (2.0, 1.5)",
        dedupe_key="locked-retry-1",
    )

    assert accepted["status"] == "accepted"
    assert started == [accepted["task_id"]]
    assert [record.task_id for record in gateway.task_queue.list_records()] == [
        accepted["task_id"]
    ]
    assert gateway.runtime_storage_state() == {
        "status": "healthy",
        "task_admission_allowed": True,
    }


def test_database_lock_http_returns_503_without_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(tmp_path)
    started: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_start_task_worker",
        lambda control, operator, **kwargs: started.append(control.task_id),
    )
    gateway.start()
    lock = sqlite3.connect(gateway.runtime_state.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        body = json.dumps(
            {
                "command": "去坐标 (2.0, 1.5)",
                "dedupe_key": "http-locked-retry-1",
            }
        ).encode("utf-8")
        http_request = request.Request(
            f"{gateway.base_url}/tasks",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as captured:
            request.urlopen(http_request, timeout=2)
        payload = json.loads(
            captured.value.read().decode("utf-8")
        )
    finally:
        lock.rollback()
        lock.close()
        gateway.stop()

    assert captured.value.code == 503
    assert payload["status"] == "unavailable"
    assert payload["code"] == "runtime_database_locked"
    assert payload["task_execution_started"] is False
    assert started == []
    assert gateway.task_queue.list_records() == []


def test_runtime_storage_full_rejects_task_without_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(tmp_path, max_page_count=64)
    started: list[str] = []
    monkeypatch.setattr(
        gateway,
        "_start_task_worker",
        lambda control, operator, **kwargs: started.append(control.task_id),
    )
    result = gateway.submit_agent(
        "运行 unavailable_payload " + ("x" * 1_000_000),
        dedupe_key="disk-full-1",
    )

    assert result["status"] == "unavailable"
    assert result["code"] == "runtime_storage_full"
    assert result["task_execution_started"] is False
    assert started == []
    assert gateway.task_queue.list_records() == []
    storage = gateway.runtime_storage_state()
    assert storage["status"] == "degraded"
    assert storage["task_admission_allowed"] is False


def test_concurrent_duplicate_command_starts_one_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway(tmp_path)
    started: list[str] = []
    start_lock = threading.Lock()

    def capture_worker(control, operator, **kwargs) -> None:
        with start_lock:
            started.append(control.task_id)

    monkeypatch.setattr(gateway, "_start_task_worker", capture_worker)
    barrier = threading.Barrier(8)

    def submit() -> dict:
        barrier.wait(timeout=3)
        return gateway.submit_agent(
            "去坐标 (2.0, 1.5)",
            dedupe_key="concurrent-command-1",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: submit(), range(8)))

    accepted = [item for item in results if item["status"] == "accepted"]
    duplicates = [item for item in results if item["status"] == "duplicate"]
    assert len(accepted) == 1
    assert len(duplicates) == 7
    task_id = accepted[0]["task_id"]
    assert {item["task_id"] for item in results} == {task_id}
    assert started == [task_id]
    assert [record.task_id for record in gateway.task_queue.list_records()] == [
        task_id
    ]


def test_network_disconnect_resumes_actual_sse_session(capsys) -> None:
    cursors: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._json(
                202,
                {"status": "planned", "mission_id": "mission-network-1"},
            )

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.endswith("/trace"):
                self._json(200, {"status": "running"})
                return
            if not parsed.path.endswith("/events/stream"):
                self._json(404, {"status": "not_found"})
                return
            cursor = int(parse_qs(parsed.query).get("after_sequence", [0])[0])
            cursors.append(cursor)
            sequence = 1 if cursor == 0 else 2
            event_type = (
                "mission.planning" if sequence == 1 else "mission.completed"
            )
            payload = json.dumps(
                {
                    "event_id": f"event-{sequence}",
                    "event_type": event_type,
                    "mission_id": "mission-network-1",
                    "sequence": sequence,
                    "payload": {},
                }
            )
            body = (
                f"event: {event_type}\nid: {sequence}\ndata: {payload}\n\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        client = MissionGatewayClient(f"http://{host}:{port}", timeout=2)
        _submit_and_follow(
            client,
            "去二楼搜索",
            sleep_fn=lambda _: None,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.01,
            max_reconnect_attempts=3,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    output = capsys.readouterr()
    assert cursors == [0, 1]
    assert output.out.count("正在规划任务") == 1
    assert "[完成] 已完成（succeeded）" in output.out
    assert "自动重连" in output.err


class _StatusProbe:
    def __init__(self, release: Path) -> None:
        self.release = release
        self.runtime_calls = 0

    def __call__(self, profile_path, *, output_root, check_runtime):
        if not check_runtime:
            return {"status": "installed", "release_dir": str(self.release)}
        self.runtime_calls += 1
        return {
            "status": (
                "installed_not_ready" if self.runtime_calls == 1 else "ready"
            ),
            "release_dir": str(self.release),
        }


def test_supervisor_restarts_actual_crashed_gateway_process(
    tmp_path: Path,
) -> None:
    setup = tmp_path / "setup.bash"
    setup.write_text("export ROS_DISTRO=noetic\n", encoding="utf-8")
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (tmp_path / "robot-data").mkdir()
    output_root = tmp_path / "deployments"
    profile = tmp_path / "robot.toml"
    profile.write_text(
        f"""
[robot]
id = "fault-test-robot"
base_url = "http://127.0.0.1:8765"
adapter = "ros1"
ros1_config = "ros1.yaml"
data_dir = "{tmp_path / 'robot-data'}"
capabilities = ["navigation"]
enabled_skills = ["navigate_to_point"]
llm_exposed_skills = ["navigate_to_point"]

[deployment]
id = "fault-test-robot"
mode = "simulation"
output_root = "{output_root}"

[deployment.ros1]
distro = "noetic"
setup_files = ["{setup}"]

[plugins]
paths = ["{plugin}"]
selected = ["example.runtime"]
""".strip(),
        encoding="utf-8",
    )
    release = output_root / "fault-test-robot" / "releases" / "fault-release"
    bin_dir = release / "bin"
    bin_dir.mkdir(parents=True)
    counter = tmp_path / "gateway-start-count"
    bringup = bin_dir / "fireclaw-bringup"
    bringup.write_text(
        f"#!{sys.executable}\nimport time\nwhile True: time.sleep(1)\n",
        encoding="utf-8",
    )
    gateway_script = bin_dir / "fireclaw-gateway"
    gateway_script.write_text(
        f"""#!{sys.executable}
from pathlib import Path
import time
path = Path({str(counter)!r})
count = int(path.read_text()) if path.exists() else 0
path.write_text(str(count + 1))
if count == 0:
    time.sleep(0.15)
    raise SystemExit(17)
while True:
    time.sleep(1)
""",
        encoding="utf-8",
    )
    bringup.chmod(0o755)
    gateway_script.chmod(0o755)

    settings = RuntimeSupervisorSettings(
        runtime_ready_timeout_seconds=2,
        gateway_ready_timeout_seconds=2,
        mission_gateway_ready_timeout_seconds=2,
        readiness_monitor_interval_seconds=0.02,
        probe_interval_seconds=0.02,
        monitor_interval_seconds=0.02,
        shutdown_timeout_seconds=1,
        terminate_timeout_seconds=0.5,
        simulation_restart_limit=1,
        restart_backoff_seconds=0.01,
        readiness_failure_limit=2,
    )
    supervisor: RuntimeSupervisor

    def health() -> dict:
        if counter.exists() and int(counter.read_text()) >= 2:
            supervisor.request_stop("fault_test_complete")
        return {
            "status": "ok",
            "robot_id": "fault-test-robot",
            "dry_run": True,
        }

    supervisor = RuntimeSupervisor(
        profile,
        settings=settings,
        status_probe=_StatusProbe(release),
        health_probe=health,
    )
    result = supervisor.run()

    assert result["status"] == "stopped"
    assert result["reason_code"] == "fault_test_complete"
    assert result["generation_count"] == 2
    assert counter.read_text() == "2"
    lifecycle = [
        json.loads(line)
        for line in (Path(result["log_dir"]) / "lifecycle.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        item["event_type"] == "process.exited"
        and item.get("service") == "gateway"
        and item.get("exit_code") == 17
        for item in lifecycle
    )
    assert any(
        item["event_type"] == "supervisor.restart_scheduled"
        for item in lifecycle
    )


def test_private_roscore_restart_recovers_graph(tmp_path: Path) -> None:
    if os.environ.get("FIRECLAW_RUN_LIVE_ROS_FAULT_TEST") != "1":
        pytest.skip("live private roscore fault test requires explicit opt-in")
    roscore = shutil.which("roscore")
    rosnode = shutil.which("rosnode")
    if roscore is None or rosnode is None:
        pytest.fail(
            "--live-ros requires installed roscore and rosnode executables"
        )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    environment = dict(os.environ)
    environment["ROS_MASTER_URI"] = f"http://127.0.0.1:{port}"
    environment["ROS_IP"] = "127.0.0.1"
    environment.pop("ROS_HOSTNAME", None)
    environment["ROS_HOME"] = str(tmp_path / "ros-home")
    environment["ROS_LOG_DIR"] = str(tmp_path / "ros-logs")
    ros_python_path = (
        Path(roscore).resolve().parent.parent
        / "lib"
        / "python3"
        / "dist-packages"
    )
    inherited_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (
            str(ros_python_path),
            inherited_python_path,
        )
        if item
    )
    Path(environment["ROS_HOME"]).mkdir()
    Path(environment["ROS_LOG_DIR"]).mkdir()

    def start_master() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [roscore, "-p", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )

    def graph_available() -> bool:
        try:
            result = subprocess.run(
                [rosnode, "list"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=2,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def wait_for(
        expected: bool,
        *,
        process: subprocess.Popen[bytes] | None = None,
        timeout: float = 15,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if graph_available() is expected:
                return
            if expected and process is not None and process.poll() is not None:
                output = (
                    process.stdout.read().decode("utf-8", errors="replace")
                    if process.stdout is not None
                    else ""
                )
                raise AssertionError(
                    "private roscore exited before becoming ready: "
                    f"code={process.returncode}, output={output[-4000:]}"
                )
            time.sleep(0.1)
        raise AssertionError(
            f"ROS graph availability did not become {expected} within {timeout}s"
        )

    def stop_master(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        if process.stdout is not None:
            process.stdout.close()

    first = start_master()
    second: subprocess.Popen[bytes] | None = None
    try:
        wait_for(True, process=first)
        stop_master(first)
        wait_for(False)
        second = start_master()
        wait_for(True, process=second)
    finally:
        stop_master(first)
        if second is not None:
            stop_master(second)
