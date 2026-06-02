from __future__ import annotations

import argparse
import json

from fireclaw_core.agent import FireClawAgent
from fireclaw_core.memory import JsonlMemoryStore
from fireclaw_core.runtime_config import ADAPTER_CHOICES, create_robot_adapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FireClaw dry-run agent.")
    parser.add_argument("command", help="Operator command, for example: 去二楼救人")
    parser.add_argument(
        "--memory-path",
        default="memory/fireclaw-runs.jsonl",
        help="Path to the JSONL memory file.",
    )
    parser.add_argument(
        "--skills-dir",
        default="skills",
        help="Directory containing workspace *.skill.json manifests.",
    )
    parser.add_argument(
        "--no-workspace-skills",
        action="store_true",
        help="Disable loading workspace skills from --skills-dir.",
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
        help="Robot adapter to use. Real ROS2 is not imported by this CLI.",
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
    robot = create_robot_adapter(args.adapter, args.robot_id)

    agent = FireClawAgent(
        robot=robot,
        memory=JsonlMemoryStore(args.memory_path),
        workspace_skills_dir=None if args.no_workspace_skills else args.skills_dir,
        dry_run=not args.real_run,
        available_sensors=set(args.available_sensor),
        session_id=args.session_id,
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
