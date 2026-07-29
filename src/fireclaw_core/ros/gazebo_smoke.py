from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_robot_registry_payload(*, robot_id: str, base_url: str) -> dict:
    return {
        "robots": [
            {
                "robot_id": robot_id,
                "base_url": base_url.rstrip("/"),
                "capabilities": [
                    "navigate_to_point",
                    "search_for_victims",
                    "report_status",
                    "return_to_safe_zone",
                ],
            }
        ]
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a FireClaw ROS1/Gazebo robot-local agent smoke workflow.")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--robot-gateway-url", required=True)
    parser.add_argument("--mission-gateway-url", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_payload = build_robot_registry_payload(
        robot_id=args.robot_id,
        base_url=args.robot_gateway_url,
    )
    (output_dir / "robots.json").write_text(
        json.dumps(registry_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "robot_id": args.robot_id,
                "robot_gateway_url": args.robot_gateway_url,
                "mission_gateway_url": args.mission_gateway_url,
                "command": args.command,
                "robots_path": str(output_dir / "robots.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
