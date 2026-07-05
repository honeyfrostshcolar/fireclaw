import json
import sys
import time

from fireclaw_core.execution.runtime import SubprocessSkillRunner
from fireclaw_core.execution.skills import create_subprocess_skill


def test_subprocess_skill_runner_passes_json_inputs_and_reads_json_result():
    runner = SubprocessSkillRunner(
        command=[
            sys.executable,
            "-c",
            (
                "import json, sys; "
                "payload = json.load(sys.stdin); "
                "print(json.dumps({'ok': True, 'data': {'echo': payload['value']}}))"
            ),
        ]
    )

    result = runner.run({"value": "hello"})

    assert result.ok is True
    assert result.data == {"echo": "hello"}
    assert result.error is None


def test_subprocess_skill_runner_reports_nonzero_exit():
    runner = SubprocessSkillRunner(
        command=[
            sys.executable,
            "-c",
            "import sys; print('boom', file=sys.stderr); raise SystemExit(7)",
        ]
    )

    result = runner.run({})

    assert result.ok is False
    assert result.data == {"returncode": 7}
    assert "code 7" in result.error
    assert "boom" in result.error


def test_subprocess_skill_runner_reports_invalid_json():
    runner = SubprocessSkillRunner(command=[sys.executable, "-c", "print('not-json')"])

    result = runner.run({})

    assert result.ok is False
    assert "invalid JSON" in result.error


def test_subprocess_skill_runner_reports_timeout():
    runner = SubprocessSkillRunner(
        command=[sys.executable, "-c", "import time; time.sleep(2)"],
        timeout_seconds=0.05,
    )

    result = runner.run({})

    assert result.ok is False
    assert "timed out" in result.error


def test_subprocess_skill_runner_terminates_process_when_cancelled():
    requested = {"cancel": False}
    runner = SubprocessSkillRunner(
        command=[
            sys.executable,
            "-c",
            "import time; time.sleep(1); print('should-not-finish')",
        ],
        timeout_seconds=5,
    )
    started = time.monotonic()

    def cancellation_requested():
        if time.monotonic() - started > 0.05:
            requested["cancel"] = True
        return requested["cancel"]

    result = runner.run({}, cancellation_requested=cancellation_requested)

    assert result.ok is False
    assert result.status == "cancelled"
    assert result.mode == "subprocess"
    assert "cancelled" in result.error
    assert time.monotonic() - started < 0.8


def test_create_subprocess_skill_sets_runtime_metadata():
    skill = create_subprocess_skill(
        name="rl_navigation",
        description="Runs an isolated RL navigation skill.",
        command=[sys.executable, "-c", "print(%r)" % json.dumps({"ok": True, "data": {}})],
    )

    assert skill.runtime == "subprocess"
    assert skill.dry_run_only is True
    assert skill.run({}).ok is True
