from __future__ import annotations

import sys

from fireclaw_core.mission.mission_cli import main as _mission_main

KNOWN_SUBCOMMANDS = {
    "submit-subtask", "trace", "cancel", "plan-mission", "events",
    "corrections", "memory", "replay", "approval", "lifecycle-check",
    "security-audit", "serve", "mission", "robot-gateway", "robot-profile",
}


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return _mission_main()
    # --demo is an agent_cli flag, not a mission_cli subcommand
    if "--demo" in args:
        from fireclaw_core.agent.agent_cli import main as _agent_main
        return _agent_main()
    if args[0].lstrip("-") in KNOWN_SUBCOMMANDS or args[0].startswith("-"):
        return _mission_main()
    from fireclaw_core.agent.agent_cli import main as _agent_main
    return _agent_main()


if __name__ == "__main__":
    raise SystemExit(main())
