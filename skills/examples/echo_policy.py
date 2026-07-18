from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.load(sys.stdin)
    print(
        json.dumps(
            {
                "ok": True,
                "data": {
                    "skill": "echo_policy",
                    "received": payload,
                    "dry_run": True,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
