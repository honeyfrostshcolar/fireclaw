"""Host-neutral contracts for trusted physical stop evidence providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


STOP_EVIDENCE_SERVICE_PREFIX = "fireclaw.safety.stop-evidence."
RUNTIME_STATIONARITY_EVIDENCE_CLASS = "runtime_stationarity_v1"
HARDWARE_STOP_EVIDENCE_CLASS = "hardware_stop_v1"


class RuntimeStopEvidenceProvider(Protocol):
    """Trusted Plugin service used to prove that a runtime is stationary."""

    provider_id: str
    evidence_class: str
    hardware_owned: bool

    def collect_stop_evidence(
        self,
        *,
        robot_id: str,
        reason: str | None = None,
    ) -> Mapping[str, Any]:
        ...


def stop_evidence_service_id(provider_id: str) -> str:
    normalized = provider_id.strip() if isinstance(provider_id, str) else ""
    if not normalized:
        raise ValueError("stop evidence provider_id must not be empty")
    return f"{STOP_EVIDENCE_SERVICE_PREFIX}{normalized}"
