"""External approval relay boundary.

Converts relay-ready pending approval records into delivery notifications
for console, webhook, or future operator UI.

Fail-safe design: relay failures never block the approval decision path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delivery record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayDeliveryRecord:
    """Immutable record of a relay delivery attempt."""

    request_id: str
    channel: str
    operator_id: str
    delivered_at: str  # ISO timestamp
    success: bool
    error: str | None
    dedup_key: str  # "{request_id}:{channel}" for idempotency


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ApprovalRelay(Protocol):
    """Protocol for approval relay adapters.

    Implementations deliver pending approval notifications to external
    channels (console, webhook, operator UI).  Must be idempotent by
    dedup_key (request_id + channel).
    """

    def deliver_pending_approval(
        self,
        *,
        request_id: str,
        mission_id: str,
        action: str,
        risk_level: str,
        channel: str,
        operator_id: str,
    ) -> RelayDeliveryRecord:
        """Deliver a pending approval notification.

        Returns a RelayDeliveryRecord indicating success or failure.
        Must be idempotent: calling with the same request_id + channel
        returns the same record without duplicate delivery.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryApprovalRelay:
    """In-memory relay for testing and single-process deployments.

    Stores delivery records keyed by dedup_key (request_id:channel).
    Idempotent: repeated calls with the same key return the existing record.
    """

    def __init__(self) -> None:
        self._deliveries: dict[str, RelayDeliveryRecord] = {}

    @property
    def deliveries(self) -> list[RelayDeliveryRecord]:
        """Return all delivery records."""
        return list(self._deliveries.values())

    def get_delivery(self, dedup_key: str) -> RelayDeliveryRecord | None:
        """Look up a delivery record by dedup key."""
        return self._deliveries.get(dedup_key)

    def deliver_pending_approval(
        self,
        *,
        request_id: str,
        mission_id: str,
        action: str,
        risk_level: str,
        channel: str,
        operator_id: str,
    ) -> RelayDeliveryRecord:
        """Deliver a pending approval notification (idempotent)."""
        dedup_key = f"{request_id}:{channel}"

        # Idempotent: return existing record if already delivered
        existing = self._deliveries.get(dedup_key)
        if existing is not None:
            return existing

        now = datetime.now(timezone.utc).isoformat()
        record = RelayDeliveryRecord(
            request_id=request_id,
            channel=channel,
            operator_id=operator_id,
            delivered_at=now,
            success=True,
            error=None,
            dedup_key=dedup_key,
        )
        self._deliveries[dedup_key] = record
        logger.info(
            "Approval relay delivered: request_id=%s channel=%s operator_id=%s",
            request_id, channel, operator_id,
        )
        return record
