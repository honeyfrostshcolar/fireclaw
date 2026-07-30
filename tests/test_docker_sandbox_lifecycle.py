from __future__ import annotations

from pathlib import Path
import sys
import time

import pytest

from fireclaw_core.agent.docker_sandbox import (
    DockerLifecycleError,
    DockerSandboxRuntime,
    run_bounded_process,
)


_IMAGE_ID = "sha256:" + ("a" * 64)


def _fake_docker(tmp_path: Path, *, remove_exit_code: int = 0) -> Path:
    state_root = tmp_path / "containers"
    state_root.mkdir()
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        f"state_root = pathlib.Path({str(state_root)!r})\n"
        f"image_id = {_IMAGE_ID!r}\n"
        f"remove_exit_code = {remove_exit_code}\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['image', 'inspect']:\n"
        "    repo_digests = ['registry/fireclaw@sha256:abc']\n"
        "    print(json.dumps(image_id) + '\\t' + json.dumps(repo_digests))\n"
        "elif args[0] == 'create':\n"
        "    name = args[args.index('--name') + 1]\n"
        "    (state_root / name).write_text('running', encoding='utf-8')\n"
        "    print(name)\n"
        "elif args[0] == 'start':\n"
        "    print('x' * 200000, flush=True)\n"
        "    time.sleep(60)\n"
        "elif args[0] == 'rm':\n"
        "    if remove_exit_code:\n"
        "        print('daemon refused cleanup', file=sys.stderr)\n"
        "        raise SystemExit(remove_exit_code)\n"
        "    target = state_root / args[-1]\n"
        "    if target.exists():\n"
        "        target.unlink()\n"
        "    print(args[-1])\n"
        "else:\n"
        "    raise SystemExit(2)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_bounded_process_discards_output_after_byte_limit() -> None:
    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "os.write(1, b'x' * 200000); "
                "os.write(2, b'y' * 150000)"
            ),
        ],
        timeout_seconds=5,
        max_output_bytes=1024,
    )

    assert result.exit_code == 0
    assert result.stdout_bytes_observed == 200000
    assert result.stderr_bytes_observed == 150000
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert len(result.stdout.encode()) < 1200
    assert len(result.stderr.encode()) < 1200


def test_timeout_forces_named_container_removal(tmp_path: Path) -> None:
    executable = _fake_docker(tmp_path)
    runtime = DockerSandboxRuntime(docker_executable=str(executable))
    container_name = runtime.new_container_name()

    result = runtime.execute_container(
        create_command=[
            str(executable),
            "create",
            "--name",
            container_name,
            _IMAGE_ID,
            "sleep",
            "60",
        ],
        container_name=container_name,
        stdin_text=None,
        timeout_seconds=0.05,
        max_output_bytes=1024,
        cancellation_requested=lambda: False,
    )

    assert result.timed_out is True
    assert result.stdout_truncated is True
    assert not (tmp_path / "containers" / container_name).exists()


def test_cancellation_forces_named_container_removal(tmp_path: Path) -> None:
    executable = _fake_docker(tmp_path)
    runtime = DockerSandboxRuntime(docker_executable=str(executable))
    container_name = runtime.new_container_name()
    started = time.monotonic()

    result = runtime.execute_container(
        create_command=[
            str(executable),
            "create",
            "--name",
            container_name,
            _IMAGE_ID,
            "sleep",
            "60",
        ],
        container_name=container_name,
        stdin_text=None,
        timeout_seconds=5,
        max_output_bytes=1024,
        cancellation_requested=lambda: time.monotonic() - started > 0.05,
    )

    assert result.cancelled is True
    assert not (tmp_path / "containers" / container_name).exists()


def test_cleanup_failure_is_not_reported_as_success(tmp_path: Path) -> None:
    executable = _fake_docker(tmp_path, remove_exit_code=7)
    runtime = DockerSandboxRuntime(docker_executable=str(executable))
    container_name = runtime.new_container_name()

    with pytest.raises(DockerLifecycleError, match="Could not prove cleanup"):
        runtime.execute_container(
            create_command=[
                str(executable),
                "create",
                "--name",
                container_name,
                _IMAGE_ID,
                "true",
            ],
            container_name=container_name,
            stdin_text=None,
            timeout_seconds=0.05,
            max_output_bytes=1024,
            cancellation_requested=lambda: False,
        )


def test_image_reference_must_resolve_to_configured_digest(
    tmp_path: Path,
) -> None:
    executable = _fake_docker(tmp_path)
    runtime = DockerSandboxRuntime(docker_executable=str(executable))

    identity = runtime.verify_image(
        configured_reference="fireclaw-agent-sim:test",
        expected_image_id=_IMAGE_ID,
        max_output_bytes=1024,
    )
    assert identity.image_id == _IMAGE_ID
    assert identity.repository_digests == (
        "registry/fireclaw@sha256:abc",
    )

    with pytest.raises(DockerLifecycleError, match="digest mismatch"):
        runtime.verify_image(
            configured_reference="fireclaw-agent-sim:test",
            expected_image_id="sha256:" + ("b" * 64),
            max_output_bytes=1024,
        )
