"""First-run setup and active-Profile preferences for novice operators."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, TextIO
from uuid import uuid4

from fireclaw_core.infra import tomllib_compat as tomllib

from fireclaw_core.agent.robot_profile import load_robot_capability_profile
from fireclaw_core.deployment import (
    apply_deployment,
    build_deployment_plan,
    load_runtime_deployment_profile,
)
from fireclaw_core.infra.path_security import discover_fireclaw_project_root
from fireclaw_core.infra.runtime_paths import resolve_fireclaw_runtime_root


SETUP_SCHEMA_VERSION = 1
SIMULATION_TEMPLATE_ID = "gazebo-turtlebot3-burger-v1"
_ACTIVE_PROFILE_NAME = "active-profile.json"
_SIMULATION_PROFILE_NAME = "gazebo-turtlebot3-burger.toml"


class FireClawSetupError(ValueError):
    """A setup failure with a stable user-facing recovery action."""

    def __init__(self, message: str, *, code: str, operator_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.operator_action = operator_action


@dataclass(frozen=True)
class ActiveProfile:
    profile_path: Path
    mode: str
    template_id: str
    updated_at: str
    state_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SETUP_SCHEMA_VERSION,
            "profile_path": str(self.profile_path),
            "mode": self.mode,
            "template_id": self.template_id,
            "updated_at": self.updated_at,
        }


def setup_fireclaw(
    *,
    mode: str,
    profile_path: str | Path | None = None,
    runtime_root: str | Path | None = None,
    source_root: str | Path | None = None,
    deploy: bool = True,
    plan_builder: Callable[..., Any] = build_deployment_plan,
    deployment_applier: Callable[..., Mapping[str, Any]] = apply_deployment,
) -> dict[str, Any]:
    """Prepare one Profile without starting a Gateway or robot action."""

    if mode not in {"simulation", "real"}:
        raise FireClawSetupError(
            "setup mode must be simulation or real",
            code="setup_mode_invalid",
            operator_action="重新运行 fireclaw setup 并选择仿真或实机模式。",
        )
    if mode == "real" and profile_path is None:
        raise FireClawSetupError(
            "Real-robot setup requires an existing reviewed Profile.",
            code="real_profile_required",
            operator_action=(
                "先准备并人工审查 real Robot Profile，再使用 "
                "fireclaw setup --mode real --profile <path>。"
            ),
        )
    if mode == "real" and deploy:
        raise FireClawSetupError(
            "First-run setup never deploys or starts a real robot automatically.",
            code="real_setup_auto_deploy_forbidden",
            operator_action="移除自动部署请求，只执行 real Profile 非致动预检。",
        )

    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    _ensure_private_directory(root)
    profiles_dir = _private_child_directory(root, "profiles")
    workspace_root = _private_child_directory(root, "workspaces")
    state_dir = _private_child_directory(root, "state")

    generated = profile_path is None
    if generated:
        project_root = _resolve_simulation_source_root(source_root)
        resolved_profile = profiles_dir / _SIMULATION_PROFILE_NAME
        profile_created = _ensure_generated_simulation_profile(
            resolved_profile,
            runtime_root=root,
            workspace_root=workspace_root / "gazebo-turtlebot3-burger",
            source_root=project_root,
        )
    else:
        resolved_profile = _regular_profile_path(profile_path)
        profile_created = False

    if mode == "real" and _is_generated_simulation_profile(resolved_profile):
        raise FireClawSetupError(
            "The built-in simulation Profile cannot be converted into a real Profile.",
            code="simulation_profile_for_real_forbidden",
            operator_action=(
                "使用厂商资料创建独立 real Profile，并完成 hardware-safety 人工审查。"
            ),
        )

    robot_profile = load_robot_capability_profile(resolved_profile)
    deployment_profile = load_runtime_deployment_profile(resolved_profile)
    if deployment_profile.mode != mode:
        raise FireClawSetupError(
            "Profile mode does not match the selected setup mode.",
            code="setup_profile_mode_mismatch",
            operator_action=(
                f"选择 {deployment_profile.mode} 模式，或使用对应模式的 Profile。"
            ),
        )
    if mode == "simulation" and robot_profile.adapter != "ros1":
        raise FireClawSetupError(
            "The built-in simulation setup requires the ROS1 adapter.",
            code="simulation_adapter_invalid",
            operator_action="使用 FireClaw 提供的 TurtleBot3 ROS1 仿真模板。",
        )

    plan = plan_builder(resolved_profile)
    deployment_result: dict[str, Any]
    if deploy:
        deployment_result = dict(deployment_applier(plan))
        deployment_status = str(deployment_result.get("status") or "unknown")
        if deployment_status != "installed":
            raise FireClawSetupError(
                "Simulation deployment did not reach the installed state.",
                code="setup_deployment_incomplete",
                operator_action="修复部署错误后重新运行 fireclaw setup；已有配置会安全续接。",
            )
        status = "ready_to_start"
    else:
        deployment_result = {
            "status": "planned",
            "fingerprint": getattr(plan, "fingerprint", None),
        }
        status = "configured"

    template_id = SIMULATION_TEMPLATE_ID if generated else "external-profile"
    active = write_active_profile(
        resolved_profile,
        mode=mode,
        template_id=template_id,
        runtime_root=root,
        state_dir=state_dir,
    )
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "kind": "fireclaw_setup",
        "status": status,
        "mode": mode,
        "profile_path": str(resolved_profile),
        "profile_created": profile_created,
        "active_profile_state": str(active.state_path),
        "deployment": deployment_result,
        "robot_id": robot_profile.robot_id,
        "robot_action_started": False,
        "safe_state": "simulation_only" if mode == "simulation" else "real_not_started",
        "message": (
            "仿真配置与运行文件已准备完成。"
            if status == "ready_to_start"
            else "配置已验证并设为当前 Profile。"
        ),
        "next_command": (
            "fireclaw deploy run"
            if status == "ready_to_start"
            else "fireclaw deploy apply"
            if mode == "simulation"
            else "fireclaw status"
        ),
    }


def write_active_profile(
    profile_path: str | Path,
    *,
    mode: str,
    template_id: str,
    runtime_root: str | Path | None = None,
    state_dir: Path | None = None,
) -> ActiveProfile:
    """Atomically replace the non-secret active-Profile preference."""

    profile = _regular_profile_path(profile_path)
    deployment = load_runtime_deployment_profile(profile)
    if deployment.mode != mode:
        raise FireClawSetupError(
            "Active Profile mode does not match its deployment mode.",
            code="active_profile_mode_mismatch",
            operator_action="重新运行 fireclaw setup 并选择与 Profile 一致的模式。",
        )
    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    _ensure_private_directory(root)
    if state_dir is None:
        resolved_state_dir = _private_child_directory(root, "state")
    else:
        resolved_state_dir = state_dir.resolve(strict=False)
        try:
            resolved_state_dir.relative_to(root)
        except ValueError as exc:
            raise FireClawSetupError(
                "Active Profile state escapes the FireClaw runtime root.",
                code="active_profile_state_unsafe",
                operator_action="使用 FireClaw runtime root 内的标准 state 目录。",
            ) from exc
        _ensure_private_directory(resolved_state_dir)
    state_path = _safe_child(resolved_state_dir, _ACTIVE_PROFILE_NAME)
    if state_path.is_symlink():
        raise FireClawSetupError(
            "Active Profile state must not be a symbolic link.",
            code="active_profile_state_unsafe",
            operator_action="移除该符号链接后重新运行 fireclaw setup。",
        )
    updated_at = datetime.now(timezone.utc).isoformat()
    active = ActiveProfile(
        profile_path=profile,
        mode=mode,
        template_id=template_id,
        updated_at=updated_at,
        state_path=state_path,
    )
    _atomic_replace_json(state_path, active.to_dict())
    return active


def load_active_profile(
    *,
    runtime_root: str | Path | None = None,
) -> ActiveProfile:
    root = resolve_fireclaw_runtime_root(configured=runtime_root)
    state_dir = _safe_child(root, "state")
    if state_dir.is_symlink():
        raise FireClawSetupError(
            "Active Profile state directory must not be a symbolic link.",
            code="active_profile_state_unsafe",
            operator_action="移除该符号链接后重新运行 fireclaw setup。",
        )
    state_path = _safe_child(state_dir, _ACTIVE_PROFILE_NAME)
    if state_path.is_symlink() or not state_path.is_file():
        raise FireClawSetupError(
            "No active FireClaw Profile is configured.",
            code="active_profile_missing",
            operator_action="先运行 fireclaw setup。",
        )
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FireClawSetupError(
            "Active Profile state is unreadable or invalid.",
            code="active_profile_state_invalid",
            operator_action="运行 fireclaw setup 重新验证并写入当前 Profile。",
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SETUP_SCHEMA_VERSION:
        raise FireClawSetupError(
            "Active Profile state uses an unsupported schema.",
            code="active_profile_state_invalid",
            operator_action="运行 fireclaw setup 重新验证并写入当前 Profile。",
        )
    profile_value = raw.get("profile_path")
    mode = raw.get("mode")
    template_id = raw.get("template_id")
    updated_at = raw.get("updated_at")
    if (
        not isinstance(profile_value, str)
        or not profile_value
        or mode not in {"simulation", "real"}
        or not isinstance(template_id, str)
        or not template_id
        or not isinstance(updated_at, str)
        or not updated_at
    ):
        raise FireClawSetupError(
            "Active Profile state is incomplete.",
            code="active_profile_state_invalid",
            operator_action="运行 fireclaw setup 重新验证并写入当前 Profile。",
        )
    profile = _regular_profile_path(profile_value)
    deployment = load_runtime_deployment_profile(profile)
    if deployment.mode != mode:
        raise FireClawSetupError(
            "Active Profile no longer matches its recorded mode.",
            code="active_profile_stale",
            operator_action="运行 fireclaw setup 重新验证当前 Profile。",
        )
    return ActiveProfile(
        profile_path=profile,
        mode=mode,
        template_id=template_id,
        updated_at=updated_at,
        state_path=state_path,
    )


def resolve_active_profile_path(
    explicit: str | Path | None,
    *,
    runtime_root: str | Path | None = None,
) -> Path:
    if explicit is not None:
        # Explicit CLI arguments retain their existing command-owned validation
        # boundary. Only the persisted active preference is trusted here.
        return Path(explicit).expanduser()
    return load_active_profile(runtime_root=runtime_root).profile_path


def handle_setup(
    args: argparse.Namespace,
    *,
    out: TextIO = sys.stdout,
    input_fn: Callable[[str], str] = input,
    stdin_is_tty: bool | None = None,
) -> int:
    try:
        mode = _resolve_setup_mode(
            args,
            input_fn=input_fn,
            stdin_is_tty=stdin_is_tty,
        )
        interactive = (
            sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
        ) and not args.json
        profile_path = args.profile
        if mode == "real" and profile_path is None and interactive:
            entered = input_fn(
                "请输入已经人工审查的 real Robot Profile 路径（回车取消）："
            ).strip()
            profile_path = Path(entered).expanduser() if entered else None
        result = setup_fireclaw(
            mode=mode,
            profile_path=profile_path,
            runtime_root=args.runtime_root,
            source_root=args.source_root,
            deploy=(not args.no_deploy and mode == "simulation"),
        )
    except (OSError, ValueError) as exc:
        result = {
            "schema_version": SETUP_SCHEMA_VERSION,
            "kind": "fireclaw_setup",
            "status": "error",
            "code": getattr(exc, "code", "setup_failed"),
            "message": str(exc) or type(exc).__name__,
            "operator_action": getattr(
                exc,
                "operator_action",
                "根据错误修复依赖或 Profile 后重新运行 fireclaw setup。",
            ),
            "robot_action_started": False,
            "safe_state": "no_robot_action_started",
        }
        _write_setup_result(result, json_output=args.json, out=out)
        return 2
    _write_setup_result(result, json_output=args.json, out=out)
    return 0


def _resolve_setup_mode(
    args: argparse.Namespace,
    *,
    input_fn: Callable[[str], str],
    stdin_is_tty: bool | None,
) -> str:
    if args.mode is not None:
        return str(args.mode)
    if args.profile is not None:
        return load_runtime_deployment_profile(args.profile).mode
    interactive = (
        sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
    ) and not args.json
    if not interactive:
        return "simulation"
    answer = input_fn(
        "选择使用方式：\n"
        "  1. 仿真体验（推荐，不连接真实机器人）\n"
        "  2. 连接真实机器人（仅验证已有 Profile）\n"
        "请输入 1 或 2 [1]："
    ).strip()
    if answer in {"", "1"}:
        return "simulation"
    if answer == "2":
        return "real"
    raise FireClawSetupError(
        "Setup selection must be 1 or 2.",
        code="setup_selection_invalid",
        operator_action="重新运行 fireclaw setup 并输入 1 或 2。",
    )


def _write_setup_result(
    result: Mapping[str, Any],
    *,
    json_output: bool,
    out: TextIO,
) -> None:
    if json_output:
        print(json.dumps(dict(result), ensure_ascii=False, indent=2), file=out)
        return
    if result.get("status") == "error":
        print("FireClaw 设置未完成", file=out)
        print(f"\n发生了什么：{result.get('message')}", file=out)
        print("机器人状态：没有启动任何机器人动作。", file=out)
        print("FireClaw 已采取：保留现有配置，不绕过校验。", file=out)
        print(f"下一步：{result.get('operator_action')}", file=out)
        return
    print("FireClaw 设置完成", file=out)
    print(f"\n✓ 当前模式：{result.get('mode')}", file=out)
    print(f"✓ 当前 Profile：{result.get('profile_path')}", file=out)
    print("✓ 没有启动任何机器人动作", file=out)
    print(f"\n下一步：{result.get('next_command')}", file=out)


def _resolve_simulation_source_root(value: str | Path | None) -> Path:
    candidates: list[Path] = []
    if value is not None:
        candidates.append(Path(value).expanduser().resolve(strict=False))
    else:
        cwd_root = discover_fireclaw_project_root(Path.cwd())
        module_root = discover_fireclaw_project_root(Path(__file__))
        if cwd_root is not None:
            candidates.append(cwd_root)
        if module_root is not None and module_root not in candidates:
            candidates.append(module_root)
    markers = (
        Path("extensions/navigation-move-base/fireclaw.plugin.json"),
        Path(
            "robots/turtlebot3_burger/ros_ws/src/"
            "fireclaw_turtlebot3_burger/launch/robot_base.launch"
        ),
        Path(
            "robots/turtlebot3_burger/ros_ws/src/turtlebot3/"
            "turtlebot3_navigation/maps/map.yaml"
        ),
    )
    for candidate in candidates:
        if candidate.is_dir() and all((candidate / marker).is_file() for marker in markers):
            return candidate
    raise FireClawSetupError(
        "FireClaw simulation assets were not found in the source installation.",
        code="simulation_assets_missing",
        operator_action=(
            "在完整 FireClaw 仓库中运行 setup，或使用 --source-root 指向仓库根目录。"
        ),
    )


def _ensure_generated_simulation_profile(
    profile_path: Path,
    *,
    runtime_root: Path,
    workspace_root: Path,
    source_root: Path,
) -> bool:
    if profile_path.is_symlink():
        raise FireClawSetupError(
            "Generated Profile path must not be a symbolic link.",
            code="setup_profile_unsafe",
            operator_action="移除该符号链接后重新运行 fireclaw setup。",
        )
    if profile_path.exists():
        if not profile_path.is_file():
            raise FireClawSetupError(
                "Generated Profile path is not a regular file.",
                code="setup_profile_unsafe",
                operator_action="移动冲突路径后重新运行 fireclaw setup。",
            )
        return False

    _ensure_private_directory(workspace_root)
    for relative in (
        "data/mission",
        "data/robots/gazebo_turtlebot3",
        "agent-workspaces/mission",
        "agent-workspaces/robot",
    ):
        _private_nested_directory(workspace_root, relative)

    from fireclaw_core.resources import load_setup_template

    try:
        template = load_setup_template("gazebo_turtlebot3")
    except FileNotFoundError:
        # Fallback to source template if package resources are not built in editable dev
        template_path = source_root / "examples/setup_templates/gazebo_turtlebot3.toml"
        if template_path.is_symlink() or not template_path.is_file():
            raise FireClawSetupError(
                "Simulation Profile template is missing or unsafe.",
                code="simulation_template_invalid",
                operator_action="恢复 FireClaw 官方仿真模板后重试。",
            )
        template = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{RUNTIME_ROOT}}": runtime_root,
        "{{PROFILE_PATH}}": profile_path,
        "{{SOURCE_ROOT}}": source_root,
        "{{WORKSPACE_ROOT}}": workspace_root,
        "{{ROS_SETUP}}": Path("/opt/ros/noetic/setup.bash"),
        "{{ROBOT_WS_SETUP}}": (
            source_root / "robots/turtlebot3_burger/ros_ws/devel/setup.bash"
        ),
        "{{ROS1_CONFIG}}": (
            source_root / "examples/ros1_configs/gazebo_turtlebot3_move_base.yaml"
        ),
        "{{ROBOT_DATA_DIR}}": workspace_root / "data/robots/gazebo_turtlebot3",
        "{{MISSION_DATA_DIR}}": workspace_root / "data/mission",
        "{{DEPLOYMENT_OUTPUT_ROOT}}": runtime_root / "deployments",
        "{{ROBOT_LAUNCH}}": (
            source_root
            / "robots/turtlebot3_burger/ros_ws/src/"
            "fireclaw_turtlebot3_burger/launch/robot_base.launch"
        ),
        "{{MAP_FILE}}": (
            source_root
            / "robots/turtlebot3_burger/ros_ws/src/turtlebot3/"
            "turtlebot3_navigation/maps/map.yaml"
        ),
        "{{PLUGIN_ROOT}}": source_root / "extensions",
        "{{MISSION_WORKSPACE}}": workspace_root / "agent-workspaces/mission",
        "{{ROBOT_WORKSPACE}}": workspace_root / "agent-workspaces/robot",
    }
    rendered = template
    for placeholder, path in replacements.items():
        rendered = rendered.replace(placeholder, json.dumps(str(path)))
    if "{{" in rendered or "}}" in rendered:
        raise FireClawSetupError(
            "Simulation Profile template contains unresolved placeholders.",
            code="simulation_template_invalid",
            operator_action="恢复 FireClaw 官方仿真模板后重试。",
        )
    return _atomic_create_text(profile_path, rendered)


def _regular_profile_path(value: str | Path | None) -> Path:
    if value is None:
        raise FireClawSetupError(
            "Profile path is required.",
            code="profile_required",
            operator_action="先运行 fireclaw setup，或显式提供 --profile。",
        )
    authored = Path(value).expanduser()
    if authored.is_symlink():
        raise FireClawSetupError(
            "Profile must be a regular non-symlink TOML file.",
            code="profile_unsafe",
            operator_action="使用普通 TOML 文件，不要使用符号链接。",
        )
    try:
        resolved = authored.resolve(strict=True)
    except OSError as exc:
        raise FireClawSetupError(
            "Profile file does not exist.",
            code="profile_missing",
            operator_action="先运行 fireclaw setup，或检查 --profile 路径。",
        ) from exc
    if not resolved.is_file():
        raise FireClawSetupError(
            "Profile must be a regular TOML file.",
            code="profile_unsafe",
            operator_action="选择一个普通 TOML Profile 文件。",
        )
    return resolved


def _is_generated_simulation_profile(profile_path: Path) -> bool:
    try:
        with profile_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    fireclaw = raw.get("fireclaw")
    setup = fireclaw.get("setup") if isinstance(fireclaw, dict) else None
    return (
        isinstance(setup, dict)
        and setup.get("template_id") == SIMULATION_TEMPLATE_ID
    )


def _safe_child(root: Path, name: str) -> Path:
    canonical_root = root.resolve(strict=False)
    authored = canonical_root / name
    if authored.is_symlink():
        raise FireClawSetupError(
            "Setup path must not use a symbolic link.",
            code="setup_path_unsafe",
            operator_action="移除该符号链接后重新运行 fireclaw setup。",
        )
    target = authored.resolve(strict=False)
    try:
        target.relative_to(canonical_root)
    except ValueError as exc:
        raise FireClawSetupError(
            "Setup path escapes the FireClaw runtime root.",
            code="setup_path_unsafe",
            operator_action="使用 FireClaw runtime root 内的标准路径。",
        ) from exc
    return target


def _ensure_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise FireClawSetupError(
            "Setup directory must not be a symbolic link.",
            code="setup_path_unsafe",
            operator_action="移除该符号链接后重新运行 fireclaw setup。",
        )
    created = not path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise FireClawSetupError(
            "Setup path is not a directory.",
            code="setup_path_unsafe",
            operator_action="移动冲突文件后重新运行 fireclaw setup。",
        )
    if created:
        path.chmod(0o700)


def _private_child_directory(root: Path, name: str) -> Path:
    path = _safe_child(root, name)
    _ensure_private_directory(path)
    return path


def _private_nested_directory(root: Path, relative: str) -> Path:
    current = root
    for part in Path(relative).parts:
        current = _private_child_directory(current, part)
    return current


def _atomic_create_text(path: Path, content: str) -> bool:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            handle.write(content)
            if not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        path.chmod(0o600)
        return True
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_replace_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            temporary.chmod(0o600)
            json.dump(dict(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "ActiveProfile",
    "FireClawSetupError",
    "SIMULATION_TEMPLATE_ID",
    "handle_setup",
    "load_active_profile",
    "resolve_active_profile_path",
    "setup_fireclaw",
    "write_active_profile",
]
