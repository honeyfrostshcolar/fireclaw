from pathlib import Path

import pytest

from fireclaw_core.policy.deployment import (
    DEPLOYMENT_POLICY_ID,
    DeploymentProfile,
    DeploymentToolDecision,
    DeploymentToolStageDecision,
    SandboxProfile,
    deployment_profile_from_config,
    validate_robot_deployment_binding,
)

_TEST_IMAGE_ID = "sha256:" + ("a" * 64)


def _sandbox(tmp_path: Path, *, enabled: bool = True) -> SandboxProfile:
    return SandboxProfile(
        enabled=enabled,
        workspace_root=tmp_path / "sandbox",
        image="fireclaw-sim:test",
        image_digest=_TEST_IMAGE_ID,
    )


def test_simulation_profile_allows_sandboxed_computer_effects(
    tmp_path: Path,
) -> None:
    profile = DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=_sandbox(tmp_path),
    )

    assert profile.evaluate(
        tool_name="computer_read_file",
        effect="read",
        requires_sandbox=True,
    ).status == "allow"
    assert profile.evaluate(
        tool_name="computer_write_file",
        effect="bounded_mutation",
        requires_sandbox=True,
    ).status == "allow"
    assert profile.evaluate(
        tool_name="computer_exec",
        effect="process",
        requires_sandbox=True,
    ).status == "allow"


def test_block_reason_takes_precedence_over_approval_stage() -> None:
    decision = DeploymentToolDecision(
        policy_id=DEPLOYMENT_POLICY_ID,
        profile_id="mission_agent.real",
        mode="real",
        role="mission_agent",
        tool_name="computer_exec",
        effect="process",
        status="block",
        stages=(
            DeploymentToolStageDecision(
                stage="hook",
                status="require_approval",
                reason_code="hook_approval_required",
                message="Approval requested.",
            ),
            DeploymentToolStageDecision(
                stage="effect_policy",
                status="block",
                reason_code="tool_effect_forbidden",
                message="Process tools are forbidden in real mode.",
            ),
        ),
    )

    assert decision.blocking_stage is decision.stages[1]
    assert decision.reason_code == "tool_effect_forbidden"


def test_simulation_profile_never_allows_real_hardware_effect(
    tmp_path: Path,
) -> None:
    profile = DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=_sandbox(tmp_path),
        allow=("*",),
    )

    decision = profile.evaluate(
        tool_name="raw_motor_driver",
        effect="real_hardware",
        requires_sandbox=False,
    )

    assert decision.status == "block"
    assert decision.reason_code == "tool_effect_forbidden"


def test_real_profile_requires_approval_for_mutation_and_blocks_process(
    tmp_path: Path,
) -> None:
    profile = DeploymentProfile(
        mode="real",
        role="mission_agent",
        sandbox=_sandbox(tmp_path),
    )

    assert profile.evaluate(
        tool_name="computer_write_file",
        effect="bounded_mutation",
        requires_sandbox=True,
    ).status == "require_approval"
    assert profile.evaluate(
        tool_name="computer_exec",
        effect="process",
        requires_sandbox=True,
    ).status == "block"


def test_deployment_config_uses_empty_selector_lists_by_default(
    tmp_path: Path,
) -> None:
    profile = deployment_profile_from_config(
        {
            "mode": "simulation",
            "sandbox": {
                "robot_agent": {
                    "enabled": True,
                    "image": "fireclaw-sim:test",
                    "image_digest": _TEST_IMAGE_ID,
                }
            },
        },
        role="robot_agent",
        default_workspace_root=tmp_path / "default",
    )

    assert profile.mode == "simulation"
    assert profile.allow is None
    assert profile.also_allow == ()
    assert profile.deny == ()


def test_enabled_process_sandbox_requires_immutable_image_digest(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="require image_digest"):
        SandboxProfile(
            enabled=True,
            workspace_root=tmp_path / "sandbox",
            image="fireclaw-sim:mutable",
        )

    with pytest.raises(ValueError, match="sha256 image ID"):
        SandboxProfile(
            enabled=True,
            workspace_root=tmp_path / "sandbox",
            image="fireclaw-sim:test",
            image_digest="sha256:not-a-digest",
        )


def test_sandbox_output_configuration_has_hard_memory_limit(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="max_output_bytes exceeds"):
        SandboxProfile(
            workspace_root=tmp_path / "sandbox",
            max_output_bytes=(16 * 1024 * 1024) + 1,
        )


def test_generic_computer_sandbox_rejects_docker_bridge_network(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="bridge access is prohibited"):
        SandboxProfile(
            workspace_root=tmp_path / "sandbox",
            network="bridge",
        )


def test_sandbox_workspace_quotas_have_hard_upper_limits(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="max_workspace_bytes exceeds"):
        SandboxProfile(
            workspace_root=tmp_path / "sandbox",
            max_workspace_bytes=(1024 * 1024 * 1024) + 1,
        )
    with pytest.raises(ValueError, match="max_concurrent_processes exceeds"):
        SandboxProfile(
            workspace_root=tmp_path / "sandbox",
            max_concurrent_processes=9,
        )


def test_simulation_policy_cannot_bind_live_real_adapter(
    tmp_path: Path,
) -> None:
    profile = DeploymentProfile(
        mode="simulation",
        role="robot_agent",
        sandbox=_sandbox(tmp_path),
    )

    with pytest.raises(ValueError, match="embodied_runtime_mode='simulation'"):
        validate_robot_deployment_binding(
            profile,
            dry_run=False,
            embodied_runtime_mode="real",
        )

    validate_robot_deployment_binding(
        profile,
        dry_run=False,
        embodied_runtime_mode="simulation",
    )
