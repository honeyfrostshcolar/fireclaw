"""Operational bounds for explicitly trusted in-process plugin callbacks."""

from __future__ import annotations

import json
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable


class TrustedCallbackTimeout(TimeoutError):
    """A trusted callback did not return within its host-enforced deadline."""


def invoke_trusted_callback(
    callback: Callable[[dict[str, Any]], Any],
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
) -> Any:
    """Bound a trusted callback's effect on its caller's control loop.

    This is not a security sandbox. The Plugin Host rejects untrusted
    executable contributions; sandboxed plugin code must run out of process.
    """

    outcomes: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            outcomes.put((True, callback(payload)))
        except BaseException as exc:
            outcomes.put((False, exc))

    thread = Thread(
        target=invoke,
        name="fireclaw-trusted-plugin-callback",
        daemon=True,
    )
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TrustedCallbackTimeout("Trusted plugin callback timed out.")
    try:
        succeeded, value = outcomes.get_nowait()
    except Empty as exc:
        raise RuntimeError(
            "Trusted plugin callback exited without a result."
        ) from exc
    if not succeeded:
        raise value
    return value


def validate_json_result_size(
    value: Any,
    *,
    max_bytes: int,
    description: str,
) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{description} must be JSON-serializable.") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{description} exceeds {max_bytes} bytes.")
