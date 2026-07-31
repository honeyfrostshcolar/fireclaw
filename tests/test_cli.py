import json
import os
import subprocess
import sys

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)


def _legacy_sandbox_args(tmp_path):
    return [
        "--legacy-skill-sandbox-image",
        "fireclaw-test-sandbox:latest",
        "--legacy-skill-sandbox-image-digest",
        _TEST_IMAGE_ID,
        "--legacy-skill-sandbox-root",
        str(tmp_path / "legacy-sandbox"),
    ]


def _fake_docker_env(tmp_path):
    bin_dir = tmp_path / "fake-docker-bin"
    bin_dir.mkdir(exist_ok=True)
    state_dir = tmp_path / "fake-docker-state"
    state_dir.mkdir(exist_ok=True)
    executable = bin_dir / "docker"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"state_root = pathlib.Path({str(state_dir)!r})\n"
        f"image_id = {_TEST_IMAGE_ID!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    print(json.dumps(image_id) + '\\t' + json.dumps([]))\n"
        "elif args[0] == 'create':\n"
        "    name = args[args.index('--name') + 1]\n"
        "    (state_root / name).write_text(json.dumps(args), encoding='utf-8')\n"
        "    print(name)\n"
        "elif args[0] == 'start':\n"
        "    name = args[-1]\n"
        "    created = json.loads((state_root / name).read_text(encoding='utf-8'))\n"
        "    mount = created[created.index('--mount') + 1]\n"
        "    source = next(part[4:] for part in mount.split(',') if part.startswith('src='))\n"
        "    workdir_index = created.index('--workdir')\n"
        "    container_cwd = pathlib.PurePosixPath(created[workdir_index + 1])\n"
        "    command = created[workdir_index + 3:]\n"
        "    relative = container_cwd.relative_to('/workspace')\n"
        "    os.chdir(pathlib.Path(source) / pathlib.Path(*relative.parts))\n"
        "    os.execvpe(command[0], command, os.environ)\n"
        "elif args[0] == 'rm':\n"
        "    name = args[-1]\n"
        "    path = state_root / name\n"
        "    if path.exists():\n"
        "        path.unlink()\n"
        "    print(name)\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def test_module_cli_runs_rescue_command_and_writes_memory(tmp_path):
    memory_path = tmp_path / "cli-memory.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--memory-path",
            str(memory_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["planning"]["target_floor"] is None
    assert result["planning"]["target_pose"] == {
        "frame_id": "map",
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
    }
    assert memory_path.exists()


def test_module_cli_accepts_robot_id(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--robot-id",
            "robot-cli",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["output"]["robot_id"] == "robot-cli"


def test_module_cli_accepts_simulator_adapter(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--adapter",
            "simulator",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--robot-id",
            "sim-cli",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["robot_state"]["mode"] == "simulator"
    assert result["execution"]["steps"][0]["output"]["mode"] == "simulator"
    assert result["execution"]["steps"][0]["output"]["x"] == 2.0
    assert result["execution"]["steps"][0]["output"]["y"] == 1.5
    assert result["execution"]["steps"][0]["output"]["frame_id"] == "map"


def test_module_cli_accepts_mock_ros1_adapter(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--adapter",
            "mock-ros1",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--robot-id",
            "ros1-cli",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["robot_state"]["mode"] == "mock_ros1"
    assert result["execution"]["steps"][0]["output"]["mode"] == "mock_ros1"
    assert result["execution"]["steps"][0]["output"]["ros1_interface"] == "topic"
    assert result["execution"]["steps"][0]["output"]["ros1_name"] == "/fireclaw/ros1-cli/navigation"


def test_module_cli_accepts_ros1_config_for_adapter_skeleton(tmp_path):
    config_path = tmp_path / "ros1.json"
    config_path.write_text(
        json.dumps(
            {
                "robot_id": "real-ros1-cli",
                "endpoints": {
                    "navigate_to_point": {
                        "interface": "action",
                        "name": "/fireclaw/real-ros1-cli/navigation",
                        "type": "move_base_msgs/MoveBaseAction",
                        "cancel_supported": True,
                        "feedback_supported": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--adapter",
            "ros1",
            "--ros1-config",
            str(config_path),
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--robot-id",
            "ignored-cli",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["status"] == "block"
    assert result["robot_state"]["mode"] == "ros1"
    assert result["robot_state"]["robot_id"] == "real-ros1-cli"
    assert result["execution"] is None


def test_module_cli_runs_rescue_demo_through_gateway_mock_ros1(tmp_path):
    task_queue_path = tmp_path / "demo-tasks.jsonl"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "--demo",
            "rescue",
            "--memory-path",
            str(tmp_path / "demo-memory.jsonl"),
            "--event-path",
            str(tmp_path / "demo-events.jsonl"),
            "--task-queue-path",
            str(task_queue_path),
            "--robot-id",
            "demo-cli",
            "--session-id",
            "demo-cli-session",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "completed"
    assert result["session_id"] == "demo-cli-session"
    assert result["operator"]["operator_id"] == "local-operator"
    assert result["control"]["status"] == "allow"
    assert result["robot_state"]["mode"] == "mock_ros1"
    assert result["result"]["execution"]["steps"][0]["output"]["ros1_name"] == "/fireclaw/demo-cli/navigation"
    assert "operator.identified" in result["event_types"]
    assert "control.decision" in result["event_types"]
    assert "action.succeeded" in result["event_types"]
    assert result["state"]["task"]["status"] == "completed"
    assert result["state"]["task"]["action_count"] == 5
    assert task_queue_path.exists()


def test_module_cli_accepts_session_id(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--session-id",
            "cli-session",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["session"]["session_id"] == "cli-session"
    assert result["session"]["turn_index"] == 1


def test_module_cli_treats_recall_as_successful_command(tmp_path):
    memory_path = tmp_path / "cli-memory.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--memory-path",
            str(memory_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "之前做过什么",
            "--memory-path",
            str(memory_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "recalled"
    assert len(result["memory"]["records"]) == 1


def test_module_cli_retrieves_memory_records_for_same_session(tmp_path):
    memory_path = tmp_path / "cli-memory.jsonl"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去一楼救人",
            "--session-id",
            "cli-retrieval",
            "--memory-path",
            str(memory_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "之前一楼救人成功了吗",
            "--session-id",
            "cli-retrieval",
            "--memory-path",
            str(memory_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "retrieved"
    assert result["memory"]["records"][0]["command"] == "去一楼救人"
    assert result["memory"]["query"]["target_floor"] == 1


def test_module_cli_confirmation_words_do_not_authorize_across_processes(tmp_path):
    memory_path = tmp_path / "cli-memory.jsonl"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "smoke_entry.skill.json").write_text(
        json.dumps(
            {
                "name": "smoke_entry",
                "description": "High-risk dry-run skill.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'confirmed': True}}))",
                ],
                "dry_run_only": True,
                "risk_level": "high",
            }
        ),
        encoding="utf-8",
    )

    pending = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "运行 smoke_entry",
            "--session-id",
            "cli-confirm",
            "--memory-path",
            str(memory_path),
            "--skills-dir",
            str(skills_dir),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    pending_result = json.loads(pending.stdout)
    assert pending_result["status"] == "awaiting_confirmation"
    assert pending_result["execution"] is None

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "确认执行",
            "--session-id",
            "cli-confirm",
            "--memory-path",
            str(memory_path),
            "--skills-dir",
            str(skills_dir),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "awaiting_confirmation"
    assert result["confirmation"]["status"] == "pending"
    assert result["execution"] is None


def test_module_cli_treats_skill_listing_as_successful_command(tmp_path):
    memory_path = tmp_path / "cli-memory.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "你有哪些技能",
            "--memory-path",
            str(memory_path),
            "--no-workspace-skills",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "skills"
    assert len(result["skills"]) == 6
    assert "navigate_to_point" in [skill["name"] for skill in result["skills"]]
    assert not memory_path.exists()


def test_module_cli_loads_workspace_skills_with_explicit_sandbox(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "workspace.skill.json").write_text(
        json.dumps(
            {
                "name": "workspace_cli_policy",
                "description": "Workspace CLI policy.",
                "runtime": "subprocess",
                "command": [sys.executable, "-c", "print('{\"ok\": true, \"data\": {}}')"],
                "timeout_seconds": 2,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "你有哪些技能",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--skills-dir",
            str(skills_dir),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert "workspace_cli_policy" in [skill["name"] for skill in result["skills"]]
    assert result["skill_load_errors"] == []


def test_module_cli_workspace_manifest_fails_closed_without_sandbox(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "workspace.skill.json").write_text(
        json.dumps(
            {
                "name": "workspace_cli_policy",
                "description": "Workspace CLI policy.",
                "runtime": "subprocess",
                "command": ["python3", "-c", "print('unsafe')"],
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "你有哪些技能",
            "--skills-dir",
            str(skills_dir),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert "workspace_cli_policy" not in [
        skill["name"] for skill in result["skills"]
    ]
    assert "explicit deployment profile" in (
        result["skill_load_errors"][0]["message"]
    )


def test_module_cli_blocks_workspace_skill_when_available_sensor_is_missing(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "gas_detector.skill.json").write_text(
        json.dumps(
            {
                "name": "gas_policy",
                "description": "Requires gas_detector.",
                "runtime": "subprocess",
                "command": [sys.executable, "-c", "print('{\"ok\": true, \"data\": {}}')"],
                "dry_run_only": True,
                "required_sensors": ["gas_detector"],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "运行 gas_policy",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--skills-dir",
            str(skills_dir),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["status"] == "block"
    assert "gas_detector" in result["message"]


def test_module_cli_accepts_available_sensor_for_workspace_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "thermal.skill.json").write_text(
        json.dumps(
            {
                "name": "thermal_policy",
                "description": "Requires thermal camera.",
                "runtime": "subprocess",
                "command": [
                    sys.executable,
                    "-c",
                    "import json; print(json.dumps({'ok': True, 'data': {'sensor_ok': True}}))",
                ],
                "dry_run_only": True,
                "required_sensors": ["thermal_camera"],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "运行 thermal_policy",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--skills-dir",
            str(skills_dir),
            "--available-sensor",
            "thermal_camera",
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
        env=_fake_docker_env(tmp_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["output"]["sensor_ok"] is True


def test_module_cli_can_disable_workspace_skill_loading(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "workspace.skill.json").write_text(
        json.dumps(
            {
                "name": "disabled_workspace_policy",
                "description": "Disabled workspace policy.",
                "runtime": "subprocess",
                "command": [sys.executable, "-c", "print('{\"ok\": true, \"data\": {}}')"],
                "timeout_seconds": 2,
                "dry_run_only": True,
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "你有哪些技能",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--skills-dir",
            str(skills_dir),
            "--no-workspace-skills",
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
    )

    result = json.loads(completed.stdout)
    assert "disabled_workspace_policy" not in [skill["name"] for skill in result["skills"]]


def test_module_cli_directly_invokes_workspace_skill(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "运行 echo_policy 处理 二楼",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
        env=_fake_docker_env(tmp_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert result["execution"]["steps"][0]["skill_name"] == "echo_policy"
    assert result["execution"]["steps"][0]["output"]["received"] == {"text": "二楼"}


def test_module_cli_direct_invocation_of_missing_skill_exits_nonzero(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "运行 missing_skill",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["status"] == "block"
    assert "Missing skill: missing_skill" in result["message"]


def test_module_cli_rescue_plan_runs_workspace_policy_first(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人 使用 echo_policy",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            *_legacy_sandbox_args(tmp_path),
        ],
        check=True,
        cwd=".",
        text=True,
        capture_output=True,
        env=_fake_docker_env(tmp_path),
    )

    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert [step["skill_name"] for step in result["execution"]["steps"]] == [
        "echo_policy",
        "navigate_to_point",
        "search_for_victims",
        "assess_victim",
        "report_status",
        "return_to_safe_zone",
    ]
    assert result["execution"]["steps"][0]["output"]["received"]["target"] == {
        "frame_id": "map",
        "x": 2.0,
        "y": 1.5,
        "yaw": 0.0,
    }


def test_module_cli_rescue_plan_missing_policy_exits_nonzero(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人 使用 missing_policy",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["status"] == "block"
    assert "Missing skill: missing_policy" in result["message"]


def test_module_cli_real_run_blocks_default_rescue_skills(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "fireclaw_core",
            "去坐标 (2.0, 1.5) 救人",
            "--memory-path",
            str(tmp_path / "memory.jsonl"),
            "--real-run",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["status"] == "block"
    assert result["dry_run"] is False
    assert "Skill is not allowed for real robot execution: navigate_to_point" in result["message"]
