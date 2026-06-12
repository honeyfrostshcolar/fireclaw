"""Phase 2 tests: richer skill metadata and typed output contracts."""
from __future__ import annotations

from fireclaw_core.agent.robot import DryRunRobotAdapter
from fireclaw_core.execution.skills import (
    ASSESS_OUTPUT_SCHEMA,
    NAVIGATE_OUTPUT_SCHEMA,
    REPORT_OUTPUT_SCHEMA,
    SEARCH_OUTPUT_SCHEMA,
    Skill,
    SkillRegistry,
    create_default_skill_registry,
    create_subprocess_skill,
)


def test_skill_has_output_schema_field() -> None:
    skill = Skill(
        name="test",
        description="test skill",
        handler=lambda inputs: None,
        output_schema=NAVIGATE_OUTPUT_SCHEMA,
    )
    assert skill.output_schema == NAVIGATE_OUTPUT_SCHEMA


def test_skill_has_domain_field() -> None:
    skill = Skill(
        name="test",
        description="test skill",
        handler=lambda inputs: None,
        domain="perception",
    )
    assert skill.domain == "perception"


def test_skill_has_preconditions_field() -> None:
    skill = Skill(
        name="test",
        description="test skill",
        handler=lambda inputs: None,
        preconditions=["robot_online", "camera_available"],
    )
    assert "robot_online" in skill.preconditions
    assert "camera_available" in skill.preconditions


def test_skill_has_degraded_mode_policy_field() -> None:
    skill = Skill(
        name="test",
        description="test skill",
        handler=lambda inputs: None,
        degraded_mode_policy="retry",
    )
    assert skill.degraded_mode_policy == "retry"


def test_skill_defaults() -> None:
    skill = Skill(name="t", description="d", handler=lambda inputs: None)
    assert skill.output_schema == {"type": "object", "additionalProperties": True}
    assert skill.domain == "navigation"
    assert skill.preconditions == []
    assert skill.degraded_mode_policy is None


def test_default_registry_skills_have_typed_metadata() -> None:
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)

    nav = registry.get("navigate_to_floor")
    assert nav is not None
    assert nav.output_schema == NAVIGATE_OUTPUT_SCHEMA
    assert nav.domain == "navigation"
    assert "robot_online" in nav.preconditions
    assert nav.degraded_mode_policy == "retry"
    assert nav.idempotent is True
    assert nav.allow_real_robot is True

    search = registry.get("search_for_victims")
    assert search is not None
    assert search.output_schema == SEARCH_OUTPUT_SCHEMA
    assert search.domain == "perception"
    assert "camera_available" in search.preconditions

    assess = registry.get("assess_victim")
    assert assess is not None
    assert assess.output_schema == ASSESS_OUTPUT_SCHEMA
    assert assess.domain == "perception"
    assert assess.degraded_mode_policy == "skip"

    report = registry.get("report_status")
    assert report is not None
    assert report.output_schema == REPORT_OUTPUT_SCHEMA
    assert report.domain == "communication"

    ret = registry.get("return_to_safe_zone")
    assert ret is not None
    assert ret.domain == "safety"
    assert ret.degraded_mode_policy == "abort"
    assert ret.risk_level == "medium"


def test_list_metadata_includes_new_fields() -> None:
    robot = DryRunRobotAdapter(robot_id="r1")
    registry = create_default_skill_registry(robot)
    metadata = registry.list_metadata()

    nav_meta = next(m for m in metadata if m["name"] == "navigate_to_floor")
    assert "output_schema" in nav_meta
    assert "domain" in nav_meta
    assert "preconditions" in nav_meta
    assert "degraded_mode_policy" in nav_meta
    assert nav_meta["domain"] == "navigation"


def test_create_subprocess_skill_with_new_fields() -> None:
    skill = create_subprocess_skill(
        name="ext_scan",
        description="External scan",
        command=["echo", "ok"],
        output_schema=SEARCH_OUTPUT_SCHEMA,
        domain="perception",
        preconditions=["robot_online"],
        degraded_mode_policy="fallback",
    )
    assert skill.output_schema == SEARCH_OUTPUT_SCHEMA
    assert skill.domain == "perception"
    assert skill.preconditions == ["robot_online"]
    assert skill.degraded_mode_policy == "fallback"
