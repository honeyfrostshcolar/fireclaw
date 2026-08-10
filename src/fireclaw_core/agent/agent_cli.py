from __future__ import annotations

import argparse
import json
from pathlib import Path

from fireclaw_core.agent.agent import FireClawAgent
from fireclaw_core.agent.computer_tools import ComputerSandbox
from fireclaw_core.devtools.demo import run_rescue_demo
from fireclaw_core.memory.memory import JsonlMemoryStore
from fireclaw_core.execution.runtime_config import ADAPTER_CHOICES, create_robot_adapter
from fireclaw_core.policy.deployment import DeploymentProfile, SandboxProfile


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FireClaw dry-run agent.")
    parser.add_argument(
        "command",
        nargs="?",
        help="Operator command, for example: 去坐标 (2.0, 1.5) 救人",
    )
    parser.add_argument(
        "--demo",
        choices=("rescue",),
        help="Run a local end-to-end demo through the Gateway and mock ROS1 adapter.",
    )
    parser.add_argument(
        "--memory-path",
        default="memory/fireclaw-runs.jsonl",
        help="Path to the JSONL memory file.",
    )
    parser.add_argument(
        "--event-path",
        default="memory/fireclaw-demo-events.jsonl",
        help="Path to the JSONL event ledger used by --demo rescue.",
    )
    parser.add_argument(
        "--task-queue-path",
        default="memory/fireclaw-demo-tasks.jsonl",
        help="Path to the JSONL task queue used by --demo rescue.",
    )
    parser.add_argument(
        "--computer-sandbox-image",
        default=None,
        help="Docker image used by the computer-tools Plugin.",
    )
    parser.add_argument(
        "--computer-sandbox-image-digest",
        default=None,
        help=(
            "Immutable Docker image ID (sha256:...) that must match "
            "--computer-sandbox-image before execution."
        ),
    )
    parser.add_argument(
        "--computer-sandbox-root",
        default="data/fireclaw-sandbox/agent-cli",
        help="Host workspace mounted into the computer-tools sandbox.",
    )
    parser.add_argument(
        "--robot-id",
        default="fireclaw-dry-run",
        help="Robot identifier used in dry-run execution output.",
    )
    parser.add_argument(
        "--adapter",
        choices=ADAPTER_CHOICES,
        default="dry-run",
        help="Robot adapter to use. Real ROS1 is not imported by this CLI.",
    )
    parser.add_argument(
        "--ros1-config",
        default=None,
        help="Path to a ROS1 adapter JSON config. Required when --adapter ros1 is used.",
    )
    parser.add_argument(
        "--session-id",
        default="default",
        help="Conversation/session identifier for memory and context.",
    )
    parser.add_argument(
        "--available-sensor",
        action="append",
        default=[],
        help="Declare an available sensor. Can be used more than once.",
    )
    parser.add_argument(
        "--real-run",
        action="store_true",
        help="Evaluate safety as non-dry-run. This does not create a real robot adapter.",
    )
    args = parser.parse_args()
    if args.demo == "rescue":
        result = run_rescue_demo(
            command=args.command or "去坐标 (2.0, 1.5) 救人",
            memory_path=args.memory_path,
            event_path=args.event_path,
            task_queue_path=args.task_queue_path,
            robot_id=args.robot_id,
            session_id=args.session_id,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "completed" else 1

    if args.command is None:
        parser.error("command is required unless --demo rescue is used.")

    robot = create_robot_adapter(args.adapter, args.robot_id, config_path=args.ros1_config)

    available_sensors = set(args.available_sensor) if args.available_sensor else None
    if args.adapter == "ros1" and available_sensors is None:
        available_sensors = set()
    deployment_profile = None
    computer_sandbox = None
    if args.computer_sandbox_image:
        if not args.computer_sandbox_image_digest:
            parser.error(
                "--computer-sandbox-image requires "
                "--computer-sandbox-image-digest"
            )
        deployment_profile = DeploymentProfile(
            mode="real" if args.real_run else "simulation",
            role="robot_agent",
            sandbox=SandboxProfile(
                enabled=True,
                image=args.computer_sandbox_image,
                image_digest=args.computer_sandbox_image_digest,
                network="none",
                workspace_root=Path(args.computer_sandbox_root),
            ),
        )
        computer_sandbox = ComputerSandbox(
            deployment_profile.sandbox
        )

    agent = FireClawAgent(
        robot=robot,
        memory=JsonlMemoryStore(args.memory_path),
        dry_run=not args.real_run,
        available_sensors=available_sensors,
        session_id=args.session_id,
        deployment_profile=deployment_profile,
        extension_paths=("extensions",),
        plugin_services={
            "adapter": args.adapter,
            "fireclaw.agent-tools.computer.sandbox": computer_sandbox,
        },
    )
    result = agent.run(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    successful_statuses = {
        "succeeded",
        "clarify",
        "recalled",
        "retrieved",
        "skills",
        "awaiting_confirmation",
        "cancelled",
    }
    return 0 if result["status"] in successful_statuses else 1


if __name__ == "__main__":
    raise SystemExit(main())
